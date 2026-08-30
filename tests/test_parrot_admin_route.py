import asyncio
import json
from pathlib import Path

from dashboard import admin_app, server
from dashboard.admin import TalkgroupConfigStore


ROUTE = "/api/admin/local-services/parrot/voice-telemetry"


def _write_config(path: Path):
    path.write_text(
        json.dumps(
            {
                "global": {"bind_ipv4": "0.0.0.0", "port_ipv4": 62031},
                "repeater_configurations": {"patterns": [], "default": {}},
                "parrot": {
                    "enabled": True,
                    "talkgroup": 9990,
                    "voice_telemetry_enabled": True,
                    "voice_telemetry_source_id": 9990,
                    "voice_telemetry_pause_seconds": 0.45,
                },
            },
            indent=4,
        )
        + "\n",
        encoding="utf-8",
    )


def _invoke(app, *, method="GET", headers=None, body=b""):
    sent = []
    delivered = False

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": method,
        "path": ROUTE,
        "headers": headers or [],
        "client": ("127.0.0.1", 12345),
    }
    asyncio.run(app(scope, receive, send))
    start = next(message for message in sent if message["type"] == "http.response.start")
    payload = b"".join(
        message.get("body", b"")
        for message in sent
        if message["type"] == "http.response.body"
    )
    return start["status"], json.loads(payload or b"{}")


def _auth_headers(token, csrf=None):
    headers = [(b"cookie", f"{server.ADMIN_COOKIE_NAME}={token}".encode("ascii"))]
    if csrf:
        headers.append((b"x-csrf-token", csrf.encode("ascii")))
    return headers


def test_parrot_voice_route_requires_admin_session(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    _write_config(config_path)
    monkeypatch.setattr(server, "_talkgroup_store", lambda: TalkgroupConfigStore(config_path))
    middleware = admin_app.AdminIdentityMiddleware(lambda scope, receive, send: None)

    status, body = _invoke(middleware)

    assert status == 401
    assert "Administrator login required" in body["detail"]


def test_parrot_voice_route_reads_and_safely_updates_switch(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    _write_config(config_path)
    monkeypatch.setattr(
        server,
        "_talkgroup_store",
        lambda: TalkgroupConfigStore(config_path, backup_on_save=True),
    )
    middleware = admin_app.AdminIdentityMiddleware(lambda scope, receive, send: None)
    token, session = server.admin_sessions.create("test-admin")
    try:
        status, current = _invoke(middleware, headers=_auth_headers(token))
        assert status == 200
        assert current["voice_telemetry_enabled"] is True

        payload = json.dumps({"revision": current["revision"], "enabled": False}).encode("utf-8")
        status, body = _invoke(
            middleware,
            method="PUT",
            headers=_auth_headers(token, session.csrf_token),
            body=payload,
        )
        assert status == 428
        assert "backup" in body["detail"].lower()

        session.config_backup_revision = current["revision"]
        session.config_backup_confirmed = True
        status, body = _invoke(
            middleware,
            method="PUT",
            headers=_auth_headers(token, session.csrf_token),
            body=payload,
        )
        assert status == 200
        assert body["restart_required"] is True
        assert body["configuration"]["voice_telemetry_enabled"] is False
        assert json.loads(config_path.read_text(encoding="utf-8"))["parrot"]["voice_telemetry_enabled"] is False
        assert config_path.with_suffix(".json.bak").exists()
    finally:
        server.admin_sessions.destroy(token)


def test_parrot_voice_route_rejects_bad_csrf(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    _write_config(config_path)
    monkeypatch.setattr(server, "_talkgroup_store", lambda: TalkgroupConfigStore(config_path))
    middleware = admin_app.AdminIdentityMiddleware(lambda scope, receive, send: None)
    token, session = server.admin_sessions.create("test-admin")
    session.config_backup_confirmed = True
    try:
        current_status, current = _invoke(middleware, headers=_auth_headers(token))
        assert current_status == 200
        payload = json.dumps({"revision": current["revision"], "enabled": False}).encode("utf-8")
        status, body = _invoke(
            middleware,
            method="PUT",
            headers=_auth_headers(token, "wrong-token"),
            body=payload,
        )
        assert status == 403
        assert "CSRF" in body["detail"]
    finally:
        server.admin_sessions.destroy(token)
