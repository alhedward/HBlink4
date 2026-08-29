import json

from dashboard import server
from dashboard.admin import AdminSessionManager, LoginRateLimiter, hash_password
from dashboard.auth_app import BreakGlassAdminMiddleware


def _scope():
    return {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/api/admin/login",
        "raw_path": b"/api/admin/login",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 443),
    }


async def _invoke(app, payload):
    raw = json.dumps(payload).encode("utf-8")
    received = False
    sent = []

    async def receive():
        nonlocal received
        if received:
            return {"type": "http.request", "body": b"", "more_body": False}
        received = True
        return {"type": "http.request", "body": raw, "more_body": False}

    async def send(message):
        sent.append(message)

    await app(_scope(), receive, send)
    status = next(item["status"] for item in sent if item["type"] == "http.response.start")
    headers = dict(next(item["headers"] for item in sent if item["type"] == "http.response.start"))
    body = b"".join(item.get("body", b"") for item in sent if item["type"] == "http.response.body")
    return status, headers, json.loads(body) if body else None


def _configure_cognito_with_local_fallback(monkeypatch):
    password_hash = hash_password("break-glass-secret", iterations=100_000)
    monkeypatch.delenv("HBLINK4_DASH_ADMIN_PASSWORD_HASH", raising=False)
    monkeypatch.setattr(
        server,
        "admin_config",
        {
            "enabled": True,
            "username": "admin",
            "password_hash": password_hash,
            "auth_provider": "cognito",
            "cookie_secure": True,
            "cognito": {
                "region": "ap-southeast-2",
                "user_pool_id": "ap-southeast-2_test",
                "client_id": "client123",
                "admin_group": "HBlink4Admins",
            },
        },
    )
    monkeypatch.setattr(server, "admin_sessions", AdminSessionManager(60))
    monkeypatch.setattr(server, "login_limiter", LoginRateLimiter())


def test_breakglass_local_admin_login_works_while_cognito_is_primary(monkeypatch):
    import asyncio

    _configure_cognito_with_local_fallback(monkeypatch)

    async def unexpected_downstream(scope, receive, send):
        raise AssertionError("reserved local username must not be sent to Cognito")

    app = BreakGlassAdminMiddleware(unexpected_downstream)
    status, headers, body = asyncio.run(
        _invoke(app, {"username": "admin", "password": "break-glass-secret"})
    )

    assert status == 200
    assert body["ok"] is True
    assert body["username"] == "admin"
    assert body["csrf_token"]
    assert b"set-cookie" in headers


def test_breakglass_wrong_password_does_not_fall_through_to_cognito(monkeypatch):
    import asyncio

    _configure_cognito_with_local_fallback(monkeypatch)

    async def unexpected_downstream(scope, receive, send):
        raise AssertionError("failed break-glass login must not fall through to Cognito")

    app = BreakGlassAdminMiddleware(unexpected_downstream)
    status, _, body = asyncio.run(
        _invoke(app, {"username": "admin", "password": "wrong"})
    )

    assert status == 401
    assert body == {"detail": "Invalid username or password"}


def test_nonlocal_username_is_replayed_unchanged_to_cognito_app(monkeypatch):
    import asyncio

    _configure_cognito_with_local_fallback(monkeypatch)
    seen = {}

    async def downstream(scope, receive, send):
        message = await receive()
        seen["payload"] = json.loads(message["body"])
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app = BreakGlassAdminMiddleware(downstream)
    payload = {"username": "tony@example.com", "password": "cognito-secret"}
    status, _, body = asyncio.run(_invoke(app, payload))

    assert status == 204
    assert body is None
    assert seen["payload"] == payload
