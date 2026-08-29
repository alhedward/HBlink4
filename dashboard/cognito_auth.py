"""Amazon Cognito authentication helpers for HBlink4 dashboard administrators.

Cognito owns administrator identities and password lifecycle. HBlink4 keeps its
existing short-lived local session/CSRF layer after Cognito has authenticated and
authorized a user.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


class CognitoAuthError(RuntimeError):
    """Base error for Cognito-backed dashboard authentication."""


class CognitoConfigError(CognitoAuthError):
    """Raised when Cognito authentication is selected but not configured."""


class CognitoInvalidCredentials(CognitoAuthError):
    """Raised when Cognito rejects a username/password login."""


class CognitoAuthorizationError(CognitoAuthError):
    """Raised when an authenticated user is not an HBlink4 administrator."""


class CognitoChallengeError(CognitoAuthError):
    """Raised when a first-login challenge is invalid, expired, or unsupported."""


class CognitoPasswordError(CognitoAuthError):
    """Raised for password-policy/reset errors that are safe to show to the user."""


@dataclass(frozen=True)
class CognitoIdentity:
    username: str
    groups: Tuple[str, ...]


@dataclass(frozen=True)
class CognitoLoginResult:
    status: str
    identity: Optional[CognitoIdentity] = None
    challenge_token: Optional[str] = None
    required_attributes: Tuple[str, ...] = ()


@dataclass
class _PendingChallenge:
    username: str
    cognito_session: str
    expires_at: float


class CognitoAdminAuthenticator:
    """Authenticate named dashboard administrators against a Cognito User Pool.

    The app client may have a client secret. When present, the secret is used
    only server-side to calculate Cognito's SECRET_HASH parameter.
    """

    def __init__(self, config: Dict[str, Any], client=None):
        config = config or {}
        self.region = os.environ.get("HBLINK4_COGNITO_REGION", str(config.get("region", ""))).strip()
        self.user_pool_id = os.environ.get(
            "HBLINK4_COGNITO_USER_POOL_ID", str(config.get("user_pool_id", ""))
        ).strip()
        self.client_id = os.environ.get(
            "HBLINK4_COGNITO_CLIENT_ID", str(config.get("client_id", ""))
        ).strip()
        self.client_secret = os.environ.get(
            "HBLINK4_COGNITO_CLIENT_SECRET", str(config.get("client_secret", ""))
        ).strip()
        self.admin_group = str(config.get("admin_group", "HBlink4Admins")).strip() or "HBlink4Admins"
        timeout_minutes = int(config.get("challenge_timeout_minutes", 10))
        if timeout_minutes < 1 or timeout_minutes > 60:
            raise CognitoConfigError("admin.cognito.challenge_timeout_minutes must be 1..60")
        self.challenge_timeout_seconds = timeout_minutes * 60

        missing = [
            name
            for name, value in (
                ("region", self.region),
                ("user_pool_id", self.user_pool_id),
                ("client_id", self.client_id),
            )
            if not value
        ]
        if missing:
            raise CognitoConfigError(
                "Cognito admin authentication is missing: " + ", ".join(missing)
            )

        if client is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover - deployment dependency guard
                raise CognitoConfigError(
                    "Cognito admin authentication requires boto3 (install requirements-dashboard.txt)"
                ) from exc
            client = boto3.client("cognito-idp", region_name=self.region)
        self.client = client
        self._pending: Dict[str, _PendingChallenge] = {}

    @staticmethod
    def _error_code(exc: Exception) -> str:
        response = getattr(exc, "response", None)
        if isinstance(response, dict):
            error = response.get("Error")
            if isinstance(error, dict):
                return str(error.get("Code", ""))
        return exc.__class__.__name__

    @staticmethod
    def _error_message(exc: Exception) -> str:
        response = getattr(exc, "response", None)
        if isinstance(response, dict):
            error = response.get("Error")
            if isinstance(error, dict) and error.get("Message"):
                return str(error["Message"])
        return str(exc)

    def _secret_hash(self, username: str) -> Optional[str]:
        if not self.client_secret:
            return None
        digest = hmac.new(
            self.client_secret.encode("utf-8"),
            (username + self.client_id).encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return base64.b64encode(digest).decode("ascii")

    def _with_secret_hash(self, username: str, values: Dict[str, str]) -> Dict[str, str]:
        result = dict(values)
        secret_hash = self._secret_hash(username)
        if secret_hash:
            result["SECRET_HASH"] = secret_hash
        return result

    def _purge_pending(self) -> None:
        now = time.time()
        for token in [key for key, value in self._pending.items() if value.expires_at <= now]:
            self._pending.pop(token, None)

    @staticmethod
    def _required_attributes(response: Dict[str, Any]) -> Tuple[str, ...]:
        raw = (response.get("ChallengeParameters") or {}).get("requiredAttributes", "[]")
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            return ()
        if not isinstance(parsed, list):
            return ()
        return tuple(str(item) for item in parsed if isinstance(item, str))

    def _authorize_authentication_result(self, authentication_result: Dict[str, Any]) -> CognitoIdentity:
        access_token = authentication_result.get("AccessToken")
        if not isinstance(access_token, str) or not access_token:
            raise CognitoAuthError("Cognito did not return an access token")
        try:
            user = self.client.get_user(AccessToken=access_token)
            username = str(user.get("Username", "")).strip()
            if not username:
                raise CognitoAuthError("Cognito did not return a username")
            group_response = self.client.admin_list_groups_for_user(
                UserPoolId=self.user_pool_id,
                Username=username,
            )
        except CognitoAuthError:
            raise
        except Exception as exc:
            raise CognitoAuthError("Could not verify Cognito administrator membership") from exc

        groups = tuple(
            str(group.get("GroupName"))
            for group in group_response.get("Groups", [])
            if isinstance(group, dict) and group.get("GroupName")
        )
        if self.admin_group not in groups:
            raise CognitoAuthorizationError(
                f"Cognito user is not a member of the required {self.admin_group} group"
            )
        return CognitoIdentity(username=username, groups=groups)

    def authenticate(self, username: str, password: str) -> CognitoLoginResult:
        if not isinstance(username, str) or not username or not isinstance(password, str) or not password:
            raise CognitoInvalidCredentials("Invalid username or password")
        try:
            response = self.client.admin_initiate_auth(
                UserPoolId=self.user_pool_id,
                ClientId=self.client_id,
                AuthFlow="ADMIN_USER_PASSWORD_AUTH",
                AuthParameters=self._with_secret_hash(
                    username,
                    {"USERNAME": username, "PASSWORD": password},
                ),
            )
        except Exception as exc:
            code = self._error_code(exc)
            if code in {"NotAuthorizedException", "UserNotFoundException"}:
                raise CognitoInvalidCredentials("Invalid username or password") from exc
            if code == "PasswordResetRequiredException":
                raise CognitoPasswordError("Password reset required") from exc
            raise CognitoAuthError("Cognito authentication is temporarily unavailable") from exc

        challenge_name = response.get("ChallengeName")
        if challenge_name:
            if challenge_name != "NEW_PASSWORD_REQUIRED":
                raise CognitoChallengeError(
                    f"Cognito challenge {challenge_name} is not supported by this HBlink4 login page"
                )
            cognito_session = response.get("Session")
            if not isinstance(cognito_session, str) or not cognito_session:
                raise CognitoChallengeError("Cognito did not return a first-login challenge session")
            self._purge_pending()
            token = secrets.token_urlsafe(32)
            self._pending[token] = _PendingChallenge(
                username=username,
                cognito_session=cognito_session,
                expires_at=time.time() + self.challenge_timeout_seconds,
            )
            return CognitoLoginResult(
                status="new_password_required",
                challenge_token=token,
                required_attributes=self._required_attributes(response),
            )

        authentication_result = response.get("AuthenticationResult")
        if not isinstance(authentication_result, dict):
            raise CognitoAuthError("Cognito login returned neither a result nor a challenge")
        return CognitoLoginResult(
            status="authenticated",
            identity=self._authorize_authentication_result(authentication_result),
        )

    def complete_new_password(self, challenge_token: str, new_password: str) -> CognitoIdentity:
        self._purge_pending()
        challenge = self._pending.get(challenge_token)
        if challenge is None:
            raise CognitoChallengeError("First-login password challenge is invalid or expired")
        if not isinstance(new_password, str) or not new_password:
            raise CognitoPasswordError("New password must not be empty")

        username = challenge.username
        responses = self._with_secret_hash(
            username,
            {"USERNAME": username, "NEW_PASSWORD": new_password},
        )
        try:
            response = self.client.admin_respond_to_auth_challenge(
                UserPoolId=self.user_pool_id,
                ClientId=self.client_id,
                ChallengeName="NEW_PASSWORD_REQUIRED",
                ChallengeResponses=responses,
                Session=challenge.cognito_session,
            )
        except Exception as exc:
            code = self._error_code(exc)
            if code in {
                "InvalidPasswordException",
                "InvalidParameterException",
                "NotAuthorizedException",
            }:
                raise CognitoPasswordError(self._error_message(exc) or "New password was rejected") from exc
            raise CognitoAuthError("Could not complete Cognito first-login password change") from exc

        next_challenge = response.get("ChallengeName")
        if next_challenge:
            raise CognitoChallengeError(
                f"Cognito challenge {next_challenge} is not supported by this HBlink4 login page"
            )
        authentication_result = response.get("AuthenticationResult")
        if not isinstance(authentication_result, dict):
            raise CognitoAuthError("Cognito did not complete the first-login authentication")

        self._pending.pop(challenge_token, None)
        return self._authorize_authentication_result(authentication_result)

    def start_password_reset(self, username: str) -> Optional[Dict[str, Any]]:
        """Start Cognito's reset-code flow without revealing whether a user exists."""
        if not isinstance(username, str) or not username:
            return None
        kwargs: Dict[str, Any] = {"ClientId": self.client_id, "Username": username}
        secret_hash = self._secret_hash(username)
        if secret_hash:
            kwargs["SecretHash"] = secret_hash
        try:
            response = self.client.forgot_password(**kwargs)
        except Exception as exc:
            code = self._error_code(exc)
            if code in {"UserNotFoundException", "InvalidParameterException"}:
                return None
            if code == "LimitExceededException":
                raise CognitoPasswordError("Too many password reset attempts; try again later") from exc
            raise CognitoAuthError("Could not start Cognito password reset") from exc
        details = response.get("CodeDeliveryDetails")
        return details if isinstance(details, dict) else None

    def confirm_password_reset(self, username: str, confirmation_code: str, new_password: str) -> None:
        if not all(isinstance(value, str) and value for value in (username, confirmation_code, new_password)):
            raise CognitoPasswordError("Username, confirmation code, and new password are required")
        kwargs: Dict[str, Any] = {
            "ClientId": self.client_id,
            "Username": username,
            "ConfirmationCode": confirmation_code,
            "Password": new_password,
        }
        secret_hash = self._secret_hash(username)
        if secret_hash:
            kwargs["SecretHash"] = secret_hash
        try:
            self.client.confirm_forgot_password(**kwargs)
        except Exception as exc:
            code = self._error_code(exc)
            if code in {
                "CodeMismatchException",
                "ExpiredCodeException",
                "InvalidPasswordException",
                "InvalidParameterException",
            }:
                raise CognitoPasswordError(self._error_message(exc) or "Password reset was rejected") from exc
            raise CognitoAuthError("Could not confirm Cognito password reset") from exc
