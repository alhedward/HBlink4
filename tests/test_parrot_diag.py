from hblink4 import parrot_voice
from hblink4.parrot_diag import (
    DIAGNOSTIC_TOKENS,
    fixed_telemetry_tokens,
    install_fixed_parrot_voice_report,
)


def test_fixed_report_is_exact_and_ignores_live_rf_values_and_slot():
    expected = list(DIAGNOSTIC_TOKENS)
    assert fixed_telemetry_tokens(
        {"ber_average_percent": 12.34, "rssi_average_dbm": -101.0}, 1
    ) == expected
    assert fixed_telemetry_tokens({}, 2) == expected


def test_install_only_overrides_runtime_token_selection():
    original = parrot_voice.telemetry_tokens
    try:
        install_fixed_parrot_voice_report()
        assert parrot_voice.telemetry_tokens is fixed_telemetry_tokens
        assert parrot_voice.telemetry_tokens(None, 1) == list(DIAGNOSTIC_TOKENS)
    finally:
        parrot_voice.telemetry_tokens = original
