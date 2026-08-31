from pathlib import Path

from dashboard import admin_app


ROOT = Path(__file__).parents[1]


class _FakeStore:
    def __init__(self, config):
        self.config = config

    def _read(self):
        return b"{}", self.config, "revision", "repeater_configurations"


def test_public_local_services_exposes_enabled_parrot_without_secrets(monkeypatch):
    config = {
        "repeater_configurations": {
            "default": {
                "passphrase": "must-not-leak",
                "slot1_talkgroups": [8, 91],
                "slot2_talkgroups": [8, 505],
            }
        },
        "parrot": {"enabled": True, "talkgroup": 9990, "delay_seconds": 2.0},
    }
    monkeypatch.setattr(admin_app.server, "_talkgroup_store", lambda: _FakeStore(config))
    services = admin_app._public_local_services()
    assert services == [{
        "type": "parrot",
        "name": "Parrot / Echo Test",
        "talkgroup": 9990,
        "scope": "local-only",
        "description": (
            "Records a local group call and plays it back only to the "
            "originating repeater or hotspot. It is not routed to other "
            "repeaters or external DMR networks."
        ),
    }]
    assert "must-not-leak" not in repr(services)


def test_public_local_services_hides_disabled_parrot(monkeypatch):
    monkeypatch.setattr(
        admin_app.server,
        "_talkgroup_store",
        lambda: _FakeStore({"parrot": {"enabled": False, "talkgroup": 9990}}),
    )
    assert admin_app._public_local_services() == []


def test_dashboard_and_admin_load_cache_busted_local_services_script():
    source = (
        (ROOT / "dashboard" / "admin_app.py").read_text()
        + "\n"
        + (ROOT / "dashboard" / "admin_app_impl.py").read_text()
    )
    assert 'path == "/api/local-services"' in source
    assert 'local_services.js?v={version}' in source
    assert 'Cache-Control": "no-store"' in source


def test_local_services_ui_labels_parrot_as_local_only():
    source = (ROOT / "dashboard" / "static" / "local_services.js").read_text()
    assert "Local DMR Services" in source
    assert "Parrot / Echo Test" in source
    assert "TG${parrot.talkgroup}" in source
    assert "local-only echo" in source.lower()
    assert "not routed to other repeaters or external DMR networks" in source
    assert "renderDashboardCard(parrot)" in source
    assert "renderAdminCard(parrot)" in source
