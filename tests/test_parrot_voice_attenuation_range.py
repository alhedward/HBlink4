import pytest

from hblink4.parrot_voice import MAX_ATTENUATION_DB, VoiceTelemetrySettings


def test_runtime_voice_telemetry_accepts_full_admin_attenuation_range():
    settings = VoiceTelemetrySettings.from_parrot_config(
        {"voice_telemetry_attenuation_db": 60.0},
        9990,
    )

    assert MAX_ATTENUATION_DB == 60.0
    assert settings.attenuation_db == 60.0


@pytest.mark.parametrize("value", [-0.5, 60.5, "60", True])
def test_runtime_voice_telemetry_rejects_invalid_attenuation(value):
    with pytest.raises(ValueError, match="voice_telemetry_attenuation_db"):
        VoiceTelemetrySettings.from_parrot_config(
            {"voice_telemetry_attenuation_db": value},
            9990,
        )
