"""Bundled AMBE+2 vocabulary for HBlink4 parrot voice telemetry.

The source speech was generated offline with Australian-English gTTS and
converted to 8 kHz, 16-bit signed little-endian mono PCM.  OpenDMR then encoded
the PCM to canonical 9-byte DMR AMBE+2 frames.  Production HBlink4 never runs
TTS or a vocoder; it only reads these pre-encoded frames.
"""

from __future__ import annotations

import json
import tarfile
from pathlib import Path
from typing import Any, Dict


OPENDMR_COMMIT = "d28164b39ba4d91ad5948ff22707937f8944f70f"
_ARCHIVE = Path(__file__).with_name("parrot_voice_assets.tar.gz")
_EXPECTED_FIXED = {
    "bit_error_rate",
    "received_signal_strength_indication",
    "point",
    "percent",
    "minus",
    "dbm",
    "timeslot",
    "unavailable",
    "seventy_threes",
}
_EXPECTED_NUMBERS = {f"number_{value}" for value in range(201)}
_EXPECTED_NAMES = _EXPECTED_FIXED | _EXPECTED_NUMBERS


def _load_assets() -> tuple[Dict[str, bytes], Dict[str, Any]]:
    if not _ARCHIVE.is_file():
        raise RuntimeError(f"bundled parrot voice archive is missing: {_ARCHIVE}")

    with tarfile.open(_ARCHIVE, "r:gz") as archive:
        manifest_member = archive.getmember("manifest.json")
        manifest_stream = archive.extractfile(manifest_member)
        if manifest_stream is None:
            raise RuntimeError("bundled parrot voice manifest is unreadable")
        manifest = json.load(manifest_stream)

        if manifest.get("format") != "raw-ambe-plus-2":
            raise RuntimeError("bundled parrot voice archive has an unexpected format")
        if manifest.get("frame_bytes") != 9:
            raise RuntimeError("bundled parrot voice archive has an unexpected frame size")
        if manifest.get("opendmr_commit") != OPENDMR_COMMIT:
            raise RuntimeError("bundled parrot voice archive was encoded with an unexpected OpenDMR revision")

        metadata = manifest.get("assets")
        if not isinstance(metadata, dict):
            raise RuntimeError("bundled parrot voice manifest has no asset table")
        if set(metadata) != _EXPECTED_NAMES:
            missing = sorted(_EXPECTED_NAMES - set(metadata))
            extra = sorted(set(metadata) - _EXPECTED_NAMES)
            raise RuntimeError(
                "bundled parrot voice vocabulary mismatch "
                f"(missing={missing[:5]}, extra={extra[:5]})"
            )

        assets: Dict[str, bytes] = {}
        for name, entry in metadata.items():
            if not isinstance(entry, dict):
                raise RuntimeError(f"invalid metadata for parrot voice asset {name!r}")
            relative_path = entry.get("file")
            if not isinstance(relative_path, str) or not relative_path.startswith("ambe/"):
                raise RuntimeError(f"invalid archive path for parrot voice asset {name!r}")
            member = archive.getmember(relative_path)
            stream = archive.extractfile(member)
            if stream is None:
                raise RuntimeError(f"parrot voice asset {name!r} is unreadable")
            payload = stream.read()
            if not payload or len(payload) % 9:
                raise RuntimeError(f"parrot voice asset {name!r} is not whole 9-byte AMBE frames")
            if entry.get("bytes") != len(payload):
                raise RuntimeError(f"parrot voice asset {name!r} byte count does not match manifest")
            if entry.get("frames") != len(payload) // 9:
                raise RuntimeError(f"parrot voice asset {name!r} frame count does not match manifest")
            assets[name] = payload

    return assets, manifest


AMBE_ASSETS, ASSET_MANIFEST = _load_assets()
