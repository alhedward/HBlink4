"""Top-level HBlink4 administrator application.

Composes the existing local/Cognito/TOTP auth wrapper, WebAuthn security-key
wrapper, administrator profile/personalised-invitation routes, and public
local-service visibility.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import unquote

from fastapi.responses import HTMLResponse, JSONResponse

from . import auth_app, security_app, server
from .admin_identity import CognitoAdminIdentityService
from .cognito_auth import CognitoAuthError, CognitoPasswordError


_identity_service_instance = None
_ADMIN_ASSET_VERSION = "20260830-local-services-1"


def _identity_service() -> CognitoAdminIdentityService:
    global _identity_service_instance
    authenticator = server._cognito_authenticator()
    if (
        _identity_service_instance is None
        or _identity_service_instance.auth is not authenticator
    ):
        _identity_service_instance = CognitoAdminIdentityService(authenticator)
    return _identity_service_instance


def _public_local_services() -> list[dict]:
    """Return non-secret local-service metadata from the live HBlink4 config."""
    try:
        _raw, config, _revision, _section_key = server._talkgroup_store()._read()
    except Exception:
        return []

    services: list[dict] = []
    parrot = config.get("parrot")
    if isinstance(parrot, dict) and parrot.get("enabled") is True:
        talkgroup = parrot.get("talkgroup")
        if isinstance(talkgroup, int) and not isinstance(talkgroup, bool):
            services.append(
                {
                    "type": "parrot",
                    "name": "Parrot / Echo Test",
                    "talkgroup": talkgroup,
                    "scope": "local-only",
                    "description": (
                        "Records a local group call and plays it back only to the "
                        "originating repeater or hotspot. It is not routed to other "
                        "repeaters or external DMR networks."
                    ),
                }
            )
    return services


class AdminIdentityMiddleware:
    def __init__(self, app):
        self.app = app

    async def _send(self, response, scope, receive, send):
        await response(scope, receive, send)

    @staticmethod
    def _inject_local_services_script(html: str, version: str) -> str:
        script = f'\n<script src="/static/local_services.js?v={version}"></script>\n'
        return html.replace("</body>", script + "</body>")

    async def _serve_dashboard_page(self, scope, receive, send):
        html_path = Path(server.__file__).parent / "static" / "dashboard.html"
        if not html_path.exists():
            await self.app(scope, receive, send)
            return
        html = html_path.read_text(encoding="utf-8")
        html = self._inject_local_services_script(html, _ADMIN_ASSET_VERSION)
        await self._send(HTMLResponse(html, headers={"Cache-Control": "no-store"}), scope, receive, send)

    async def _serve_admin_page(self, scope, receive, send):
        html_path = Path(server.__file__).parent / "static" / "admin.html"
        if not html_path.exists():
            await self.app(scope, receive, send)
            return
        html = html_path.read_text(encoding="utf-8")
        version = _ADMIN_ASSET_VERSION
        scripts = (
            f'\n<script src="/static/admin_mfa.js?v={version}"></script>'
            f'\n<script src="/static/admin_webauthn.js?v={version}"></script>'
            f'\n<script src="/static/admin_invite_feedback.js?v={version}"></script>'
            f'\n<script src="/static/admin_identity.js?v={version}"></script>'
            f'\n<script src="/static/admin_profile_compact.js?v={version}"></script>'
            f'\n<script src="/static/local_services.js?v={version}"></script>\n'
        )
        html = html.replace("</body>", scripts + "</body>")
        await self._send(HTMLResponse(html, headers={"Cache-Control": "no-store"}), scope, receive, send)

    def _cognito_session(self, scope, require_csrf=False):
        session = auth_app._session_from_scope(scope)
        if session is None:
            raise PermissionError("Administrator login required")
        access_token = getattr(session, "cognito_access_token", "")
        if not access_token:
            raise RuntimeError(
                "Administrator profile and personalised invitations are available only to Cognito administrator sessions"
            )
        if require_csrf:
            auth_app._require_csrf_scope(scope, session)
        return session, access_token

    async def _profile(self, scope, receive, send, update=False):
        try:
            _session, access_token = self._cognito_session(scope, require_csrf=update)
            service = _identity_service()
            if update:
                payload = await auth_app._read_json(receive)
                if not isinstance(payload, dict):
                    raise CognitoPasswordError("Invalid administrator profile")
                profile = await asyncio.to_thread(
                    service.update_profile, access_token, payload
                )
            else:
                profile = await asyncio.to_thread(service.get_profile, access_token)
            response = JSONResponse(
                {
                    "ok": True,
                    "profile": profile,
                    "complete": service.profile_complete(profile),
                }
            )
        except PermissionError as exc:
            response = JSONResponse({"detail": str(exc)}, status_code=401)
        except RuntimeError as exc:
            response = JSONResponse({"detail": str(exc)}, status_code=409)
        except CognitoPasswordError as exc:
            response = JSONResponse({"detail": str(exc)}, status_code=400)
        except CognitoAuthError as exc:
            response = JSONResponse({"detail": str(exc)}, status_code=503)
        await self._send(response, scope, receive, send)

    async def _list_users(self, scope, receive, send):
        try:
            session, _access_token = self._cognito_session(scope)
            users = await asyncio.to_thread(_identity_service().list_admin_users)
            response = JSONResponse(
                {"users": users, "current_username": session.username}
            )
        except RuntimeError:
            # The permanent local break-glass account can still use the original
            # role-authorized admin listing/reset API. It simply has no Cognito
            # profile and cannot sign personalised invitations.
            await self.app(scope, receive, send)
            return
        except PermissionError as exc:
            response = JSONResponse({"detail": str(exc)}, status_code=401)
        except CognitoAuthError as exc:
            response = JSONResponse({"detail": str(exc)}, status_code=503)
        await self._send(response, scope, receive, send)

    async def _invite(self, scope, receive, send):
        try:
            _session, access_token = self._cognito_session(scope, require_csrf=True)
            payload = await auth_app._read_json(receive)
            if not isinstance(payload, dict):
                raise CognitoPasswordError("Invalid administrator invitation")
            service = _identity_service()
            inviter = await asyncio.to_thread(service.get_profile, access_token)
            username = await asyncio.to_thread(service.invite_admin, payload, inviter)
            response = JSONResponse({"ok": True, "username": username})
        except PermissionError as exc:
            response = JSONResponse({"detail": str(exc)}, status_code=401)
        except RuntimeError as exc:
            response = JSONResponse({"detail": str(exc)}, status_code=409)
        except CognitoPasswordError as exc:
            status = 409 if "Complete your administrator profile" in str(exc) else 400
            response = JSONResponse({"detail": str(exc)}, status_code=status)
        except CognitoAuthError as exc:
            response = JSONResponse({"detail": str(exc)}, status_code=503)
        await self._send(response, scope, receive, send)

    async def _resend(self, scope, receive, send, username):
        try:
            _session, access_token = self._cognito_session(scope, require_csrf=True)
            service = _identity_service()
            inviter = await asyncio.to_thread(service.get_profile, access_token)
            await asyncio.to_thread(service.resend_invite, username, inviter)
            response = JSONResponse({"ok": True})
        except PermissionError as exc:
            response = JSONResponse({"detail": str(exc)}, status_code=401)
        except RuntimeError as exc:
            response = JSONResponse({"detail": str(exc)}, status_code=409)
        except CognitoPasswordError as exc:
            response = JSONResponse({"detail": str(exc)}, status_code=409)
        except CognitoAuthError as exc:
            response = JSONResponse({"detail": str(exc)}, status_code=503)
        await self._send(response, scope, receive, send)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        method = scope.get("method")
        path = scope.get("path")

        if method == "GET" and path == "/":
            await self._serve_dashboard_page(scope, receive, send)
            return
        if method == "GET" and path == "/admin":
            await self._serve_admin_page(scope, receive, send)
            return
        if method == "GET" and path == "/api/local-services":
            await self._send(JSONResponse({"services": _public_local_services()}), scope, receive, send)
            return

        if server._admin_auth_provider() != "cognito" or not server.admin_config.get("enabled", False):
            await self.app(scope, receive, send)
            return

        if method == "GET" and path == "/api/admin/profile":
            await self._profile(scope, receive, send, update=False)
            return
        if method == "PUT" and path == "/api/admin/profile":
            await self._profile(scope, receive, send, update=True)
            return
        if method == "GET" and path == "/api/admin/users":
            await self._list_users(scope, receive, send)
            return
        if method == "POST" and path == "/api/admin/users/invite":
            await self._invite(scope, receive, send)
            return
        if method == "POST" and path.startswith("/api/admin/users/") and path.endswith("/resend-invite"):
            encoded = path[len("/api/admin/users/") : -len("/resend-invite")]
            username = unquote(encoded.rstrip("/"))
            await self._resend(scope, receive, send, username)
            return

        await self.app(scope, receive, send)


app = AdminIdentityMiddleware(security_app.app)
