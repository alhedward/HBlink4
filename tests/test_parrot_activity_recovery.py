import asyncio
from pathlib import Path

from dashboard import parrot_activity_state as activity_state


ROOT = Path(__file__).parents[1]


def setup_function():
    activity_state._activity_by_talkgroup.clear()


def test_parrot_lifecycle_state_preserves_timeslot_and_safe_rf_metrics():
    event = {
        "type": "parrot_playback_started",
        "timestamp": 1234.5,
        "data": {
            "talkgroup": 9990,
            "slot": 2,
            "src_id": 5051234,
            "repeater_id": 5050001,
            "stream_id": "12345678",
            "packet_count": 80,
            "duration": 3.2,
            "rf_quality": {
                "ber_average_percent": 0.7,
                "rssi_average_dbm": -72,
                "unexpected": "drop-me",
            },
            "packet": "never expose packet contents",
            "passphrase": "never expose credentials",
        },
    }

    activity_state._record(event)
    activity = activity_state.get_activity(9990)

    assert activity["phase"] == "playback"
    assert activity["timestamp"] == 1234.5
    assert activity["data"]["slot"] == 2
    assert activity["data"]["src_id"] == 5051234
    assert activity["data"]["repeater_id"] == 5050001
    assert activity["data"]["rf_quality"] == {
        "ber_average_percent": 0.7,
        "rssi_average_dbm": -72,
    }
    assert "packet" not in activity["data"]
    assert "passphrase" not in activity["data"]


def test_local_hotspot_stream_is_a_recording_fallback_but_assumed_tx_is_not():
    activity_state._record(
        {
            "type": "stream_start",
            "timestamp": 2000.0,
            "data": {
                "connection_type": "hotspot",
                "dst_id": 9990,
                "slot": 1,
                "rf_src": 5052222,
                "repeater_id": 5050002,
                "is_assumed": False,
            },
        }
    )
    activity = activity_state.get_activity(9990)
    assert activity["phase"] == "recording"
    assert activity["data"]["slot"] == 1
    assert activity["data"]["src_id"] == 5052222

    activity_state._activity_by_talkgroup.clear()
    activity_state._record(
        {
            "type": "stream_start",
            "timestamp": 2001.0,
            "data": {
                "connection_type": "repeater",
                "dst_id": 9990,
                "slot": 2,
                "is_assumed": True,
            },
        }
    )
    assert activity_state.get_activity(9990) is None


def test_external_trunk_stream_does_not_masquerade_as_local_parrot():
    for connection_type in ("outbound", "openbridge", "network"):
        activity_state._record(
            {
                "type": "stream_start",
                "timestamp": 3000.0,
                "data": {
                    "connection_type": connection_type,
                    "dst_id": 9990,
                    "slot": 2,
                },
            }
        )
    assert activity_state.get_activity(9990) is None


def test_html_middleware_injects_polling_script_without_buffering_json():
    async def html_app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/html; charset=utf-8"),
                    (b"content-length", b"31"),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"<html><body>ok</body></html>",
                "more_body": False,
            }
        )

    sent = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    middleware = activity_state.ParrotActivityScriptMiddleware(html_app, "asset-1")
    asyncio.run(middleware({"type": "http", "path": "/"}, receive, send))

    assert sent[0]["type"] == "http.response.start"
    assert b"parrot_activity_poll.js?v=asset-1" in sent[1]["body"]

    forwarded = []

    async def json_app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        forwarded.append("start-returned")
        await send(
            {
                "type": "http.response.body",
                "body": b'{"ok":true}',
                "more_body": False,
            }
        )

    json_sent = []

    async def json_send(message):
        json_sent.append(message)
        if message["type"] == "http.response.start":
            forwarded.append("start-sent")

    middleware = activity_state.ParrotActivityScriptMiddleware(json_app, "asset-1")
    asyncio.run(middleware({"type": "http", "path": "/api/test"}, receive, json_send))

    assert forwarded[:2] == ["start-sent", "start-returned"]
    assert json_sent[1]["body"] == b'{"ok":true}'


def test_polling_asset_reports_explicit_timeslot_and_release_bump():
    polling = (ROOT / "dashboard" / "static" / "parrot_activity_poll.js").read_text()
    launcher = (ROOT / "run_dashboard.py").read_text()
    state_source = (ROOT / "dashboard" / "parrot_activity_state.py").read_text()

    assert "`TS${data.slot}`" in polling
    assert "/api/local-services/activity" in polling
    assert "POLL_MS = 750" in polling
    assert "dashboard_version = \"1.3.1\"" in state_source
    assert "20260830-parrot-live-3" in state_source
    assert "install_parrot_activity_state(admin_app)" in launcher
