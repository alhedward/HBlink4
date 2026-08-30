from hblink4.parrot import ParrotSettings


def test_voice_telemetry_defaults_off_without_affecting_parrot():
    settings = ParrotSettings.from_config(
        {
            "parrot": {
                "enabled": True,
                "talkgroup": 9990,
            }
        }
    )

    assert settings.enabled is True
    assert settings.voice_telemetry.enabled is False
    assert settings.voice_telemetry.source_id == 9990
    assert settings.voice_telemetry.pause_after_echo_seconds == 0.45


def test_voice_telemetry_can_be_enabled_with_explicit_source_and_pause():
    settings = ParrotSettings.from_config(
        {
            "parrot": {
                "enabled": True,
                "talkgroup": 9990,
                "voice_telemetry_enabled": True,
                "voice_telemetry_source_id": 5059990,
                "voice_telemetry_pause_seconds": 0.6,
            }
        }
    )

    assert settings.voice_telemetry.enabled is True
    assert settings.voice_telemetry.source_id == 5059990
    assert settings.voice_telemetry.source_id_bytes == (5059990).to_bytes(3, "big")
    assert settings.voice_telemetry.pause_after_echo_seconds == 0.6
