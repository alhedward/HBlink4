import json
from pathlib import Path

import pytest

from dashboard.admin import StaleConfigError, TalkgroupConfigError, TalkgroupConfigStore
from dashboard.parrot_admin import ParrotVoiceTelemetryStore


def _write_config(path: Path, *, enabled=True):
    config = {
        "global": {"bind_ipv4": "0.0.0.0", "port_ipv4": 62031},
        "repeater_configurations": {"patterns": [], "default": {}},
        "parrot": {
            "enabled": True,
            "talkgroup": 9990,
            "delay_seconds": 2.0,
            "packet_interval_seconds": 0.06,
            "max_duration_seconds": 120.0,
            "voice_telemetry_enabled": enabled,
            "voice_telemetry_source_id": 9990,
            "voice_telemetry_pause_seconds": 0.45,
        },
        "unrelated": {"preserve": [1, 2, 3]},
    }
    path.write_text(json.dumps(config, indent=4) + "\n", encoding="utf-8")
    return config


def test_load_reports_safe_parrot_voice_fields(tmp_path):
    path = tmp_path / "config.json"
    _write_config(path, enabled=True)
    store = ParrotVoiceTelemetryStore(TalkgroupConfigStore(path))

    result = store.load()

    assert result["parrot_enabled"] is True
    assert result["talkgroup"] == 9990
    assert result["voice_telemetry_enabled"] is True
    assert result["voice_telemetry_source_id"] == 9990
    assert result["voice_telemetry_pause_seconds"] == 0.45
    assert len(result["revision"]) == 64


def test_save_changes_only_voice_switch_and_creates_backup(tmp_path):
    path = tmp_path / "config.json"
    original = _write_config(path, enabled=True)
    store = ParrotVoiceTelemetryStore(TalkgroupConfigStore(path, backup_on_save=True))
    before = store.load()

    saved = store.save(expected_revision=before["revision"], enabled=False)

    current = json.loads(path.read_text(encoding="utf-8"))
    expected = json.loads(json.dumps(original))
    expected["parrot"]["voice_telemetry_enabled"] = False
    assert current == expected
    assert saved["voice_telemetry_enabled"] is False
    assert saved["revision"] != before["revision"]

    backup = path.with_suffix(".json.bak")
    assert json.loads(backup.read_text(encoding="utf-8")) == original


def test_save_rejects_stale_revision(tmp_path):
    path = tmp_path / "config.json"
    _write_config(path)
    store = ParrotVoiceTelemetryStore(TalkgroupConfigStore(path))

    with pytest.raises(StaleConfigError):
        store.save(expected_revision="0" * 64, enabled=False)


def test_save_rejects_non_boolean_enabled(tmp_path):
    path = tmp_path / "config.json"
    _write_config(path)
    store = ParrotVoiceTelemetryStore(TalkgroupConfigStore(path))
    revision = store.load()["revision"]

    with pytest.raises(TalkgroupConfigError):
        store.save(expected_revision=revision, enabled=1)


def test_load_rejects_missing_parrot_object(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "global": {"bind_ipv4": "0.0.0.0", "port_ipv4": 62031},
                "repeater_configurations": {"patterns": [], "default": {}},
            }
        ),
        encoding="utf-8",
    )
    store = ParrotVoiceTelemetryStore(TalkgroupConfigStore(path))

    with pytest.raises(TalkgroupConfigError):
        store.load()
