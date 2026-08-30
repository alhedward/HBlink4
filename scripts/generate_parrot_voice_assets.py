#!/usr/bin/env python3
"""Generate the pre-encoded AMBE+2 vocabulary used by TG9990 telemetry.

This is an offline/build-time tool only. Production HBlink4 does not need gTTS,
ffmpeg, num2words, or OpenDMR.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.metadata
import json
import shutil
import struct
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

from gtts import gTTS
from num2words import num2words

SAMPLE_RATE = 8000
FRAME_SAMPLES = 160
AMBE_FRAME_BYTES = 9
SILENCE_THRESHOLD = 250
PAD_SAMPLES = int(SAMPLE_RATE * 0.060)
OPENDMR_COMMIT = "d28164b39ba4d91ad5948ff22707937f8944f70f"

PHRASES = {
    "bit_error_rate": "Bit Error Rate",
    "received_signal_strength_indication": "Received Signal Strength Indication",
    "point": "point",
    "percent": "percent",
    "minus": "minus",
    "dbm": "D B M",
    "timeslot": "Timeslot",
    "unavailable": "unavailable",
    "seventy_threes": "Seventy threes",
}


def vocabulary() -> dict[str, str]:
    result = dict(PHRASES)
    for number in range(201):
        result[f"number_{number}"] = num2words(number, lang="en")
    return result


def read_s16le(path: Path) -> list[int]:
    data = path.read_bytes()
    if not data or len(data) % 2:
        raise RuntimeError(f"invalid PCM data in {path}")
    return list(struct.unpack("<" + "h" * (len(data) // 2), data))


def trim_and_align(samples: list[int]) -> list[int]:
    active = [index for index, value in enumerate(samples) if abs(value) >= SILENCE_THRESHOLD]
    if active:
        first = max(0, active[0] - PAD_SAMPLES)
        last = min(len(samples), active[-1] + 1 + PAD_SAMPLES)
        samples = samples[first:last]
    remainder = len(samples) % FRAME_SAMPLES
    if remainder:
        samples += [0] * (FRAME_SAMPLES - remainder)
    return samples


def write_s16le(path: Path, samples: list[int]) -> None:
    path.write_bytes(struct.pack("<" + "h" * len(samples), *samples))


def run_checked(args: list[str]) -> None:
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL)


def synthesize(text: str, output_mp3: Path) -> None:
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            gTTS(text=text, lang="en", tld="com.au", slow=False).save(str(output_mp3))
            return
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(attempt * 2)
    raise RuntimeError(f"gTTS failed after 3 attempts for {text!r}") from last_error


def encode_asset(codec: Path, text: str, target: Path, work: Path) -> tuple[int, float]:
    mp3 = work / "source.mp3"
    pcm = work / "source.raw"
    aligned = work / "aligned.raw"
    synthesize(text, mp3)
    run_checked([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(mp3), "-ac", "1", "-ar", "8000",
        "-acodec", "pcm_s16le", "-f", "s16le", str(pcm),
    ])
    samples = trim_and_align(read_s16le(pcm))
    write_s16le(aligned, samples)
    run_checked([str(codec), "encode", str(aligned), str(target)])
    payload = target.read_bytes()
    if not payload or len(payload) % AMBE_FRAME_BYTES:
        raise RuntimeError(f"OpenDMR produced invalid AMBE data for {text!r}")
    frames = len(payload) // AMBE_FRAME_BYTES
    expected = len(samples) // FRAME_SAMPLES
    if frames != expected:
        raise RuntimeError(f"OpenDMR frame mismatch for {text!r}: {frames} != {expected}")
    return frames, frames * 0.020


def deterministic_archive(source_dir: Path, manifest: dict, output: Path) -> str:
    import io
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    with output.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9) as gz:
            with tarfile.open(fileobj=gz, mode="w") as archive:
                info = tarfile.TarInfo("manifest.json")
                info.size = len(manifest_bytes)
                info.mode = 0o644
                info.mtime = 0
                archive.addfile(info, fileobj=io.BytesIO(manifest_bytes))
                for path in sorted(source_dir.glob("*.ambe")):
                    info = archive.gettarinfo(str(path), arcname=f"ambe/{path.name}")
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mtime = 0
                    with path.open("rb") as stream:
                        archive.addfile(info, stream)
    return hashlib.sha256(output.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codec", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path,
        default=Path("hblink4/parrot_voice_assets.tar.gz"),
    )
    args = parser.parse_args()
    codec = args.codec.resolve()
    if not codec.is_file():
        raise SystemExit(f"OpenDMR codec not found: {codec}")

    words = vocabulary()
    if len(words) != 210:
        raise RuntimeError(f"unexpected vocabulary size: {len(words)}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hblink4-parrot-voice-") as temp:
        temp_path = Path(temp)
        ambe_dir = temp_path / "ambe"
        ambe_dir.mkdir()
        assets: dict[str, dict[str, object]] = {}
        for index, (name, text) in enumerate(words.items(), 1):
            target = ambe_dir / f"{name}.ambe"
            work = temp_path / "work"
            if work.exists():
                shutil.rmtree(work)
            work.mkdir()
            frames, duration = encode_asset(codec, text, target, work)
            assets[name] = {
                "text": text,
                "file": f"ambe/{name}.ambe",
                "frames": frames,
                "duration_seconds": round(duration, 3),
                "bytes": target.stat().st_size,
            }
            print(f"[{index:3d}/210] {name:38s} {frames:4d} frames")

        manifest = {
            "format": "raw-ambe-plus-2",
            "frame_bytes": AMBE_FRAME_BYTES,
            "frame_duration_ms": 20,
            "opendmr_commit": OPENDMR_COMMIT,
            "source_tts": {
                "engine": "gTTS / Google Translate speech",
                "gtts_version": importlib.metadata.version("gTTS"),
                "num2words_version": importlib.metadata.version("num2words"),
                "lang": "en",
                "tld": "com.au",
                "slow": False,
            },
            "source_pcm": {
                "sample_rate_hz": SAMPLE_RATE,
                "bits_per_sample": 16,
                "channels": 1,
                "encoding": "signed little-endian",
            },
            "silence_trim": {
                "absolute_sample_threshold": SILENCE_THRESHOLD,
                "retained_padding_ms": 60,
            },
            "assets": assets,
        }
        sha256 = deterministic_archive(ambe_dir, manifest, args.output)

    print(f"wrote {args.output} ({args.output.stat().st_size} bytes)")
    print(f"sha256 {sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
