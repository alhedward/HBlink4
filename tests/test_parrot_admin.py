import asyncio
import json

import pytest

from dashboard import admin_app, server
from dashboard.admin import AdminSessionManager, TalkgroupConfigStore
from dashboard.parrot_admin import (
    ParrotVoiceConfigError,
    ParrotVoiceConfigStore,
    StaleParrotVoiceConfigError,
)


def sample_config():
    return {
        "global": {"bind_ipv4": "0.0.0.0", "port_ipv4": 62031},
        "repeater_configurations": {
            "patterns": [],
            "default": {
                "passphrase": "do-not-change",
                "slot1_talkgroups": [8, 777],
                "slot2_talkgroups": [8, 777],
                "default_unit_calls": False,
            },
        },
        "parrot": {
            "enabled": True,
            "talkgroup": 9990,
            "delay_seconds": 2.0,
            "packet_interval_seconds": 0.06,
            "max_duration_seconds": 120.0,
            "voice_telemetry_enabled": True,
            "voice_telemetry_source_id": 9990,
            "voice_telemetry_pause_seconds": 0.45,
        },
        "unrelated": {"secret": "preserve-me"},
    }


def write_config(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(sample_config(), indent=4) + "\n", encoding="utf-8")
    return path


def test_parrot_voice_store_changes_only_voice_settings_and_backs_up(tmp_path):
    path = write_config(tmp_path)
    original = json.loads(path.read_text(encoding="utf-8"))
    store = ParrotVoiceConfigStore(TalkgroupConfigStore(path, backup_on_save=True))

    loaded = store.load()
    assert loaded == {
        "revision": loaded["revision"],
        "parrot_enabled": True,
        "talkgroup": 9990,
        "voice_telemetry_enabled": True,
        "voice_telemetry_source_id": 9990,
        "voice_telemetry_pause_seconds": 0.45,
        "voice_telemetry_attenuation_db": 6.0,
    }
    assert "passphrase" not in json.dumps(loaded)
    assert "preserve-me" not in json.dumps(loaded)

    saved = store.save_settings(loaded["revision"], False, 7.5)
    assert saved["voice_telemetry_enabled"] is False
    assert saved["voice_telemetry_attenuation_db"] == 7.5

    after = json.loads(path.read_text(encoding="utf-8"))
    expected = json.loads(json.dumps(original))
    expected["parrot"]["voice_telemetry_enabled"] = False
    expected["parrot"]["voice_telemetry_attenuation_db"] = 7.5
    assert after == expected
    assert json.loads(path.with_suffix(".json.bak").read_text(encoding="utf-8")) == original


def test_parrot_voice_store_rejects_stale_revision(tmp_path):
    path = write_config(tmp_path)
    store = ParrotVoiceConfigStore(TalkgroupConfigStore(path))
    loaded = store.load()
    disk = json.loads(path.read_text(encoding="utf-8"))
    disk["external_change"] = True
    path.write_text(json.dumps(disk, indent=4) + "\n", encoding="utf-8")

    with pytest.raises(StaleParrotVoiceConfigError):
        store.save_enabled(loaded["revision"], False)


def test_parrot_voice_store_rejects_non_boolean_toggle(tmp_path):
    path = write_config(tmp_path)
    store = ParrotVoiceConfigStore(TalkgroupConfigStore(path))
    loaded = store.load()
    with pytest.raises(ParrotVoiceConfigError):
        store.save_enabled(loaded["revision"], "false")


def test_parrot_voice_store_allows_up_to_60_db_and_rejects_invalid_attenuation(tmp_path):
    path = write_config(tmp_path)
    store = ParrotVoiceConfigStore(TalkgroupConfigStore(path))
    loaded = store.load()
    saved = store.save_settings(loaded["revision"], True, 60.0)
    assert saved["voice_telemetry_attenuation_db"] == 60.0
    for value in (-0.5, 60.5, "6", True):
        current = store.load()
        with pytest.raises(ParrotVoiceConfigError):
            store.save_settings(current["revision"], True, value)


async def call_asgi(app, method, path, *, cookie="", csrf="", body=None):
    headers = []
    if cookie:
        headers.append((b"cookie", cookie.encode("ascii")))
    if csrf:
        headers.append((b"x-csrf-token", csrf.encode("ascii")))
    raw = b""
    if body is not None:
        raw = json.dumps(body).encode("utf-8")
        headers.append((b"content-type", b"application/json"))
        headers.append((b"content-length", str(len(raw)).encode("ascii")))

    sent_request = False

    async def receive():
        nonlocal sent_request
        if sent_request:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent_request = True
        return {"type": "http.request", "body": raw, "more_body": False}

    messages = []

    async def send(message):
        messages.append(message)

    await app(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "https",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 443),
        },
        receive,
        send,
    )
    status = next(message["status"] for message in messages if message["type"] == "http.response.start")
    response_body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    return status, response_body


def test_admin_parrot_voice_api_is_independent_of_config_backup_gate_and_still_requires_csrf(tmp_path, monkeypatch):
    path = write_config(tmp_path)
    talkgroup_store = TalkgroupConfigStore(path, backup_on_save=True)
    voice_store = ParrotVoiceConfigStore(talkgroup_store)
    initial = voice_store.load()

    sessions = AdminSessionManager(60)
    token, session = sessions.create("break-glass-admin")
    monkeypatch.setattr(server, "admin_sessions", sessions)
    monkeypatch.setattr(server, "_talkgroup_store", lambda: talkgroup_store)

    async def fallback(scope, receive, send):
        from fastapi.responses import JSONResponse
        await JSONResponse({"detail": "fallback"}, status_code=404)(scope, receive, send)

    middleware = admin_app.AdminIdentityMiddleware(fallback)
    cookie = f"{server.ADMIN_COOKIE_NAME}={token}"

    status, body = asyncio.run(call_asgi(middleware, "GET", "/api/admin/parrot-voice", cookie=cookie))
    assert status == 200
    assert json.loads(body)["voice_telemetry_attenuation_db"] == 6.0

    status, _body = asyncio.run(
        call_asgi(
            middleware, "PUT", "/api/admin/parrot-voice", cookie=cookie,
            body={"revision": initial["revision"], "voice_telemetry_enabled": False},
        )
    )
    assert status == 403

    status, body = asyncio.run(
        call_asgi(
            middleware, "PUT", "/api/admin/parrot-voice", cookie=cookie,
            csrf=session.csrf_token,
            body={
                "revision": initial["revision"],
                "voice_telemetry_enabled": False,
                "voice_telemetry_attenuation_db": 8.0,
            },
        )
    )
    assert status == 200
    payload = json.loads(body)
    assert payload["restart_required"] is True
    assert payload["configuration"]["voice_telemetry_enabled"] is False
    assert payload["configuration"]["voice_telemetry_attenuation_db"] == 8.0
    disk = json.loads(path.read_text(encoding="utf-8"))["parrot"]
    assert disk["voice_telemetry_enabled"] is False
    assert disk["voice_telemetry_attenuation_db"] == 8.0


def test_admin_page_injects_parrot_voice_control_script():
    async def fallback(scope, receive, send):
        from fastapi.responses import JSONResponse
        await JSONResponse({"detail": "fallback"}, status_code=404)(scope, receive, send)

    middleware = admin_app.AdminIdentityMiddleware(fallback)
    status, body = asyncio.run(call_asgi(middleware, "GET", "/admin"))
    assert status == 200
    html = body.decode("utf-8")
    assert "admin_parrot_voice.js?v=" in html
    assert admin_app._DASHBOARD_VERSION == "1.3.3"
