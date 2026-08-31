from dmr_utils3.const import EMB, SLOT_TYPE

from hblink4.lc import decode_lc_from_vhead
from hblink4.parrot_ambe import (
    repair_legacy_opendmr_frame,
    repair_legacy_opendmr_parameter_packing,
    validate_canonical_frame,
)
from hblink4.parrot_voice import (
    _attenuate_pcm,
    _emb_bits,
    _interleave_ambe_frame,
    _slot_type_bits,
    assemble_ambe_frames,
    build_telemetry_packets,
    extract_colour_code,
    telemetry_tokens,
)


RID = (5050001).to_bytes(4, "big")
TG9990 = (9990).to_bytes(3, "big")
VOICE_SRC = (9990).to_bytes(3, "big")
STREAM = b"\x9a\xbc\xde\xf0"


def test_cc1_fec_generation_matches_dmr_utils_reference_constants():
    assert _slot_type_bits(1, 1) == SLOT_TYPE["VOICE_LC_HEAD"]
    assert _slot_type_bits(1, 2) == SLOT_TYPE["VOICE_LC_TERM"]
    assert _emb_bits(1, 1) == EMB["BURST_B"]
    assert _emb_bits(1, 3) == EMB["BURST_C"]
    assert _emb_bits(1, 3) == EMB["BURST_D"]
    assert _emb_bits(1, 2) == EMB["BURST_E"]
    assert _emb_bits(1, 0) == EMB["BURST_F"]


def test_opendmr_canonical_silence_converts_to_dmr_reference_frame():
    canonical = bytes.fromhex("f002920e0b20000000")
    validate_canonical_frame(canonical)
    assert _interleave_ambe_frame(canonical) == bytes.fromhex("acaa40200044408080")


def test_legacy_opendmr_b_block_is_losslessly_repaired():
    legacy = bytes.fromhex("1230acbe6856000000")
    corrected = bytes.fromhex("1230acbe4168000000")
    assert repair_legacy_opendmr_frame(legacy) == corrected
    validate_canonical_frame(corrected)


def test_production_capture_49bit_parameter_packing_is_losslessly_repaired():
    legacy = bytes.fromhex("b40930d10843ce4769")
    corrected = bytes.fromhex("b0357c6213d29f05c5")
    assert repair_legacy_opendmr_parameter_packing(legacy) == corrected
    validate_canonical_frame(corrected)
    assert _interleave_ambe_frame(corrected) == bytes.fromhex("d5ee220741b9680b2f")


def test_telemetry_tokens_end_at_timeslot_without_73s():
    tokens = telemetry_tokens(
        {"ber_average_percent": 0.4, "rssi_average_dbm": -72.2},
        2,
    )
    assert tokens == [
        "bit_error_rate",
        "number_0",
        "point",
        "number_4",
        "percent",
        "received_signal_strength_indication",
        "minus",
        "number_72",
        "dbm",
        "timeslot",
        "number_2",
    ]


def test_low_ber_retains_two_decimal_resolution_and_missing_metrics_are_spoken():
    assert telemetry_tokens({"ber_average_percent": 0.04}, 1)[:5] == [
        "bit_error_rate",
        "number_0",
        "point",
        "number_0",
        "number_4",
    ]
    assert telemetry_tokens({}, 1) == [
        "bit_error_rate",
        "unavailable",
        "received_signal_strength_indication",
        "unavailable",
        "timeslot",
        "number_1",
    ]


def test_pcm_attenuation_is_six_db_by_default_scale():
    import struct
    pcm = struct.pack("<160h", *([10000] * 160))
    adjusted = struct.unpack("<160h", _attenuate_pcm(pcm, 6.0))
    assert 5000 <= adjusted[0] <= 5025
    assert len(set(adjusted)) == 1


def test_generated_call_is_valid_homebrew_shape_and_preserves_cc_slot_and_lc():
    tokens = telemetry_tokens(
        {"ber_average_percent": 0.4, "rssi_average_dbm": -72.0},
        2,
    )
    canonical_silence = bytes.fromhex("f002920e0b20000000")
    assets = {token: canonical_silence for token in set(tokens)}

    emitted_tokens, packets = build_telemetry_packets(
        rf_quality={"ber_average_percent": 0.4, "rssi_average_dbm": -72.0},
        slot=2,
        dst_id=TG9990,
        repeater_id=RID,
        colour_code=7,
        source_id=VOICE_SRC,
        assets=assets,
        stream_id=STREAM,
    )

    assert emitted_tokens == tokens
    assert len(packets) > 5
    assert all(len(packet) == 55 for packet in packets)
    assert all(packet[:4] == b"DMRD" for packet in packets)
    assert all(packet[5:8] == VOICE_SRC for packet in packets)
    assert all(packet[8:11] == TG9990 for packet in packets)
    assert all(packet[11:15] == RID for packet in packets)
    assert all(packet[16:20] == STREAM for packet in packets)
    assert all(packet[15] & 0x80 for packet in packets)

    for packet in packets[:3]:
        assert ((packet[15] & 0x30) >> 4, packet[15] & 0x0F) == (2, 1)
    assert ((packets[-1][15] & 0x30) >> 4, packets[-1][15] & 0x0F) == (2, 2)

    assert extract_colour_code(packets) == 7
    assert decode_lc_from_vhead(packets[0][20:53]) == b"\x00\x00\x20" + TG9990 + VOICE_SRC


def test_ambe_assets_must_be_whole_nine_byte_frames():
    try:
        assemble_ambe_frames(["bad"], {"bad": b"123"})
    except ValueError as exc:
        assert "9-byte" in str(exc)
    else:
        raise AssertionError("invalid AMBE asset should have been rejected")
