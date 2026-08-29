import asyncio
import json

import pytest

from dashboard.admin import (
    MAX_TALKGROUP_ID,
    RestartController,
    StaleConfigError,
    TalkgroupConfigError,
    TalkgroupConfigStore,
    hash_password,
    verify_password,
)
from dashboard import server


def sample_hblink_config():
    return {
        "global": {
            "bind_ipv4": "0.0.0.0",
            "port_ipv4": 62031,
        },
        "blacklist": {"patterns": []},
        "repeater_configurations": {
            "patterns": [
                {
                    "name": "Metro",
                    "description": "Metro repeaters",
                    "match": {"ids": [5050001]},
                    "config": {
                        "passphrase": "do-not-leak-or-change",
                        "slot1_talkgroups": [8, 9],
                        "slot2_talkgroups": [505, 50501],
                    },
                },
                {
                    "name": "Open",
                    "match": {"callsigns": ["VK2*"]},
                    "config": {
                        "passphrase": "another-secret",
                        "slot1_talkgroups": None,
                        "slot2_talkgroups": [],
                    },
                },
            ],
            "default": {
                "passphrase": "default-secret",
                "slot1_talkgroups": [8],
                "slot2_talkgroups": [8],
            },
        },
    }


def write_config(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(sample_hblink_config(), indent=4) + "\n")
    return path


def test_password_hash_round_trip():
    encoded = hash_password("correct horse battery staple", iterations=100_000)
    assert verify_password("correct horse battery staple", encoded)
    assert not verify_password("wrong password", encoded)
    assert not verify_password("correct horse battery staple", "not-a-valid-hash")


def test_talkgroup_store_adds_new_tgs_and_preserves_secrets(tmp_path):
    path = write_config(tmp_path)
    store = TalkgroupConfigStore(path)
    initial = store.load_for_editor()

    # The editor projection contains no repeater passphrases.
    assert "passphrase" not in json.dumps(initial)

    saved = store.save_talkgroups(
        initial["revision"],
        [
            {
                "index": 0,
                "slot1_talkgroups": [8, 9, 91, 91],
                "slot2_talkgroups": [505, 50501, 50502],
            },
            {
                "index": 1,
                "slot1_talkgroups": None,
                "slot2_talkgroups": [],
            },
        ],
        {"slot1_talkgroups": [8, 91], "slot2_talkgroups": [8, 505]},
    )

    assert saved["patterns"][0]["slot1_talkgroups"] == [8, 9, 91]
    assert saved["patterns"][0]["slot2_talkgroups"] == [505, 50501, 50502]
    assert saved["patterns"][1]["slot1_talkgroups"] is None
    assert saved["patterns"][1]["slot2_talkgroups"] == []

    on_disk = json.loads(path.read_text())
    assert on_disk["repeater_configurations"]["patterns"][0]["config"]["passphrase"] == "do-not-leak-or-change"
    assert on_disk["repeater_configurations"]["patterns"][0]["match"] == {"ids": [5050001]}
    assert on_disk["repeater_configurations"]["default"]["passphrase"] == "default-secret"

    backup = path.with_suffix(".json.bak")
    assert backup.exists()
    assert json.loads(backup.read_text()) == sample_hblink_config()


def test_full_config_backup_and_restore_preserves_complete_file(tmp_path):
    path = write_config(tmp_path)
    store = TalkgroupConfigStore(path)
    original = path.read_bytes()

    exported, revision = store.export_full_config()
    assert exported == original
    assert revision == store.load_for_editor()["revision"]
    assert b"do-not-leak-or-change" in exported

    replacement = sample_hblink_config()
    replacement["global"]["port_ipv4"] = 62099
    replacement["repeater_configurations"]["patterns"][0]["config"]["passphrase"] = "restored-secret"
    replacement_raw = (json.dumps(replacement, indent=2) + "\n").encode()

    editor = store.restore_full_config(replacement_raw)
    assert path.read_bytes() == replacement_raw
    assert path.with_suffix(".json.bak").read_bytes() == original
    assert json.loads(path.read_text())["global"]["port_ipv4"] == 62099
    assert "passphrase" not in json.dumps(editor)


def test_full_config_restore_rejects_non_hblink_json(tmp_path):
    path = write_config(tmp_path)
    store = TalkgroupConfigStore(path)
    with pytest.raises(TalkgroupConfigError):
        store.restore_full_config(b'{"not_hblink": true}\n')


