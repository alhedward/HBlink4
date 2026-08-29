"""ASGI wrapper that keeps the local PBKDF2 admin as a Cognito break-glass login."""

from __future__ import annotations

import asyncio
import hmac
import json

from fastapi.responses import JSONResponse

from . import server


class BreakGlassAdminMiddleware:
    """Route the reserved local administrator to PBKDF2 while Cognito is primary.

    The local credential is only considered when the submitted username exactly
    matches the configured legacy local username. Other usernames are replayed
    unchanged to the normal HBlink4 application and therefore follow the
    Cognito flow. A failed local-password attempt never falls through to
    Cognito, which keeps the two authentication paths explicit and auditable.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope.get("type") != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != "/api/admin/login"
            or server._admin_auth_provider() != "cognito"
            or not server.admin_config.get("enabled", False)
        ):
            await self.app(scope, receive, send)
            return

        body = bytearray()
        while True:
            message = await receive()
            if message.get("type") == "http.disconnect":
                return
            if message.get("type") != "http.request":
                continue
            body.extend(message.get("body", b""))
            if not message.get("more_body", False):
                break

        raw_body = bytes(body)
        try:
            payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = None

        username = payload.get("username", "") if isinstance(payload, dict) else ""
        password = payload.get("password", "") if isinstance(payload, dict) else ""
        configured_username = str(server.admin_config.get("username", ""))
        local_hash = server._admin_password_hash()

        is_local_username = (
            isinstance(username, str)
            and bool(configured_username)
            and hmac.compare_digest(username, configured_username)
        )

        if is_local_username and local_hash:
            client = scope.get("client")
            client_key = client[0] if client else "unknown"
            retry_after = server.login_limiter.retry_after(client_key)
            if retry_after:
                response = JSONResponse(
                    {"detail": f"Too many failed login attempts; try again in {retry_after} seconds"},
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )
                await response(scope, receive, send)
                return

            if not isinstance(password, str) or len(password) > 4096:
                password_ok = False
            else:
                password_ok = await asyncio.to_thread(
                    server.verify_password, password, local_hash
                )

            if not password_ok:
                server.login_limiter.record_failure(client_key)
                server.logger.warning(
                    "Dashboard break-glass local admin login failed from %s", client_key
                )
                response = JSONResponse(
                    {"detail": "Invalid username or password"}, status_code=401
                )
                await response(scope, receive, send)
                return

            server.login_limiter.record_success(client_key)
            response = server._admin_login_response(configured_username)
            server.logger.warning(
                "Dashboard break-glass local admin login succeeded from %s", client_key
            )
            await response(scope, receive, send)
            return

        replayed = False

        async def replay_receive():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": raw_body, "more_body": False}
            return {"type": "http.request", "body": b"", "more_body": False}

        await self.app(scope, replay_receive, send)


app = BreakGlassAdminMiddleware(server.app)
