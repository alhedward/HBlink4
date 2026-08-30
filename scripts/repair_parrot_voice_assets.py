#!/usr/bin/env python3
"""Repair the legacy OpenDMR B-block alignment in bundled parrot AMBE assets.

This is a one-time/build-time data migration. It preserves the vocoder output
and reconstructs the single lost Golay bit from channel-code redundancy; no
TTS or re-vocoding is performed.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hblink4.parrot_ambe import repair_legacy_opendmr_frame, validate_canonical_frame


FRAME_BYTES = 9
CHANNEL_CODING_MARKER = "golay23-right-aligned-before-prng"


def _write_archive(asset_payloads: dict[str, bytes], manifest: dict, output: Path) -> str:
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    with output.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9) as gz:
            with tarfile.open(fileobj=gz, mode="w") as archive:
                info = tarfile.TarInfo("manifest.json")
                info.size = len(manifest_bytes)
                info.mode = 0o644
                info.mtime = 0
                archive.addfile(info, io.BytesIO(manifest_bytes))
                for name in sorted(asset_payloads):
                    payload = asset_payloads[name]
                    info = tarfile.TarInfo(f"ambe/{name}.ambe")
                    info.size = len(payload)
                    info.mode = 0o644
                    info.mtime = 0
                    archive.addfile(info, io.BytesIO(payload))
    return hashlib.sha256(output.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "archive",
        type=Path,
        nargs="?",
        default=Path("hblink4/parrot_voice_assets.tar.gz"),
    )
    args = parser.parse_args()
    archive_path = args.archive

    with tarfile.open(archive_path, "r:gz") as archive:
        manifest_stream = archive.extractfile("manifest.json")
        if manifest_stream is None:
            raise RuntimeError("parrot voice manifest is unreadable")
        manifest = json.load(manifest_stream)
        assets_meta = manifest.get("assets")
        if not isinstance(assets_meta, dict):
            raise RuntimeError("parrot voice manifest has no asset table")

        existing_coding = manifest.get("channel_coding") or {}
        if existing_coding.get("b_block") == CHANNEL_CODING_MARKER:
            print("archive is already marked with corrected B-block channel coding")
            return 0

        repaired_assets: dict[str, bytes] = {}
        repaired_frames = 0
        for name, entry in assets_meta.items():
            if not isinstance(entry, dict) or not isinstance(entry.get("file"), str):
                raise RuntimeError(f"invalid manifest entry for {name!r}")
            stream = archive.extractfile(entry["file"])
            if stream is None:
                raise RuntimeError(f"asset {name!r} is unreadable")
            payload = stream.read()
            if not payload or len(payload) % FRAME_BYTES:
                raise RuntimeError(f"asset {name!r} is not whole 9-byte frames")

            output = bytearray()
            for offset in range(0, len(payload), FRAME_BYTES):
                frame = repair_legacy_opendmr_frame(payload[offset:offset + FRAME_BYTES])
                validate_canonical_frame(frame)
                output.extend(frame)
                repaired_frames += 1
            repaired_assets[name] = bytes(output)

    manifest["channel_coding"] = {
        "canonical_format": "A24+B23+C25",
        "b_block": CHANNEL_CODING_MARKER,
        "migration": "lossless legacy OpenDMR Golay B-block reconstruction",
        "reference": "MMDVM-Host AMBEFEC.cpp: encode23127(datb) >> 1 before PRNG",
    }

    with tempfile.NamedTemporaryFile(
        prefix="parrot-voice-repaired-", suffix=".tar.gz", delete=False
    ) as temp:
        temp_path = Path(temp.name)
    try:
        sha256 = _write_archive(repaired_assets, manifest, temp_path)
        temp_path.replace(archive_path)
    finally:
        temp_path.unlink(missing_ok=True)

    print(f"repaired {repaired_frames} canonical AMBE frames")
    print(f"wrote {archive_path} ({archive_path.stat().st_size} bytes)")
    print(f"sha256 {sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
