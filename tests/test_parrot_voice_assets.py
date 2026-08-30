from hblink4.parrot_ambe import validate_canonical_frame
from hblink4.parrot_voice_assets import AMBE_ASSETS, ASSET_MANIFEST, OPENDMR_COMMIT


def test_bundled_parrot_voice_vocabulary_is_complete_and_frame_aligned():
    expected = {
        "bit_error_rate",
        "received_signal_strength_indication",
        "point",
        "percent",
        "minus",
        "dbm",
        "timeslot",
        "unavailable",
        "seventy_threes",
    } | {f"number_{value}" for value in range(201)}
    assert set(AMBE_ASSETS) == expected
    assert len(AMBE_ASSETS) == 210
    assert all(payload and len(payload) % 9 == 0 for payload in AMBE_ASSETS.values())
    assert ASSET_MANIFEST["frame_bytes"] == 9
    assert ASSET_MANIFEST["frame_duration_ms"] == 20
    assert ASSET_MANIFEST["opendmr_commit"] == OPENDMR_COMMIT
    assert ASSET_MANIFEST["channel_coding"]["b_block"] == (
        "golay23-right-aligned-before-prng"
    )
    for payload in AMBE_ASSETS.values():
        for offset in range(0, len(payload), 9):
            validate_canonical_frame(payload[offset:offset + 9])
    assert ASSET_MANIFEST["source_tts"]["lang"] == "en"
    assert ASSET_MANIFEST["source_tts"]["tld"] == "com.au"


def test_bundled_assets_cover_requested_example_report():
    for name in (
        "bit_error_rate", "number_0", "point", "number_4", "percent",
        "received_signal_strength_indication", "minus", "number_72", "dbm",
        "timeslot", "number_2", "seventy_threes",
    ):
        assert name in AMBE_ASSETS