def test_talkgroup_store_rejects_stale_revision(tmp_path):
    path = write_config(tmp_path)
    store = TalkgroupConfigStore(path)
    initial = store.load_for_editor()
    disk = json.loads(path.read_text())
    disk["unrelated_external_change"] = True
    path.write_text(json.dumps(disk, indent=4) + "\n")

    with pytest.raises(StaleConfigError):
        store.save_talkgroups(initial["revision"], [], None)


@pytest.mark.parametrize("bad_tg", [0, MAX_TALKGROUP_ID + 1, True, "91"])
def test_talkgroup_store_rejects_invalid_tg_ids(tmp_path, bad_tg):
    path = write_config(tmp_path)
    store = TalkgroupConfigStore(path)
    initial = store.load_for_editor()
    with pytest.raises(TalkgroupConfigError):
        store.save_talkgroups(
            initial["revision"],
            [{"index": 0, "slot1_talkgroups": [bad_tg], "slot2_talkgroups": [505]}],
            None,
        )


def test_restart_controller_uses_fixed_commands_and_verifies_active(monkeypatch):
    controller = RestartController(
        {
            "enabled": True,
            "command": ["/usr/bin/systemctl", "restart", "hblink4.service"],
            "status_command": ["/usr/bin/systemctl", "is-active", "hblink4.service"],
            "verify_attempts": 2,
            "verify_delay_seconds": 0,
        }
    )
    calls = []

    async def fake_run(command):
        calls.append(tuple(command))
        if command[1] == "restart":
            return 0, "", ""
        return 0, "active\n", ""

    monkeypatch.setattr(controller, "_run_command", fake_run)
    result = asyncio.run(controller.restart())
    assert result == {"ok": True, "status": "active"}
    assert calls == [
        ("/usr/bin/systemctl", "restart", "hblink4.service"),
        ("/usr/bin/systemctl", "is-active", "hblink4.service"),
    ]


def test_public_dashboard_config_does_not_expose_admin(monkeypatch):
    monkeypatch.setattr(
        server,
        "dashboard_config",
        {"dashboard_title": "Test", "admin": {"password_hash": "secret-hash"}},
    )
    result = asyncio.run(server.get_config())
    assert result["dashboard_title"] == "Test"
    assert "admin" not in result
    assert "password_hash" not in json.dumps(result)


def _request(path, cookie=None, csrf=None, method="POST", body=b"", content_type=None):
    from starlette.requests import Request

    headers = []
    if cookie:
        headers.append((b"cookie", cookie.encode("ascii")))
    if csrf:
        headers.append((b"x-csrf-token", csrf.encode("ascii")))
    if body:
        headers.append((b"content-length", str(len(body)).encode("ascii")))
    if content_type:
        headers.append((b"content-type", content_type.encode("ascii")))

    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        },
        receive=receive,
    )


