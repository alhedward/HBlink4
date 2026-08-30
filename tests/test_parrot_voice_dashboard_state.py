from pathlib import Path

from dashboard import parrot_activity_state as activity_state


ROOT = Path(__file__).parents[1]


def setup_function():
    activity_state._activity_by_talkgroup.clear()


def test_voice_report_has_its_own_dashboard_phase_and_safe_status():
    activity_state._record(
        {
            "type": "parrot_telemetry_started",
            "timestamp": 100.0,
            "data": {
                "talkgroup": 9990,
                "slot": 2,
                "src_id": 5051234,
                "voice_telemetry_status": "playing",
                "packet": "not-public",
            },
        }
    )

    activity = activity_state.get_activity(9990)
    assert activity["phase"] == "telemetry"
    assert activity["data"]["voice_telemetry_status"] == "playing"
    assert "packet" not in activity["data"]

    activity_state._record(
        {
            "type": "parrot_playback_complete",
            "timestamp": 101.0,
            "data": {
                "talkgroup": 9990,
                "slot": 2,
                "voice_telemetry_status": "complete",
            },
        }
    )
    assert activity_state.get_activity(9990)["phase"] == "complete"


def test_websocket_ui_independently_returns_final_state_to_ready():
    source = (ROOT / "dashboard" / "static" / "local_services.js").read_text()
    assert "activity = null;" in source
    assert "renderActivity();" in source
    assert "}, 8000);" in source
    assert "['repeater', 'hotspot', 'unknown'].includes(connectionType)" in source
    assert "Voice report" in source
