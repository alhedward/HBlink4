import json
from pathlib import Path

from dashboard.admin import TalkgroupConfigStore
from dashboard.parrot_admin import ParrotVoiceTelemetryStore


def _config(enabled=True):
    return {
        "global": {"bind_ipv4": "0.0.0.0", "port_ipv4": 62031},
        "repeater_configurations": {"patterns": [], "default": {}},
        "parrot": {
            "enabled": True,
            "talkgroup": 9990,
            "voice_telemetry_enabled": enabled,
            "voice_telemetry_source_id": 9990,
            "voice_telemetry_pause_seconds": 0.45,
        },
        "operator_owned": {"keep": "this"},
    }


def _write(path: Path, config):
    path.write_text(json.dumps(config, indent=4) + "\n", encoding="utf-8")


def test_each_write_keeps_an_immutable_prechange_snapshot(tmp_path):
    path = tmp_path / "config.json"
    original = _config(True)
    _write(path, original)
    base = TalkgroupConfigStore(path, backup_on_save=True)
    voice = ParrotVoiceTelemetryStore(base)

    first = voice.load()
    voice.save(expected_revision=first["revision"], enabled=False)
    second = voice.load()
    voice.save(expected_revision=second["revision"], enabled=True)

    history = path.parent / ".hblink4-config-history"
    snapshots = sorted(history.glob("config.json.prechange.*.json"))
    assert len(snapshots) == 2
    assert json.loads(snapshots[0].read_text(encoding="utf-8")) == original
    assert json.loads(snapshots[1].read_text(encoding="utf-8"))["parrot"]["voice_telemetry_enabled"] is False
    assert all((snapshot.stat().st_mode & 0o077) == 0 for snapshot in snapshots)


def test_last_known_good_is_not_overwritten_by_later_unverified_edit(tmp_path):
    path = tmp_path / "config.json"
    _write(path, _config(True))
    base = TalkgroupConfigStore(path, backup_on_save=True)
    voice = ParrotVoiceTelemetryStore(base)

    known_good = base.mark_current_known_good()
    before = voice.load()
    voice.save(expected_revision=before["revision"], enabled=False)

    status = base.last_known_good_status()
    history = path.parent / ".hblink4-config-history"
    payload = json.loads((history / "config.json.last-known-good.json").read_text(encoding="utf-8"))

    assert status["available"] is True
    assert status["revision"] == known_good["revision"]
    assert payload["parrot"]["voice_telemetry_enabled"] is True
    assert json.loads(path.read_text(encoding="utf-8"))["parrot"]["voice_telemetry_enabled"] is False


def test_restore_last_known_good_archives_bad_current_config_first(tmp_path):
    path = tmp_path / "config.json"
    _write(path, _config(True))
    base = TalkgroupConfigStore(path, backup_on_save=True)
    voice = ParrotVoiceTelemetryStore(base)
    base.mark_current_known_good()

    current = voice.load()
    voice.save(expected_revision=current["revision"], enabled=False)
    restored = base.restore_last_known_good()

    assert restored["revision"] == base.load_for_editor()["revision"]
    assert json.loads(path.read_text(encoding="utf-8"))["parrot"]["voice_telemetry_enabled"] is True

    snapshots = list((path.parent / ".hblink4-config-history").glob("config.json.prechange.*.json"))
    assert any(
        json.loads(snapshot.read_text(encoding="utf-8"))["parrot"]["voice_telemetry_enabled"] is False
        for snapshot in snapshots
    )
