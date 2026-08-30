import asyncio
from pathlib import Path
from types import SimpleNamespace

from dashboard import admin_app, server
from hblink4.parrot import (
    ParrotRecording,
    ParrotSettings,
    parrot_activity_payload,
)


ROOT = Path(__file__).parents[1]
RID = (5050001).to_bytes(4, "big")
SRC = (5051234).to_bytes(3, "big")
TG9990 = (9990).to_bytes(3, "big")
STREAM = b"\x12\x34\x56\x78"


def test_parrot_activity_payload_reuses_stream_rf_quality_without_packet_contents():
    settings = ParrotSettings(enabled=True, talkgroup=9990)
    recording = ParrotRecording(
        repeater_id=RID,
        slot=2,
        rf_src=SRC,
        dst_id=TG9990,
        stream_id=STREAM,
        started_at=100.0,
        packets=[b"secret-packet-one", b"secret-packet-two"],
    )
    quality = {
        "ber_average_percent": 0.71,
        "ber_peak_percent": 2.13,
        "ber_samples": 60,
        "rssi_average_dbm": -71.5,
        "rssi_min_dbm": -78,
        "rssi_max_dbm": -67,
        "rssi_samples": 60,
    }
    stream = SimpleNamespace(
        packet_count=120,
        start_time=1000.0,
        end_time=1004.8,
        get_rf_quality=lambda: quality,
    )

    payload = parrot_activity_payload(settings, recording, stream)

    assert payload == {
        "repeater_id": 5050001,
        "slot": 2,
        "src_id": 5051234,
        "talkgroup": 9990,
        "stream_id": "12345678",
        "packet_count": 120,
        "duration": 4.8,
        "rf_quality": quality,
    }
    assert "secret-packet" not in repr(payload)


def test_parrot_core_emits_distinct_non_routing_lifecycle_events():
    source = (ROOT / "hblink4" / "parrot.py").read_text()

    for event_type in (
        "parrot_recording_started",
        "parrot_recording_complete",
        "parrot_playback_started",
        "parrot_playback_complete",
        "parrot_recording_discarded",
        "parrot_playback_cancelled",
    ):
        assert event_type in source

    assert "get_rf_quality" in source
    assert '_emit_parrot_event(self, "parrot_playback_started", activity)' in source
    assert '_emit_parrot_event(self, "parrot_playback_complete", activity)' in source


def test_dashboard_receiver_forwards_parrot_events_unchanged():
    receiver = server.EventReceiver()
    observed = []

    async def capture(event):
        observed.append(event)

    receiver.send_to_clients = capture
    event = {
        "type": "parrot_playback_started",
        "timestamp": 1234.5,
        "data": {
            "repeater_id": 5050001,
            "slot": 2,
            "src_id": 5051234,
            "talkgroup": 9990,
            "stream_id": "12345678",
        },
    }

    asyncio.run(receiver.handle_event(event))

    assert observed == [event]


def test_local_services_ui_tracks_parrot_lifecycle_on_dashboard_socket():
    source = (ROOT / "dashboard" / "static" / "local_services.js").read_text()

    assert "attachSharedDashboardSocket" in source
    assert "sharedSocket.addEventListener('message', handleSocketMessage)" in source
    assert "new WebSocket" in source  # admin-page fallback
    assert "normaliseActivityData" in source
    assert "stream_update" in source
    assert "parrot_recording_started" in source
    assert "parrot_recording_complete" in source
    assert "parrot_playback_started" in source
    assert "parrot_playback_complete" in source
    assert "parrot_playback_cancelled" in source
    assert "RSSI ${quality.rssi_average_dbm} dBm avg" in source
    assert "BER ${quality.ber_average_percent}% avg" in source
    assert "`TS${activity.slot}`" in source
    assert "intentionally not in the routed talkgroup lists" in source
    assert "Recording" in source
    assert "Preparing playback" in source
    assert "Playing back" in source
    assert "Test complete" in source


def test_composed_dashboard_release_version_is_current():
    assert admin_app._DASHBOARD_VERSION == "1.3.0"
    assert server.DASHBOARD_VERSION == "1.3.0"
    assert server.app.version == "1.3.0"
    assert admin_app._ADMIN_ASSET_VERSION == "20260830-parrot-live-2"
