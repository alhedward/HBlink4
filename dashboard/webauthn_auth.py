"""WebAuthn/passkey support for HBlink4 Cognito administrators.

The browser performs all WebAuthn ceremonies. HBlink4 keeps Cognito challenge
sessions and access tokens server-side and forwards only public challenge data
and credential responses between the browser and Cognito.
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .cognito_auth import (
    CognitoAdminAuthenticator,
    CognitoAuthError,
    CognitoAuthorizationError,
    CognitoChallengeError,
    CognitoIdentity,
    CognitoInvalidCredentials,
)


@dataclass(frozen=True)
class WebAuthnLoginStart:
    challenge_token: str
    public_key: Dict[str, Any]


@dataclass(frozen=True)
class WebAuthnLoginResult:
    identity: CognitoIdentity
    access_token: str


@dataclass
class _PendingWebAuthnLogin:
    username: str
    cognito_session: str
    expires_at: float


class CognitoWebAuthnBridge:
    """Register and authenticate Cognito passkeys/security keys."""

    def __init__(self, authenticator: CognitoAdminAuthenticator):
        self.auth = authenticator
        self._pending: Dict[str, _PendingWebAuthnLogin] = {}

    def _purge_pending(self) -> None:
        now = time.time()
        for token in [key for key, value in self._pending.items() if value.expires_at <= now]:
            self._pending.pop(token, None)

    @staticmethod
    def _parse_json_object(value: Any, label: str) -> Dict[str, Any]:
        if isinstance(value, dict):
            return value
        if not isinstance(value, str) or not value:
            raise CognitoChallengeError(f"Cognito did not return {label}")
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise CognitoChallengeError(f"Cognito returned invalid {label}") from exc
        if not isinstance(parsed, dict):
            raise CognitoChallengeError(f"Cognito returned invalid {label}")
        return parsed

    def start_login(self, username: str) -> WebAuthnLoginStart:
        if not isinstance(username, str) or not username.strip():
            raise CognitoInvalidCredentials("Enter your administrator username or email")
        username = username.strip()
        try:
            response = self.auth.client.admin_initiate_auth(
                UserPoolId=self.auth.user_pool_id,
                ClientId=self.auth.client_id,
                AuthFlow="USER_AUTH",
                AuthParameters=self.auth._with_secret_hash(
                    username,
                    {
                        "USERNAME": username,
                        "PREFERRED_CHALLENGE": "WEB_AUTHN",
                    },
                ),
            )
        except Exception as exc:
            code = self.auth._error_code(exc)
            if code in {"NotAuthorizedException", "UserNotFoundException"}:
                raise CognitoInvalidCredentials("Passkey sign-in is not available for this account") from exc
            raise CognitoAuthError("Could not start passkey sign-in") from exc

        if response.get("ChallengeName") != "WEB_AUTHN":
            raise CognitoChallengeError("Cognito did not offer a passkey challenge for this account")
        cognito_session = response.get("Session")
        if not isinstance(cognito_session, str) or not cognito_session:
            raise CognitoChallengeError("Cognito did not return a passkey challenge session")
        options = self._parse_json_object(
            (response.get("ChallengeParameters") or {}).get("CREDENTIAL_REQUEST_OPTIONS"),
            "passkey request options",
        )
        self._purge_pending()
        token = secrets.token_urlsafe(32)
        self._pending[token] = _PendingWebAuthnLogin(
            username=username,
            cognito_session=cognito_session,
            expires_at=time.time() + self.auth.challenge_timeout_seconds,
        )
        return WebAuthnLoginStart(challenge_token=token, public_key=options)

    def complete_login(self, challenge_token: str, credential: Any) -> WebAuthnLoginResult:
        self._purge_pending()
        challenge = self._pending.get(challenge_token)
        if challenge is None:
            raise CognitoChallengeError("Passkey sign-in challenge is invalid or expired")
        if not isinstance(credential, dict) or not credential:
            raise CognitoChallengeError("The browser did not return a passkey credential")
        try:
            response = self.auth.client.admin_respond_to_auth_challenge(
                UserPoolId=self.auth.user_pool_id,
                ClientId=self.auth.client_id,
                ChallengeName="WEB_AUTHN",
                ChallengeResponses=self.auth._with_secret_hash(
                    challenge.username,
                    {
                        "USERNAME": challenge.username,
                        "CREDENTIAL": json.dumps(credential, separators=(",", ":")),
                    },
                ),
                Session=challenge.cognito_session,
            )
        except Exception as exc:
            code = self.auth._error_code(exc)
            if code in {
                "NotAuthorizedException",
                "InvalidParameterException",
                "WebAuthnChallengeNotFoundException",
                "WebAuthnNotEnabledException",
            }:
                raise CognitoChallengeError("Passkey verification was rejected") from exc
            raise CognitoAuthError("Could not complete passkey sign-in") from exc

        if response.get("ChallengeName"):
            raise CognitoChallengeError(
                f"Cognito challenge {response['ChallengeName']} is not supported after passkey sign-in"
            )
        auth_result = response.get("AuthenticationResult")
        if not isinstance(auth_result, dict):
            raise CognitoAuthError("Cognito did not complete passkey authentication")
        access_token = auth_result.get("AccessToken")
        if not isinstance(access_token, str) or not access_token:
            raise CognitoAuthError("Cognito did not return an access token")
        try:
            identity = self.auth._authorize_authentication_result(auth_result)
        except CognitoAuthorizationError:
            raise
        self._pending.pop(challenge_token, None)
        return WebAuthnLoginResult(identity=identity, access_token=access_token)

    def start_registration(self, access_token: str) -> Dict[str, Any]:
        if not isinstance(access_token, str) or not access_token:
            raise CognitoChallengeError("Cognito session does not contain an access token")
        try:
            response = self.auth.client.start_web_authn_registration(AccessToken=access_token)
        except Exception as exc:
            raise CognitoAuthError("Could not start security-key registration") from exc
        options = response.get("CredentialCreationOptions")
        if not isinstance(options, dict):
            options = self._parse_json_object(options, "passkey registration options")
        return options

    def complete_registration(self, access_token: str, credential: Any) -> None:
        if not isinstance(access_token, str) or not access_token:
            raise CognitoChallengeError("Cognito session does not contain an access token")
        if not isinstance(credential, dict) or not credential:
            raise CognitoChallengeError("The browser did not return a security-key credential")
        try:
            self.auth.client.complete_web_authn_registration(
                AccessToken=access_token,
                Credential=credential,
            )
        except Exception as exc:
            code = self.auth._error_code(exc)
            if code in {
                "InvalidParameterException",
                "WebAuthnClientMismatchException",
                "WebAuthnOriginNotAllowedException",
                "WebAuthnRelyingPartyMismatchException",
                "WebAuthnNotEnabledException",
            }:
                raise CognitoChallengeError("Security-key registration was rejected") from exc
            raise CognitoAuthError("Could not complete security-key registration") from exc

    def list_credentials(self, access_token: str) -> list[dict]:
        if not isinstance(access_token, str) or not access_token:
            raise CognitoChallengeError("Cognito session does not contain an access token")
        try:
            response = self.auth.client.list_web_authn_credentials(AccessToken=access_token)
        except Exception as exc:
            raise CognitoAuthError("Could not list security-key credentials") from exc
        result = []
        for item in response.get("Credentials", []):
            if not isinstance(item, dict):
                continue
            result.append(
                {
                    "credential_id": str(item.get("CredentialId", "")),
                    "name": str(item.get("FriendlyCredentialName", "") or "Security key / passkey"),
                    "attachment": str(item.get("AuthenticatorAttachment", "")),
                    "relying_party_id": str(item.get("RelyingPartyId", "")),
                }
            )
        return result

    def delete_credential(self, access_token: str, credential_id: str) -> None:
        if not isinstance(access_token, str) or not access_token:
            raise CognitoChallengeError("Cognito session does not contain an access token")
        if not isinstance(credential_id, str) or not credential_id:
            raise CognitoChallengeError("Security-key credential ID is required")
        try:
            self.auth.client.delete_web_authn_credential(
                AccessToken=access_token,
                CredentialId=credential_id,
            )
        except Exception as exc:
            raise CognitoAuthError("Could not remove security-key credential") from exc

    def set_mfa_enabled(self, access_token: str, enabled: bool) -> None:
        """Opt this user into/out of passkey MFA.

        Cognito requires another MFA method (for this deployment, TOTP) to be
        enabled before WebAuthn MFA can be activated, preventing account lockout.
        """
        if not isinstance(access_token, str) or not access_token:
            raise CognitoChallengeError("Cognito session does not contain an access token")
        try:
            self.auth.client.set_user_mfa_preference(
                WebAuthnMfaSettings={"Enabled": bool(enabled)},
                AccessToken=access_token,
            )
        except Exception as exc:
            code = self.auth._error_code(exc)
            if code == "InvalidParameterException" and enabled:
                raise CognitoChallengeError(
                    "Enable authenticator-app MFA first, then enable security-key MFA"
                ) from exc
            raise CognitoAuthError("Could not change security-key MFA preference") from exc
