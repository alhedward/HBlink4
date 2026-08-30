"""Narrow administrator editor for the TG9990 spoken telemetry toggle.

This intentionally exposes only ``parrot.voice_telemetry_enabled``.  All other
HBlink4 configuration values are carried through unchanged and the existing
TalkgroupConfigStore atomic write/backup implementation is reused.
"""

from __future__ import annotations

import hmac
import json
from typing import Any

from .admin import TalkgroupConfigStore


MAX_DMR_ID = 0xFFFFFF


class ParrotVoiceConfigError(RuntimeError):
    """Raised when the parrot voice configuration cannot be read or changed."""


class StaleParrotVoiceConfigError(ParrotVoiceConfigError):
    """Raised when the live HBlink4 config changed after the admin loaded it."""


def _dmr_id(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ParrotVoiceConfigError(f"{field_name} must be an integer DMR ID")
    if value < 1 or value > MAX_DMR_ID:
        raise ParrotVoiceConfigError(f"{field_name} is outside the DMR ID range")
    return value


class ParrotVoiceConfigStore:
    """Read/write only the optional spoken telemetry enable flag."""

    def __init__(self, talkgroup_store: TalkgroupConfigStore):
        self.talkgroup_store = talkgroup_store

    @staticmethod
    def _projection(config: dict, revision: str) -> dict:
        parrot = config.get("parrot")
        if not isinstance(parrot, dict):
            raise ParrotVoiceConfigError("HBlink4 config has no parrot object")

        parrot_enabled = parrot.get("enabled")
        if not isinstance(parrot_enabled, bool):
            raise ParrotVoiceConfigError("parrot.enabled must be true or false")

        talkgroup = _dmr_id(parrot.get("talkgroup"), "parrot.talkgroup")
        voice_enabled = parrot.get("voice_telemetry_enabled", False)
        if not isinstance(voice_enabled, bool):
            raise ParrotVoiceConfigError(
                "parrot.voice_telemetry_enabled must be true or false"
            )

        source_id = _dmr_id(
            parrot.get("voice_telemetry_source_id", talkgroup),
            "parrot.voice_telemetry_source_id",
        )
        pause = parrot.get("voice_telemetry_pause_seconds", 0.45)
        if isinstance(pause, bool) or not isinstance(pause, (int, float)):
            raise ParrotVoiceConfigError(
                "parrot.voice_telemetry_pause_seconds must be numeric"
            )
        pause = float(pause)
        if not 0.0 <= pause <= 5.0:
            raise ParrotVoiceConfigError(
                "parrot.voice_telemetry_pause_seconds must be between 0 and 5"
            )

        return {
            "revision": revision,
            "parrot_enabled": parrot_enabled,
            "talkgroup": talkgroup,
            "voice_telemetry_enabled": voice_enabled,
            "voice_telemetry_source_id": source_id,
            "voice_telemetry_pause_seconds": pause,
        }

    def load(self) -> dict:
        _raw, config, revision, _section_key = self.talkgroup_store._read()
        return self._projection(config, revision)

    def save_enabled(self, expected_revision: str, enabled: bool) -> dict:
        raw, config, current_revision, _section_key = self.talkgroup_store._read()
        if not isinstance(expected_revision, str) or not expected_revision:
            raise ParrotVoiceConfigError("Missing configuration revision")
        if not hmac.compare_digest(current_revision, expected_revision):
            raise StaleParrotVoiceConfigError(
                "HBlink4 config changed after this control loaded; reload before applying"
            )
        if not isinstance(enabled, bool):
            raise ParrotVoiceConfigError(
                "voice_telemetry_enabled must be true or false"
            )

        # Validate the complete managed projection before mutating anything.
        self._projection(config, current_revision)
        parrot = config["parrot"]
        parrot["voice_telemetry_enabled"] = enabled

        rendered = (json.dumps(config, indent=4) + "\n").encode("utf-8")
        self.talkgroup_store._atomic_replace(raw, rendered)
        return self.load()
