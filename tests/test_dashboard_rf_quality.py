#!/usr/bin/env python3
"""Verify RF-quality metadata reaches the dashboard Last Heard schema."""

import asyncio
from collections import deque
from types import SimpleNamespace

from dashboard import server


class FakeUserDatabase:
    def get(self, radio_id, default=""):
        return "VK2ALE" if radio_id == 5051234 else default


async def _exercise_event_flow(receiver):
    await receiver.handle_event({
        "type": "stream_start",
        "timestamp": 1000.0,
        "data": {
            "repeater_id": 5050001,
            "slot": 2,
            "src_id": 5051234,
            "dst_id": 777,
            "stream_id": "12345678",
            "call_type": "group",
            "is_assumed": False,
            "is_data": False,
        },
    })

    live_quality = {
        "ber_average_percent": 0.71,
        "ber_peak_percent": 2.13,
        "ber_samples": 60,
        "rssi_average_dbm": -71.5,
        "rssi_min_dbm": -78,
        "rssi_max_dbm": -67,
        "rssi_samples": 60,
    }
    await receiver.handle_event({
        "type": "stream_update",
        "timestamp": 1001.0,
        "data": {
            "repeater_id": 5050001,
            "slot": 2,
            "src_id": 5051234,
            "dst_id": 777,
            "duration": 1.0,
            "packets": 60,
            "call_type": "group",
            "rf_quality": live_quality,
        },
    })

    final_quality = {**live_quality, "ber_samples": 120, "rssi_samples": 120}
    await receiver.handle_event({
        "type": "stream_end",
        "timestamp": 1002.4,
        "data": {
            "repeater_id": 5050001,
            "slot": 2,
            "src_id": 5051234,
            "dst_id": 777,
            "stream_id": "12345678",
            "duration": 2.4,
            "packet_count": 120,
            "end_reason": "terminator",
            "hang_time": 10.0,
            "call_type": "group",
            "is_assumed": False,
            "is_data": False,
            "rf_quality": final_quality,
        },
    })


def test_dashboard_copies_rf_quality_to_last_heard(monkeypatch):
    fake_state = SimpleNamespace(
        repeaters={5050001: {"callsign": "VK2ALE-HS"}},
        repeater_details={},
        outbounds={},
        streams={},
        events=deque(maxlen=50),
        last_heard=[],
        websocket_clients=set(),
        stats={
            "total_calls_today": 0,
            "total_duration_today": 0.0,
            "retransmitted_calls": 0,
        },
        user_db=FakeUserDatabase(),
    )
    monkeypatch.setattr(server, "state", fake_state)

    receiver = server.EventReceiver()
    asyncio.run(_exercise_event_flow(receiver))

    assert len(fake_state.last_heard) == 1
    entry = fake_state.last_heard[0]
    assert entry["callsign"] == "VK2ALE"
    assert entry["stream_id"] == "12345678"
    assert entry["started_at"] == 1000.0
    assert entry["ended_at"] == 1002.4
    assert entry["active"] is False
    assert entry["duration"] == 2.4
    assert entry["packet_count"] == 120
    assert entry["end_reason"] == "terminator"
    assert entry["rf_quality"]["ber_average_percent"] == 0.71
    assert entry["rf_quality"]["rssi_average_dbm"] == -71.5
