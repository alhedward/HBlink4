"""ASGI admin-auth wrapper for Cognito, optional TOTP MFA, and local break-glass."""

from __future__ import annotations

import asyncio
import hmac
import json
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import quote

from fastapi.responses import HTMLResponse, JSONResponse

from . import server
from .cognito_auth import (
    CognitoAuthError,
    CognitoAuthorizationError,
    CognitoChallengeError,
    CognitoConfigError,
    CognitoInvalidCredentials,
    CognitoPasswordError,
)
from .mfa_auth import CognitoMfaBridge


_mfa_bridge_instance = None


def _mfa_bridge() -> CognitoMfaBridge:
    global _mfa_bridge_instance
    authenticator = server._cognito_authenticator()
    if _mfa_bridge_instance is None or _mfa_bridge_instance.auth is not authenticator:
        _mfa_bridge_instance = CognitoMfaBridge(authenticator)
    return _mfa_bridge_instance


def _header(scope, name: bytes) -> str:
    target = name.lower()
    for key, value in scope.get("headers", []):
        if key.lower() == target:
            return value.decode("latin-1")
    return ""


def _session_from_scope(scope):
    raw_cookie = _header(scope, b"cookie")
    if not raw_cookie:
        return None
    cookies = SimpleCookie()
    try:
        cookies.load(raw_cookie)
    except Exception:
        return None
    morsel = cookies.get(server.ADMIN_COOKIE_NAME)
    return server.admin_sessions.get(morsel.value if morsel else None)


def _require_csrf_scope(scope, session) -> None:
    supplied = _header(scope, b"x-csrf-token")
    if not supplied or not hmac.compare_digest(supplied, session.csrf_token):
        raise CognitoChallengeError("Invalid CSRF token")


async def _read_json(receive):
    body = bytearray()
    while True:
        message = await receive()
        if message.get("type") == "http.disconnect":
            return None
        if message.get("type") != "http.request":
            continue
        body.extend(message.get("body", b""))
        if not message.get("more_body", False):
            break
    try:
        return json.loads(bytes(body).decode("utf-8")) if body else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _cognito_session_response(username: str, access_token: str) -> JSONResponse:
    token, session = server.admin_sessions.create(username)
    # The token remains server-side and dies with the ordinary HBlink4 session.
    # It is used only for the signed-in user's opt-in MFA management calls.
    session.cognito_access_token = access_token
    response = JSONResponse(
        {"ok": True, "username": session.username, "csrf_token": session.csrf_token}
    )
    response.set_cookie(
        server.ADMIN_COOKIE_NAME,
        token,
        max_age=server.admin_sessions.timeout_seconds,
        httponly=True,
        secure=bool(server.admin_config.get("cookie_secure", False)),
        samesite="strict",
        path="/",
    )
    return response


