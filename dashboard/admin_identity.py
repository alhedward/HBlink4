"""Cognito administrator profiles and personalised SES invitations."""

from __future__ import annotations

import html
import os
import re
import secrets
import string
from typing import Any, Dict, List

from .cognito_auth import CognitoAdminAuthenticator, CognitoAuthError, CognitoPasswordError


_CALLSIGN_RE = re.compile(r"^[A-Z0-9][A-Z0-9/-]{1,19}$")


class CognitoAdminIdentityService:
    def __init__(self, authenticator: CognitoAdminAuthenticator, ses_client=None):
        self.auth = authenticator
        self.sender = os.environ.get("HBLINK4_ADMIN_INVITE_FROM", "").strip()
        self.admin_url = os.environ.get(
            "HBLINK4_ADMIN_URL", "https://dmr.vk2ale.com/admin"
        ).strip()
        if ses_client is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover
                raise CognitoAuthError("Personalised administrator invitations require boto3") from exc
            ses_client = boto3.client("sesv2", region_name=self.auth.region)
        self.ses = ses_client

    @staticmethod
    def _attributes(items) -> Dict[str, str]:
        return {
            str(item.get("Name")): str(item.get("Value", ""))
            for item in items or []
            if isinstance(item, dict) and item.get("Name")
        }

    @staticmethod
    def _validate_name(value: Any, label: str) -> str:
        if not isinstance(value, str):
            raise CognitoPasswordError(f"{label} is required")
        value = " ".join(value.strip().split())
        if not value or len(value) > 80:
            raise CognitoPasswordError(f"{label} must be 1..80 characters")
        return value

    @staticmethod
    def _validate_callsign(value: Any) -> str:
        if not isinstance(value, str):
            raise CognitoPasswordError("Callsign is required")
        value = value.strip().upper()
        if not _CALLSIGN_RE.fullmatch(value):
            raise CognitoPasswordError("Enter a valid callsign using letters, numbers, / or -")
        return value

    @staticmethod
    def _validate_email(value: Any) -> str:
        if not isinstance(value, str):
            raise CognitoPasswordError("Email address is required")
        value = value.strip().lower()
        if "@" not in value or len(value) > 320:
            raise CognitoPasswordError("Enter a valid administrator email address")
        return value

    @classmethod
    def normalize_profile(cls, payload: Dict[str, Any], require_email: bool = False) -> Dict[str, str]:
        profile = {
            "given_name": cls._validate_name(payload.get("given_name"), "First name"),
            "family_name": cls._validate_name(payload.get("family_name"), "Last name"),
            "callsign": cls._validate_callsign(payload.get("callsign")),
        }
        if require_email:
            profile["email"] = cls._validate_email(payload.get("email"))
        return profile

    @staticmethod
    def profile_complete(profile: Dict[str, str]) -> bool:
        return all(profile.get(key) for key in ("given_name", "family_name", "callsign", "email"))

    def get_profile(self, access_token: str) -> Dict[str, str]:
        try:
            response = self.auth.client.get_user(AccessToken=access_token)
        except Exception as exc:
            raise CognitoAuthError("Could not read administrator profile") from exc
        attrs = self._attributes(response.get("UserAttributes"))
        return {
            "username": str(response.get("Username", "")),
            "email": attrs.get("email", ""),
            "given_name": attrs.get("given_name", ""),
            "family_name": attrs.get("family_name", ""),
            "callsign": attrs.get("nickname", "").upper(),
        }

    def update_profile(self, access_token: str, payload: Dict[str, Any]) -> Dict[str, str]:
        values = self.normalize_profile(payload)
        try:
            self.auth.client.update_user_attributes(
                AccessToken=access_token,
                UserAttributes=[
                    {"Name": "given_name", "Value": values["given_name"]},
                    {"Name": "family_name", "Value": values["family_name"]},
                    {"Name": "nickname", "Value": values["callsign"]},
                ],
            )
        except Exception as exc:
            raise CognitoAuthError("Could not update administrator profile") from exc
        return self.get_profile(access_token)

    def list_admin_users(self) -> List[Dict[str, Any]]:
        users = []
        token = None
        try:
            while True:
                kwargs = {
                    "UserPoolId": self.auth.user_pool_id,
                    "GroupName": self.auth.admin_group,
                    "Limit": 60,
                }
                if token:
                    kwargs["NextToken"] = token
                response = self.auth.client.list_users_in_group(**kwargs)
                for user in response.get("Users", []):
                    if not isinstance(user, dict):
                        continue
                    attrs = self._attributes(user.get("Attributes"))
                    users.append(
                        {
                            "username": str(user.get("Username", "")),
                            "email": attrs.get("email", ""),
                            "given_name": attrs.get("given_name", ""),
                            "family_name": attrs.get("family_name", ""),
                            "callsign": attrs.get("nickname", "").upper(),
                            "enabled": bool(user.get("Enabled", False)),
                            "status": str(user.get("UserStatus", "")),
                        }
                    )
                token = response.get("NextToken")
                if not token:
                    break
        except Exception as exc:
            raise CognitoAuthError("Could not list Cognito administrator users") from exc
        return sorted(users, key=lambda item: (item.get("email") or item.get("username") or "").lower())

    @staticmethod
    def _temporary_password() -> str:
        alphabet = string.ascii_letters + string.digits + "!@#%_-"
        while True:
            value = "".join(secrets.choice(alphabet) for _ in range(20))
            if any(c.islower() for c in value) and any(c.isupper() for c in value) and any(c.isdigit() for c in value):
                return value

    def _message(self, invitee: Dict[str, str], inviter: Dict[str, str], temporary_password: str):
        first = html.escape(invitee["given_name"])
        email = html.escape(invitee["email"])
        callsign = html.escape(invitee["callsign"])
        url = html.escape(self.admin_url, quote=True)
        password = html.escape(temporary_password)
        inviter_name = html.escape(f"{inviter['given_name']} {inviter['family_name']}")
        inviter_callsign = html.escape(inviter["callsign"])
        signature = f"{inviter_name} ({inviter_callsign})"

        subject = "HBlink4 dashboard administrator invitation"
        html_body = f"""<!doctype html>
<html><body style="font-family:Arial,Helvetica,sans-serif;color:#172033;line-height:1.5">
<p>Hi {first},</p>
<p>You have been invited to administer the HBlink4 DMR server as <strong>{callsign}</strong>.</p>
<p><a href="{url}" style="display:inline-block;background:#146c94;color:#fff;text-decoration:none;padding:10px 16px;border-radius:6px">Open HBlink4 Administration</a></p>
<p>If the button does not work, open <a href="{url}">{url}</a>.</p>
<table style="border-collapse:collapse;margin:16px 0">
<tr><td style="padding:5px 12px 5px 0"><strong>Username</strong></td><td>{email}</td></tr>
<tr><td style="padding:5px 12px 5px 0"><strong>Temporary password</strong></td><td><code>{password}</code></td></tr>
</table>
<p>At first sign-in you will choose a permanent password. It must:</p>
<ul><li>be at least 12 characters</li><li>contain an uppercase letter</li><li>contain a lowercase letter</li><li>contain a number</li><li>a symbol is optional</li></ul>
<p>The temporary password is valid for 7 days. Optional authenticator-app MFA and FIDO2/WebAuthn security keys are available after sign-in.</p>
<p>Regards,<br>{signature}<br>HBlink4 administrator</p>
</body></html>"""
        text_body = (
            f"Hi {invitee['given_name']},\n\n"
            f"You have been invited to administer the HBlink4 DMR server as {invitee['callsign']}.\n\n"
            f"Login: {self.admin_url}\nUsername: {invitee['email']}\n"
            f"Temporary password: {temporary_password}\n\n"
            "Permanent password rules: at least 12 characters, one uppercase letter, "
            "one lowercase letter, and one number. A symbol is optional.\n"
            "The temporary password is valid for 7 days.\n\n"
            f"Regards,\n{inviter['given_name']} {inviter['family_name']} ({inviter['callsign']})\n"
        )
        return subject, html_body, text_body

    def _send_invite(self, invitee: Dict[str, str], inviter: Dict[str, str], temporary_password: str) -> None:
        if not self.sender:
            raise CognitoAuthError("Administrator invitation sender is not configured")
        subject, html_body, text_body = self._message(invitee, inviter, temporary_password)
        kwargs = {
            "FromEmailAddress": self.sender,
            "Destination": {"ToAddresses": [invitee["email"]]},
            "Content": {
                "Simple": {
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": {
                        "Html": {"Data": html_body, "Charset": "UTF-8"},
                        "Text": {"Data": text_body, "Charset": "UTF-8"},
                    },
                }
            },
        }
        if inviter.get("email"):
            kwargs["ReplyToAddresses"] = [inviter["email"]]
        try:
            self.ses.send_email(**kwargs)
        except Exception as exc:
            raise CognitoAuthError("Cognito user was prepared but the invitation email could not be sent") from exc

    def invite_admin(self, payload: Dict[str, Any], inviter: Dict[str, str]) -> str:
        if not self.profile_complete(inviter):
            raise CognitoPasswordError("Complete your administrator profile before sending invitations")
        invitee = self.normalize_profile(payload, require_email=True)
        temporary_password = self._temporary_password()
        try:
            response = self.auth.client.admin_create_user(
                UserPoolId=self.auth.user_pool_id,
                Username=invitee["email"],
                TemporaryPassword=temporary_password,
                MessageAction="SUPPRESS",
                UserAttributes=[
                    {"Name": "email", "Value": invitee["email"]},
                    {"Name": "email_verified", "Value": "true"},
                    {"Name": "given_name", "Value": invitee["given_name"]},
                    {"Name": "family_name", "Value": invitee["family_name"]},
                    {"Name": "nickname", "Value": invitee["callsign"]},
                ],
            )
            username = str((response.get("User") or {}).get("Username") or invitee["email"])
            self.auth.client.admin_add_user_to_group(
                UserPoolId=self.auth.user_pool_id,
                Username=username,
                GroupName=self.auth.admin_group,
            )
        except Exception as exc:
            code = self.auth._error_code(exc)
            if code == "UsernameExistsException":
                raise CognitoPasswordError("That Cognito user already exists") from exc
            raise CognitoAuthError("Could not create Cognito administrator") from exc
        self._send_invite(invitee, inviter, temporary_password)
        return username

    def resend_invite(self, username: str, inviter: Dict[str, str]) -> None:
        if not self.profile_complete(inviter):
            raise CognitoPasswordError("Complete your administrator profile before resending invitations")
        try:
            user = self.auth.client.admin_get_user(
                UserPoolId=self.auth.user_pool_id,
                Username=username,
            )
            attrs = self._attributes(user.get("UserAttributes"))
            invitee = {
                "email": attrs.get("email", username),
                "given_name": attrs.get("given_name", "Administrator"),
                "family_name": attrs.get("family_name", ""),
                "callsign": attrs.get("nickname", "ADMIN").upper(),
            }
            temporary_password = self._temporary_password()
            self.auth.client.admin_set_user_password(
                UserPoolId=self.auth.user_pool_id,
                Username=username,
                Password=temporary_password,
                Permanent=False,
            )
        except Exception as exc:
            raise CognitoAuthError("Could not prepare administrator invitation resend") from exc
        self._send_invite(invitee, inviter, temporary_password)
