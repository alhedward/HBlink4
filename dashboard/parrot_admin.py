"""Protected configuration helper for local parrot voice telemetry.

This deliberately exposes only the voice_telemetry_enabled switch.  All other
HBlink4 configuration remains operator-owned and is carried through unchanged.
Writes reuse the dashboard's existing revision, backup and atomic-replace
machinery so an administrator cannot silently overwrite a concurrently changed
live configuration.
"""

from __future__ import annotations

import hmac
import json
from typing import Dict

from .admin import StaleConfigError, TalkgroupConfigError, TalkgroupConfigStore


class ParrotVoiceTelemetryStore:
    """Read and update only ``parrot.voice_telemetry_enabled``."""

    def __init__(self, config_store: TalkgroupConfigStore):
        self.config_store = config_store

    @staticmethod
    def _status(config: dict, revision: str) -> Dict[str, object]:
        parrot = config.get("parrot")
        if not isinstance(parrot, dict):
            raise TalkgroupConfigError("HBlink4 config has no parrot configuration object")

        enabled = parrot.get("voice_telemetry_enabled", False)
        if not isinstance(enabled, bool):
            raise TalkgroupConfigError("parrot.voice_telemetry_enabled must be true or false")

        parrot_enabled = parrot.get("enabled", False)
        if not isinstance(parrot_enabled, bool):
            raise TalkgroupConfigError("parrot.enabled must be true or false")

        talkgroup = parrot.get("talkgroup")
        if isinstance(talkgroup, bool) or not isinstance(talkgroup, int):
            raise TalkgroupConfigError("parrot.talkgroup must be an integer")

        source_id = parrot.get("voice_telemetry_source_id", talkgroup)
        if isinstance(source_id, bool) or not isinstance(source_id, int):
            raise TalkgroupConfigError("parrot.voice_telemetry_source_id must be an integer")

        pause = parrot.get("voice_telemetry_pause_seconds", 0.45)
        if isinstance(pause, bool) or not isinstance(pause, (int, float)):
            raise TalkgroupConfigError("parrot.voice_telemetry_pause_seconds must be numeric")

        return {
            "revision": revision,
            "parrot_enabled": parrot_enabled,
            "talkgroup": talkgroup,
            "voice_telemetry_enabled": enabled,
            "voice_telemetry_source_id": source_id,
            "voice_telemetry_pause_seconds": float(pause),
        }

    def load(self) -> Dict[str, object]:
        _raw, config, revision, _section_key = self.config_store._read()
        return self._status(config, revision)

    def save(self, *, expected_revision: str, enabled: bool) -> Dict[str, object]:
        raw, config, current_revision, _section_key = self.config_store._read()
        if not isinstance(expected_revision, str) or not expected_revision:
            raise TalkgroupConfigError("Missing configuration revision")
        if not hmac.compare_digest(current_revision, expected_revision):
            raise StaleConfigError(
                "HBlink4 config changed after the parrot telemetry setting loaded; reload before saving"
            )
        if not isinstance(enabled, bool):
            raise TalkgroupConfigError("voice telemetry enabled must be true or false")

        parrot = config.get("parrot")
        if not isinstance(parrot, dict):
            raise TalkgroupConfigError("HBlink4 config has no parrot configuration object")

        # This is the only mutable value exposed by this store.
        parrot["voice_telemetry_enabled"] = enabled
        rendered = (json.dumps(config, indent=4) + "\n").encode("utf-8")
        self.config_store._atomic_replace(raw, rendered)
        return self.load()