def test_admin_api_login_requires_session_and_csrf_for_save(tmp_path, monkeypatch):
    from http.cookies import SimpleCookie
    from fastapi import HTTPException
    from dashboard.admin import AdminSessionManager, LoginRateLimiter

    path = write_config(tmp_path)
    password_hash = hash_password("dashboard-passphrase", iterations=100_000)
    monkeypatch.delenv("HBLINK4_DASH_ADMIN_PASSWORD_HASH", raising=False)
    monkeypatch.setattr(
        server,
        "admin_config",
        {
            "enabled": True,
            "username": "admin",
            "password_hash": password_hash,
            "hblink_config_path": str(path),
            "backup_on_save": True,
            "restart": {"enabled": False},
        },
    )
    monkeypatch.setattr(server, "admin_sessions", AdminSessionManager(60))
    monkeypatch.setattr(server, "login_limiter", LoginRateLimiter())

    with pytest.raises(HTTPException) as unauthenticated:
        asyncio.run(server.admin_get_talkgroups(_request("/api/admin/talkgroups")))
    assert unauthenticated.value.status_code == 401

    login_response = asyncio.run(
        server.admin_login(
            _request("/api/admin/login"),
            {"username": "admin", "password": "dashboard-passphrase"},
        )
    )
    login_body = json.loads(login_response.body)
    csrf = login_body["csrf_token"]
    cookies = SimpleCookie()
    cookies.load(login_response.headers["set-cookie"])
    token = cookies[server.ADMIN_COOKIE_NAME].value
    cookie_header = f"{server.ADMIN_COOKIE_NAME}={token}"

    with pytest.raises(HTTPException) as backup_required:
        asyncio.run(
            server.admin_get_talkgroups(_request("/api/admin/talkgroups", cookie=cookie_header))
        )
    assert backup_required.value.status_code == 428

    backup_response = asyncio.run(
        server.admin_download_config_backup(
            _request("/api/admin/config-backup", cookie=cookie_header)
        )
    )
    assert "attachment;" in backup_response.headers["content-disposition"]
    assert b"do-not-leak-or-change" in backup_response.body
    backup_revision = backup_response.headers["x-hblink4-config-revision"]

    # Starting/receiving the backup response alone does not unlock editing.
    with pytest.raises(HTTPException) as not_confirmed:
        asyncio.run(
            server.admin_get_talkgroups(_request("/api/admin/talkgroups", cookie=cookie_header))
        )
    assert not_confirmed.value.status_code == 428

    with pytest.raises(HTTPException) as confirm_missing_csrf:
        asyncio.run(
            server.admin_confirm_config_backup(
                _request("/api/admin/config-backup-confirm", cookie=cookie_header),
                {"revision": backup_revision},
            )
        )
    assert confirm_missing_csrf.value.status_code == 403

    confirm_result = asyncio.run(
        server.admin_confirm_config_backup(
            _request(
                "/api/admin/config-backup-confirm",
                cookie=cookie_header,
                csrf=csrf,
            ),
            {"revision": backup_revision},
        )
    )
    assert confirm_result == {"ok": True, "revision": backup_revision}

    editor_data = asyncio.run(
        server.admin_get_talkgroups(_request("/api/admin/talkgroups", cookie=cookie_header))
    )
    assert "passphrase" not in json.dumps(editor_data)

    editor_data["patterns"][0]["slot1_talkgroups"].append(91)
    payload = {
        "revision": editor_data["revision"],
        "patterns": [
            {
                "index": item["index"],
                "slot1_talkgroups": item["slot1_talkgroups"],
                "slot2_talkgroups": item["slot2_talkgroups"],
            }
            for item in editor_data["patterns"]
        ],
        "default": editor_data["default"],
    }

    with pytest.raises(HTTPException) as missing_csrf:
        asyncio.run(
            server.admin_save_talkgroups(
                _request("/api/admin/talkgroups", cookie=cookie_header), payload
            )
        )
    assert missing_csrf.value.status_code == 403

    result = asyncio.run(
        server.admin_save_talkgroups(
            _request("/api/admin/talkgroups", cookie=cookie_header, csrf=csrf), payload
        )
    )
    assert result["ok"] is True
    assert 91 in json.loads(path.read_text())["repeater_configurations"]["patterns"][0]["config"]["slot1_talkgroups"]

    restored_config = sample_hblink_config()
    restored_config["global"]["port_ipv4"] = 62100
    restored_raw = (json.dumps(restored_config, indent=2) + "\n").encode()
    restore_result = asyncio.run(
        server.admin_restore_config(
            _request(
                "/api/admin/config-restore",
                cookie=cookie_header,
                csrf=csrf,
                body=restored_raw,
                content_type="application/json",
            )
        )
    )
    assert restore_result["ok"] is True
    assert json.loads(path.read_text())["global"]["port_ipv4"] == 62100


def test_cognito_login_uses_existing_local_session_layer(monkeypatch):
    from http.cookies import SimpleCookie
    from types import SimpleNamespace
    from dashboard.admin import AdminSessionManager, LoginRateLimiter

    class FakeCognitoAuth:
        def authenticate(self, username, password):
            assert username == "tony@example.com"
            assert password == "secret"
            return SimpleNamespace(
                status="authenticated",
                identity=SimpleNamespace(username="cognito-tony"),
                challenge_token=None,
                required_attributes=(),
            )

    monkeypatch.setattr(
        server,
        "admin_config",
        {
            "enabled": True,
            "auth_provider": "cognito",
            "cognito": {
                "region": "ap-southeast-2",
                "user_pool_id": "ap-southeast-2_test",
                "client_id": "client123",
                "admin_group": "HBlink4Admins",
            },
            "restart": {"enabled": False},
        },
    )
    monkeypatch.setattr(server, "admin_sessions", AdminSessionManager(60))
    monkeypatch.setattr(server, "login_limiter", LoginRateLimiter())
    monkeypatch.setattr(server, "_cognito_auth", FakeCognitoAuth())

    response = asyncio.run(
        server.admin_login(
            _request("/api/admin/login"),
            {"username": "tony@example.com", "password": "secret"},
        )
    )
    body = json.loads(response.body)
    assert body["ok"] is True
    assert body["username"] == "cognito-tony"
    assert body["csrf_token"]

    cookies = SimpleCookie()
    cookies.load(response.headers["set-cookie"])
    token = cookies[server.ADMIN_COOKIE_NAME].value
    session = server.admin_sessions.get(token)
    assert session.username == "cognito-tony"
    assert session.csrf_token == body["csrf_token"]
