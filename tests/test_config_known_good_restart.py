import asyncio
import json
from types import SimpleNamespace

from dashboard import server
from dashboard.admin import TalkgroupConfigStore


def test_successful_admin_restart_promotes_current_config_to_known_good(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "global": {"bind_ipv4": "0.0.0.0", "port_ipv4": 62031},
                "repeater_configurations": {"patterns": [], "default": {}},
                "parrot": {
                    "enabled": True,
                    "talkgroup": 9990,
                    "voice_telemetry_enabled": False,
                },
            },
            indent=4,
        )
        + "\n",
        encoding="utf-8",
    )
    store = TalkgroupConfigStore(path, backup_on_save=True)

    class FakeRestartController:
        async def restart(self):
            return {"ok": True, "status": "active"}

    monkeypatch.setattr(server, "_admin_session", lambda request: SimpleNamespace(username="admin", csrf_token="csrf"))
    monkeypatch.setattr(server, "_require_csrf", lambda request, session: None)
    monkeypatch.setattr(server, "_restart_controller", lambda: FakeRestartController())
    monkeypatch.setattr(server, "_talkgroup_store", lambda: store)
    monkeypatch.setattr(server, "admin_config", {"restart": {"enabled": True}})

    result = asyncio.run(server.admin_restart_hblink(object()))

    assert result["ok"] is True
    assert result["status"] == "active"
    assert result["known_good_recorded"] is True
    status = store.last_known_good_status()
    assert status["available"] is True
    assert status["revision"] == result["known_good_revision"]
    snapshot = path.parent / ".hblink4-config-history" / "config.json.last-known-good.json"
    assert snapshot.read_bytes() == path.read_bytes()
