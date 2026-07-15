#!/usr/bin/env python3
"""Tests for HomeBrew DMRD BER/RSSI parsing and stream aggregation."""

from time import time

import pytest

from hblink4.hblink import HBProtocol
from hblink4.models import DMR_VOICE_PROTECTED_BITS, StreamState
from hblink4.protocol import parse_dmr_packet


def make_dmrd(*, ber_errors: int = 0, rssi_magnitude: int = 0,
              frame_type: int = 1) -> bytes:
    """Build a minimal 55-byte DMRD packet for parser tests."""
    packet = bytearray(55)
    packet[0:4] = b"DMRD"
    packet[4] = 7
    packet[5:8] = (5051234).to_bytes(3, "big")
    packet[8:11] = (777).to_bytes(3, "big")
    packet[11:15] = (5050001).to_bytes(4, "big")
    packet[15] = (frame_type & 0x03) << 4
    packet[16:20] = b"\x12\x34\x56\x78"
    packet[53] = ber_errors
    packet[54] = rssi_magnitude
    return bytes(packet)


def make_stream() -> StreamState:
    now = time()
    return StreamState(
        repeater_id=(5050001).to_bytes(4, "big"),
        rf_src=(5051234).to_bytes(3, "big"),
        dst_id=(777).to_bytes(3, "big"),
        slot=2,
        start_time=now,
        last_seen=now,
        stream_id=b"\x12\x34\x56\x78",
    )


def test_parse_dmrd_extracts_ber_and_rssi():
    parsed = parse_dmr_packet(make_dmrd(ber_errors=3, rssi_magnitude=71))

    assert parsed is not None
    assert parsed["ber_errors"] == 3
    assert parsed["rssi_raw"] == 71
    assert parsed["rssi_dbm"] == -71


def test_parse_dmrd_zero_rssi_means_not_reported():
    parsed = parse_dmr_packet(make_dmrd(ber_errors=0, rssi_magnitude=0))

    assert parsed is not None
    assert parsed["ber_errors"] == 0  # zero BER is a valid perfect voice sample
    assert parsed["rssi_dbm"] is None


def test_stream_aggregates_rf_quality():
    stream = make_stream()
    stream.add_rf_quality_sample(0, -70)
    stream.add_rf_quality_sample(3, -74)
    stream.add_rf_quality_sample(6, -68)

    quality = stream.get_rf_quality()

    assert quality is not None
    assert quality["ber_average_percent"] == pytest.approx(
        9 * 100 / (3 * DMR_VOICE_PROTECTED_BITS), abs=0.01
    )
    assert quality["ber_peak_percent"] == pytest.approx(
        6 * 100 / DMR_VOICE_PROTECTED_BITS, abs=0.01
    )
    assert quality["ber_samples"] == 3
    assert quality["rssi_average_dbm"] == pytest.approx(-70.7, abs=0.1)
    assert quality["rssi_min_dbm"] == -74
    assert quality["rssi_max_dbm"] == -68
    assert quality["rssi_samples"] == 3


def test_stream_ignores_malformed_quality_values():
    stream = make_stream()
    stream.add_rf_quality_sample(DMR_VOICE_PROTECTED_BITS + 1, 0)
    stream.add_rf_quality_sample(-1, -300)

    assert stream.get_rf_quality() is None


class CaptureEvents:
    def __init__(self):
        self.events = []

    def emit(self, event_type, data):
        self.events.append((event_type, data))


def test_stream_end_event_contains_rf_quality():
    protocol = object.__new__(HBProtocol)
    protocol._events = CaptureEvents()
    stream = make_stream()
    stream.packet_count = 2
    stream.add_rf_quality_sample(1, -70)
    stream.add_rf_quality_sample(2, -72)

    protocol._emit_stream_end(
        "repeater", 5050001, 2, stream, "terminator"
    )

    event_type, event_data = protocol._events.events[-1]
    assert event_type == "stream_end"
    assert event_data["rf_quality"]["ber_samples"] == 2
    assert event_data["rf_quality"]["rssi_average_dbm"] == -71.0
