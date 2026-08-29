"""Outer ASGI layer for HBlink4 administrator WebAuthn/passkey features.

This wrapper sits outside ``auth_app`` so the existing local break-glass and
password/TOTP flows remain unchanged. It adds passkey registration and sign-in
using Cognito's USER_AUTH / WEB_AUTHN APIs.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi.responses import HTMLResponse, JSONResponse

from . import auth_app, server
from .cognito_auth import (
    CognitoAuthError,
    CognitoAuthorizationError,
    CognitoChallengeError,
    CognitoConfigError,
    CognitoInvalidCredentials,
)
from .webauthn_auth import CognitoWebAuthnBridge


_webauthn_bridge_instance = None


def _webauthn_bridge() -> CognitoWebAuthnBridge:
    global _webauthn_bridge_instance
    authenticator = server._cognito_authenticator()
    if _webauthn_bridge_instance is None or _webauthn_bridge_instance.auth is not authenticator:
        _webauthn_bridge_instance = CognitoWebAuthnBridge(authenticator)
    return _webauthn_bridge_instance


class AdminSecurityMiddleware:
    def __init__(self, app):
        self.app = app

    async def _send(self, response, scope, receive, send):
        await response(scope, receive, send)

    async def _serve_admin_page(self, scope, receive, send):
        html_path = Path(server.__file__).parent / "static" / "admin.html"
        if not html_path.exists():
            await self.app(scope, receive, send)
            return
        html = html_path.read_text(encoding="utf-8")
        scripts = (
            '\n<script src="/static/admin_mfa.js"></script>'
            '\n<script src="/static/admin_webauthn.js"></script>\n'
        )
        html = html.replace("</body>", scripts + "</body>")
        await self._send(HTMLResponse(html), scope, receive, send)

    async def _start_login(self, scope, receive, send):
        payload = await auth_app._read_json(receive)
        username = payload.get("username", "") if isinstance(payload, dict) else ""
        configured_username = str(server.admin_config.get("username", ""))
        if username == configured_username and configured_username:
            response = JSONResponse(
                {"detail": "The local break-glass account uses password authentication"},
                status_code=409,
            )
        else:
            try:
                result = await asyncio.to_thread(_webauthn_bridge().start_login, username)
            except CognitoInvalidCredentials as exc:
                response = JSONResponse({"detail": str(exc)}, status_code=401)
            except CognitoChallengeError as exc:
                response = JSONResponse({"detail": str(exc)}, status_code=409)
            except (CognitoConfigError, CognitoAuthError) as exc:
                response = JSONResponse({"detail": str(exc)}, status_code=503)
            else:
                response = JSONResponse(
                    {
                        "ok": True,
                        "challenge_token": result.challenge_token,
                        "public_key": result.public_key,
                    }
                )
        await self._send(response, scope, receive, send)

    async def _complete_login(self, scope, receive, send):
        payload = await auth_app._read_json(receive)
        token = payload.get("challenge_token", "") if isinstance(payload, dict) else ""
        credential = payload.get("credential") if isinstance(payload, dict) else None
        try:
            result = await asyncio.to_thread(
                _webauthn_bridge().complete_login, token, credential
            )
        except CognitoAuthorizationError:
            response = JSONResponse(
                {"detail": "This account is not authorized for HBlink4 administration"},
                status_code=403,
            )
        except CognitoChallengeError as exc:
            response = JSONResponse({"detail": str(exc)}, status_code=400)
        except (CognitoConfigError, CognitoAuthError) as exc:
            response = JSONResponse({"detail": str(exc)}, status_code=503)
        else:
            response = auth_app._cognito_session_response(
                result.identity.username, result.access_token
            )
            client = scope.get("client")
            client_key = client[0] if client else "unknown"
            server.login_limiter.record_success(client_key)
            server.logger.info(
                "Dashboard Cognito admin %s logged in with WebAuthn from %s",
                result.identity.username,
                client_key,
            )
        await self._send(response, scope, receive, send)

    async def _require_cognito_session(self, scope, require_csrf: bool = False):
        session = auth_app._session_from_scope(scope)
        if session is None:
            raise PermissionError("Administrator login required")
        access_token = getattr(session, "cognito_access_token", "")
        if not access_token:
            raise RuntimeError("Security-key management is available only to Cognito administrators")
        if require_csrf:
            auth_app._require_csrf_scope(scope, session)
        return session, access_token

    async def _manage(self, scope, receive, send, action: str):
        try:
            session, access_token = await self._require_cognito_session(
                scope, require_csrf=action not in {"list"}
            )
            if action == "start":
                options = await asyncio.to_thread(
                    _webauthn_bridge().start_registration, access_token
                )
                response = JSONResponse({"ok": True, "public_key": options})
            elif action == "complete":
                payload = await auth_app._read_json(receive)
                credential = payload.get("credential") if isinstance(payload, dict) else None
                await asyncio.to_thread(
                    _webauthn_bridge().complete_registration,
                    access_token,
                    credential,
                )
                response = JSONResponse({"ok": True})
            elif action == "list":
                credentials = await asyncio.to_thread(
                    _webauthn_bridge().list_credentials, access_token
                )
                response = JSONResponse(
                    {
                        "supported": True,
                        "username": session.username,
                        "credentials": credentials,
                    }
                )
            elif action == "delete":
                payload = await auth_app._read_json(receive)
                credential_id = (
                    payload.get("credential_id", "") if isinstance(payload, dict) else ""
                )
                await asyncio.to_thread(
                    _webauthn_bridge().delete_credential,
                    access_token,
                    credential_id,
                )
                response = JSONResponse({"ok": True})
            else:
                response = JSONResponse({"detail": "Unknown WebAuthn action"}, status_code=404)
        except PermissionError as exc:
            response = JSONResponse({"detail": str(exc)}, status_code=401)
        except RuntimeError as exc:
            response = JSONResponse({"detail": str(exc)}, status_code=409)
        except CognitoChallengeError as exc:
            response = JSONResponse({"detail": str(exc)}, status_code=400)
        except CognitoAuthError as exc:
            response = JSONResponse({"detail": str(exc)}, status_code=503)
        await self._send(response, scope, receive, send)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        method = scope.get("method")
        path = scope.get("path")

        if method == "GET" and path == "/admin":
            await self._serve_admin_page(scope, receive, send)
            return

        if server._admin_auth_provider() != "cognito" or not server.admin_config.get("enabled", False):
            await self.app(scope, receive, send)
            return

        if method == "POST" and path == "/api/admin/webauthn/login/start":
            await self._start_login(scope, receive, send)
            return
        if method == "POST" and path == "/api/admin/webauthn/login/complete":
            await self._complete_login(scope, receive, send)
            return
        if method == "POST" and path == "/api/admin/webauthn/register/start":
            await self._manage(scope, receive, send, "start")
            return
        if method == "POST" and path == "/api/admin/webauthn/register/complete":
            await self._manage(scope, receive, send, "complete")
            return
        if method == "GET" and path == "/api/admin/webauthn/credentials":
            await self._manage(scope, receive, send, "list")
            return
        if method == "POST" and path == "/api/admin/webauthn/credentials/delete":
            await self._manage(scope, receive, send, "delete")
            return

        await self.app(scope, receive, send)


app = AdminSecurityMiddleware(auth_app.app)