class BreakGlassAdminMiddleware:
    """Handle Cognito auth/MFA while reserving the local PBKDF2 break-glass user."""

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
        injection = '\n<script src="/static/admin_mfa.js"></script>\n'
        if injection.strip() not in html:
            html = html.replace("</body>", injection + "</body>")
        await self._send(HTMLResponse(html), scope, receive, send)

    async def _handle_login(self, scope, receive, send):
        payload = await _read_json(receive)
        username = payload.get("username", "") if isinstance(payload, dict) else ""
        password = payload.get("password", "") if isinstance(payload, dict) else ""
        configured_username = str(server.admin_config.get("username", ""))
        local_hash = server._admin_password_hash()
        client = scope.get("client")
        client_key = client[0] if client else "unknown"

        retry_after = server.login_limiter.retry_after(client_key)
        if retry_after:
            await self._send(
                JSONResponse(
                    {"detail": f"Too many failed login attempts; try again in {retry_after} seconds"},
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                ),
                scope,
                receive,
                send,
            )
            return

        is_local_username = (
            isinstance(username, str)
            and bool(configured_username)
            and hmac.compare_digest(username, configured_username)
        )
        if is_local_username and local_hash:
            if not isinstance(password, str) or len(password) > 4096:
                password_ok = False
            else:
                password_ok = await asyncio.to_thread(server.verify_password, password, local_hash)
            if not password_ok:
                server.login_limiter.record_failure(client_key)
                server.logger.warning(
                    "Dashboard break-glass local admin login failed from %s", client_key
                )
                await self._send(
                    JSONResponse({"detail": "Invalid username or password"}, status_code=401),
                    scope,
                    receive,
                    send,
                )
                return
            server.login_limiter.record_success(client_key)
            response = server._admin_login_response(configured_username)
            server.logger.warning(
                "Dashboard break-glass local admin login succeeded from %s", client_key
            )
            await self._send(response, scope, receive, send)
            return

        if not isinstance(username, str) or not isinstance(password, str) or len(password) > 4096:
            server.login_limiter.record_failure(client_key)
            await self._send(
                JSONResponse({"detail": "Invalid username or password"}, status_code=401),
                scope,
                receive,
                send,
            )
            return

        try:
            result = await asyncio.to_thread(_mfa_bridge().authenticate, username, password)
        except CognitoInvalidCredentials:
            server.login_limiter.record_failure(client_key)
            server.logger.warning("Dashboard Cognito admin login failed from %s", client_key)
            response = JSONResponse({"detail": "Invalid username or password"}, status_code=401)
        except CognitoAuthorizationError:
            server.login_limiter.record_failure(client_key)
            response = JSONResponse(
                {"detail": "This account is not authorized for HBlink4 administration"},
                status_code=403,
            )
        except (CognitoPasswordError, CognitoChallengeError) as exc:
            response = JSONResponse({"detail": str(exc)}, status_code=409)
        except (CognitoConfigError, CognitoAuthError) as exc:
            server.logger.error("Cognito dashboard authentication failed: %s", exc)
            response = JSONResponse({"detail": str(exc)}, status_code=503)
        else:
            server.login_limiter.record_success(client_key)
            if result.status == "new_password_required":
                response = JSONResponse(
                    {
                        "ok": False,
                        "challenge": "NEW_PASSWORD_REQUIRED",
                        "challenge_token": result.challenge_token,
                        "required_attributes": list(result.required_attributes),
                    }
                )
            elif result.status == "mfa_required":
                response = JSONResponse(
                    {
                        "ok": False,
                        "challenge": "SOFTWARE_TOKEN_MFA",
                        "challenge_token": result.challenge_token,
                    }
                )
            elif result.identity is not None and result.access_token:
                response = _cognito_session_response(result.identity.username, result.access_token)
                server.logger.info(
                    "Dashboard Cognito admin %s logged in from %s",
                    result.identity.username,
                    client_key,
                )
            else:
                response = JSONResponse({"detail": "Cognito returned an incomplete login result"}, status_code=503)
        await self._send(response, scope, receive, send)

    async def _handle_new_password(self, scope, receive, send):
        payload = await _read_json(receive)
        token = payload.get("challenge_token", "") if isinstance(payload, dict) else ""
        password = payload.get("new_password", "") if isinstance(payload, dict) else ""
        try:
            result = await asyncio.to_thread(_mfa_bridge().complete_new_password, token, password)
        except (CognitoChallengeError, CognitoPasswordError) as exc:
            response = JSONResponse({"detail": str(exc)}, status_code=400)
        except (CognitoConfigError, CognitoAuthError) as exc:
            response = JSONResponse({"detail": str(exc)}, status_code=503)
        else:
            if result.status == "mfa_required":
                response = JSONResponse(
                    {
                        "ok": False,
                        "challenge": "SOFTWARE_TOKEN_MFA",
                        "challenge_token": result.challenge_token,
                    }
                )
            elif result.identity is not None and result.access_token:
                response = _cognito_session_response(result.identity.username, result.access_token)
                server.logger.info(
                    "Dashboard Cognito admin %s completed first-login password change",
                    result.identity.username,
                )
            else:
                response = JSONResponse({"detail": "Cognito returned an incomplete password-change result"}, status_code=503)
        await self._send(response, scope, receive, send)

    async def _handle_mfa_challenge(self, scope, receive, send):
        payload = await _read_json(receive)
        token = payload.get("challenge_token", "") if isinstance(payload, dict) else ""
        code = payload.get("code", "") if isinstance(payload, dict) else ""
        try:
            result = await asyncio.to_thread(_mfa_bridge().complete_mfa, token, code)
        except CognitoChallengeError as exc:
            response = JSONResponse({"detail": str(exc)}, status_code=400)
        except (CognitoConfigError, CognitoAuthError) as exc:
            response = JSONResponse({"detail": str(exc)}, status_code=503)
        else:
            if result.identity is None or not result.access_token:
                response = JSONResponse({"detail": "Cognito returned an incomplete MFA result"}, status_code=503)
            else:
                response = _cognito_session_response(result.identity.username, result.access_token)
        await self._send(response, scope, receive, send)

    async def _handle_mfa_manage(self, scope, receive, send, action: str):
        session = _session_from_scope(scope)
        if session is None:
            await self._send(JSONResponse({"detail": "Administrator login required"}, status_code=401), scope, receive, send)
            return
        access_token = getattr(session, "cognito_access_token", "")
        if not access_token:
            await self._send(
                JSONResponse(
                    {"detail": "MFA management is available only to Cognito administrator sessions"},
                    status_code=409,
                ),
                scope,
                receive,
                send,
            )
            return

        try:
            if action == "status":
                result = await asyncio.to_thread(_mfa_bridge().mfa_status, access_token)
                response = JSONResponse({"supported": True, **result})
            else:
                _require_csrf_scope(scope, session)
                if action == "start":
                    secret = await asyncio.to_thread(_mfa_bridge().start_totp_setup, access_token)
                    issuer = "HBlink4"
                    account = session.username
                    uri = (
                        f"otpauth://totp/{quote(issuer)}:{quote(account)}"
                        f"?secret={quote(secret)}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"
                    )
                    response = JSONResponse({"ok": True, "secret": secret, "otpauth_uri": uri})
                elif action == "verify":
                    payload = await _read_json(receive)
                    code = payload.get("code", "") if isinstance(payload, dict) else ""
                    await asyncio.to_thread(_mfa_bridge().verify_totp_setup, access_token, code)
                    response = JSONResponse({"ok": True})
                elif action == "disable":
                    await asyncio.to_thread(_mfa_bridge().disable_totp, access_token)
                    response = JSONResponse({"ok": True})
                else:
                    response = JSONResponse({"detail": "Unknown MFA action"}, status_code=404)
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

        if method == "POST" and path == "/api/admin/login":
            await self._handle_login(scope, receive, send)
            return
        if method == "POST" and path == "/api/admin/new-password":
            await self._handle_new_password(scope, receive, send)
            return
        if method == "POST" and path == "/api/admin/mfa/challenge":
            await self._handle_mfa_challenge(scope, receive, send)
            return
        if method == "GET" and path == "/api/admin/mfa/status":
            await self._handle_mfa_manage(scope, receive, send, "status")
            return
        if method == "POST" and path == "/api/admin/mfa/setup/start":
            await self._handle_mfa_manage(scope, receive, send, "start")
            return
        if method == "POST" and path == "/api/admin/mfa/setup/verify":
            await self._handle_mfa_manage(scope, receive, send, "verify")
            return
        if method == "POST" and path == "/api/admin/mfa/disable":
            await self._handle_mfa_manage(scope, receive, send, "disable")
            return

        await self.app(scope, receive, send)


app = BreakGlassAdminMiddleware(server.app)
