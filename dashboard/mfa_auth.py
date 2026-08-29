"""Optional TOTP MFA helpers layered on HBlink4's Cognito administrator auth.

The existing Cognito helper predates MFA and deliberately supports only the
first-login password challenge.  This bridge keeps MFA state server-side,
handles SOFTWARE_TOKEN_MFA login challenges, and exposes opt-in TOTP setup
using Cognito access tokens held only in HBlink4's in-memory admin session.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Optional, Tuple

from .cognito_auth import (
    CognitoAdminAuthenticator,
    CognitoAuthError,
    CognitoAuthorizationError,
    CognitoChallengeError,
    CognitoIdentity,
    CognitoInvalidCredentials,
    CognitoPasswordError,
)


@dataclass(frozen=True)
class CognitoBridgeLoginResult:
    status: str
    identity: Optional[CognitoIdentity] = None
    access_token: Optional[str] = None
    challenge_token: Optional[str] = None
    required_attributes: Tuple[str, ...] = ()


@dataclass
class _PendingLoginChallenge:
    username: str
    cognito_session: str
    challenge_name: str
    expires_at: float


class CognitoMfaBridge:
    """Add optional software-token MFA to the existing Cognito admin helper."""

    def __init__(self, authenticator: CognitoAdminAuthenticator):
        self.auth = authenticator
        self._pending: dict[str, _PendingLoginChallenge] = {}

    def _purge_pending(self) -> None:
        now = time.time()
        for token in [key for key, value in self._pending.items() if value.expires_at <= now]:
            self._pending.pop(token, None)

    def _store_challenge(self, username: str, response: dict, challenge_name: str) -> str:
        cognito_session = response.get("Session")
        if not isinstance(cognito_session, str) or not cognito_session:
            raise CognitoChallengeError("Cognito did not return an authentication challenge session")
        self._purge_pending()
        token = secrets.token_urlsafe(32)
        self._pending[token] = _PendingLoginChallenge(
            username=username,
            cognito_session=cognito_session,
            challenge_name=challenge_name,
            expires_at=time.time() + self.auth.challenge_timeout_seconds,
        )
        return token

    def _authenticated(self, authentication_result: dict) -> CognitoBridgeLoginResult:
        access_token = authentication_result.get("AccessToken")
        if not isinstance(access_token, str) or not access_token:
            raise CognitoAuthError("Cognito did not return an access token")
        identity = self.auth._authorize_authentication_result(authentication_result)
        return CognitoBridgeLoginResult(
            status="authenticated",
            identity=identity,
            access_token=access_token,
        )

    def authenticate(self, username: str, password: str) -> CognitoBridgeLoginResult:
        if not isinstance(username, str) or not username or not isinstance(password, str) or not password:
            raise CognitoInvalidCredentials("Invalid username or password")
        try:
            response = self.auth.client.admin_initiate_auth(
                UserPoolId=self.auth.user_pool_id,
                ClientId=self.auth.client_id,
                AuthFlow="ADMIN_USER_PASSWORD_AUTH",
                AuthParameters=self.auth._with_secret_hash(
                    username,
                    {"USERNAME": username, "PASSWORD": password},
                ),
            )
        except Exception as exc:
            code = self.auth._error_code(exc)
            if code in {"NotAuthorizedException", "UserNotFoundException"}:
                raise CognitoInvalidCredentials("Invalid username or password") from exc
            if code == "PasswordResetRequiredException":
                raise CognitoPasswordError("Password reset required") from exc
            raise CognitoAuthError("Cognito authentication is temporarily unavailable") from exc

        challenge_name = response.get("ChallengeName")
        if challenge_name == "NEW_PASSWORD_REQUIRED":
            token = self._store_challenge(username, response, challenge_name)
            return CognitoBridgeLoginResult(
                status="new_password_required",
                challenge_token=token,
                required_attributes=self.auth._required_attributes(response),
            )
        if challenge_name == "SOFTWARE_TOKEN_MFA":
            token = self._store_challenge(username, response, challenge_name)
            return CognitoBridgeLoginResult(status="mfa_required", challenge_token=token)
        if challenge_name:
            raise CognitoChallengeError(
                f"Cognito challenge {challenge_name} is not supported by this HBlink4 login page"
            )

        authentication_result = response.get("AuthenticationResult")
        if not isinstance(authentication_result, dict):
            raise CognitoAuthError("Cognito login returned neither a result nor a challenge")
        return self._authenticated(authentication_result)

    def complete_new_password(self, challenge_token: str, new_password: str) -> CognitoBridgeLoginResult:
        self._purge_pending()
        challenge = self._pending.get(challenge_token)
        if challenge is None or challenge.challenge_name != "NEW_PASSWORD_REQUIRED":
            raise CognitoChallengeError("First-login password challenge is invalid or expired")
        if not isinstance(new_password, str) or not new_password:
            raise CognitoPasswordError("New password must not be empty")

        responses = self.auth._with_secret_hash(
            challenge.username,
            {"USERNAME": challenge.username, "NEW_PASSWORD": new_password},
        )
        try:
            response = self.auth.client.admin_respond_to_auth_challenge(
                UserPoolId=self.auth.user_pool_id,
                ClientId=self.auth.client_id,
                ChallengeName="NEW_PASSWORD_REQUIRED",
                ChallengeResponses=responses,
                Session=challenge.cognito_session,
            )
        except Exception as exc:
            code = self.auth._error_code(exc)
            if code in {
                "InvalidPasswordException",
                "InvalidParameterException",
                "NotAuthorizedException",
            }:
                raise CognitoPasswordError(
                    self.auth._error_message(exc) or "New password was rejected"
                ) from exc
            raise CognitoAuthError("Could not complete Cognito first-login password change") from exc

        next_challenge = response.get("ChallengeName")
        if next_challenge == "SOFTWARE_TOKEN_MFA":
            cognito_session = response.get("Session")
            if not isinstance(cognito_session, str) or not cognito_session:
                raise CognitoChallengeError("Cognito did not return an MFA challenge session")
            challenge.challenge_name = "SOFTWARE_TOKEN_MFA"
            challenge.cognito_session = cognito_session
            challenge.expires_at = time.time() + self.auth.challenge_timeout_seconds
            return CognitoBridgeLoginResult(status="mfa_required", challenge_token=challenge_token)
        if next_challenge:
            raise CognitoChallengeError(
                f"Cognito challenge {next_challenge} is not supported by this HBlink4 login page"
            )

        authentication_result = response.get("AuthenticationResult")
        if not isinstance(authentication_result, dict):
            raise CognitoAuthError("Cognito did not complete the first-login authentication")
        self._pending.pop(challenge_token, None)
        return self._authenticated(authentication_result)

    def complete_mfa(self, challenge_token: str, code: str) -> CognitoBridgeLoginResult:
        self._purge_pending()
        challenge = self._pending.get(challenge_token)
        if challenge is None or challenge.challenge_name != "SOFTWARE_TOKEN_MFA":
            raise CognitoChallengeError("MFA challenge is invalid or expired")
        if not isinstance(code, str) or len(code.strip()) != 6 or not code.strip().isdigit():
            raise CognitoChallengeError("Enter the 6-digit authenticator code")

        responses = self.auth._with_secret_hash(
            challenge.username,
            {"USERNAME": challenge.username, "SOFTWARE_TOKEN_MFA_CODE": code.strip()},
        )
        try:
            response = self.auth.client.admin_respond_to_auth_challenge(
                UserPoolId=self.auth.user_pool_id,
                ClientId=self.auth.client_id,
                ChallengeName="SOFTWARE_TOKEN_MFA",
                ChallengeResponses=responses,
                Session=challenge.cognito_session,
            )
        except Exception as exc:
            code_name = self.auth._error_code(exc)
            if code_name in {"CodeMismatchException", "NotAuthorizedException", "InvalidParameterException"}:
                raise CognitoChallengeError("Authenticator code was rejected") from exc
            raise CognitoAuthError("Could not complete Cognito MFA authentication") from exc

        next_challenge = response.get("ChallengeName")
        if next_challenge:
            raise CognitoChallengeError(
                f"Cognito challenge {next_challenge} is not supported by this HBlink4 login page"
            )
        authentication_result = response.get("AuthenticationResult")
        if not isinstance(authentication_result, dict):
            raise CognitoAuthError("Cognito did not complete MFA authentication")
        self._pending.pop(challenge_token, None)
        return self._authenticated(authentication_result)

    def mfa_status(self, access_token: str) -> dict:
        if not isinstance(access_token, str) or not access_token:
            raise CognitoChallengeError("Cognito session does not contain an access token")
        try:
            user = self.auth.client.get_user(AccessToken=access_token)
        except Exception as exc:
            raise CognitoAuthError("Could not read Cognito MFA status") from exc
        methods = [str(value) for value in user.get("UserMFASettingList", [])]
        preferred = str(user.get("PreferredMfaSetting", ""))
        enabled = "SOFTWARE_TOKEN_MFA" in methods or preferred == "SOFTWARE_TOKEN_MFA"
        return {
            "enabled": enabled,
            "preferred": preferred == "SOFTWARE_TOKEN_MFA",
            "methods": methods,
        }

    def start_totp_setup(self, access_token: str) -> str:
        if not isinstance(access_token, str) or not access_token:
            raise CognitoChallengeError("Cognito session does not contain an access token")
        try:
            response = self.auth.client.associate_software_token(AccessToken=access_token)
        except Exception as exc:
            raise CognitoAuthError("Could not start authenticator-app setup") from exc
        secret = response.get("SecretCode")
        if not isinstance(secret, str) or not secret:
            raise CognitoAuthError("Cognito did not return an authenticator secret")
        return secret

    def verify_totp_setup(self, access_token: str, code: str) -> None:
        if not isinstance(code, str) or len(code.strip()) != 6 or not code.strip().isdigit():
            raise CognitoChallengeError("Enter the 6-digit authenticator code")
        try:
            response = self.auth.client.verify_software_token(
                AccessToken=access_token,
                UserCode=code.strip(),
                FriendlyDeviceName="HBlink4 dashboard admin",
            )
            if response.get("Status") != "SUCCESS":
                raise CognitoChallengeError("Authenticator code was not verified")
            self.auth.client.set_user_mfa_preference(
                SoftwareTokenMfaSettings={"Enabled": True, "PreferredMfa": True},
                AccessToken=access_token,
            )
        except CognitoChallengeError:
            raise
        except Exception as exc:
            code_name = self.auth._error_code(exc)
            if code_name in {"CodeMismatchException", "EnableSoftwareTokenMFAException", "InvalidParameterException"}:
                raise CognitoChallengeError("Authenticator code was rejected") from exc
            raise CognitoAuthError("Could not enable authenticator-app MFA") from exc

    def disable_totp(self, access_token: str) -> None:
        if not isinstance(access_token, str) or not access_token:
            raise CognitoChallengeError("Cognito session does not contain an access token")
        try:
            self.auth.client.set_user_mfa_preference(
                SoftwareTokenMfaSettings={"Enabled": False, "PreferredMfa": False},
                AccessToken=access_token,
            )
        except Exception as exc:
            raise CognitoAuthError("Could not disable authenticator-app MFA") from exc
