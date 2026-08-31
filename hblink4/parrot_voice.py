"""Runtime PCM gain layer for TG9990 voice telemetry.

The proven DMR packet builder remains in parrot_voice_impl.py.  This entry point
keeps that implementation intact, removes the trailing 73s token, and uses the
installed OpenDMR codec to decode each bundled prompt to PCM, apply the configured
attenuation, and encode it again before normal DMR framing.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Mapping, Optional

from . import parrot_voice_impl as _impl
from .parrot_pcm_codec import OpenDMRParrotCodec, ParrotCodecUnavailable

for _name in dir(_impl):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_impl, _name))

DEFAULT_ATTENUATION_DB = 6.0
MAX_ATTENUATION_DB = 30.0
_RUNTIME_ATTENUATION_DB = DEFAULT_ATTENUATION_DB


@dataclass(frozen=True)
class VoiceTelemetrySettings:
    """Runtime controls for the optional spoken report."""

    enabled: bool = False
    source_id: int = 9990
    pause_after_echo_seconds: float = 0.45
    attenuation_db: float = DEFAULT_ATTENUATION_DB

    @classmethod
    def from_parrot_config(cls, raw: Mapping[str, object], talkgroup: int) -> "VoiceTelemetrySettings":
        global _RUNTIME_ATTENUATION_DB
        enabled = raw.get("voice_telemetry_enabled", False)
        source_id = raw.get("voice_telemetry_source_id", talkgroup)
        pause = raw.get("voice_telemetry_pause_seconds", 0.45)
        attenuation = raw.get("voice_telemetry_attenuation_db", DEFAULT_ATTENUATION_DB)

        if not isinstance(enabled, bool):
            raise ValueError("parrot.voice_telemetry_enabled must be true or false")
        if isinstance(source_id, bool) or not isinstance(source_id, int):
            raise ValueError("parrot.voice_telemetry_source_id must be an integer")
        if not 1 <= source_id <= 0xFFFFFF:
            raise ValueError("parrot.voice_telemetry_source_id must be in the DMR ID range")
        if isinstance(pause, bool) or not isinstance(pause, (int, float)):
            raise ValueError("parrot.voice_telemetry_pause_seconds must be numeric")
        pause = float(pause)
        if not 0.0 <= pause <= 5.0:
            raise ValueError("parrot.voice_telemetry_pause_seconds must be between 0 and 5")
        if isinstance(attenuation, bool) or not isinstance(attenuation, (int, float)):
            raise ValueError("parrot.voice_telemetry_attenuation_db must be numeric")
        attenuation = float(attenuation)
        if not 0.0 <= attenuation <= MAX_ATTENUATION_DB:
            raise ValueError(
                f"parrot.voice_telemetry_attenuation_db must be between 0 and {MAX_ATTENUATION_DB:g}"
            )
        _RUNTIME_ATTENUATION_DB = attenuation
        return cls(
            enabled=enabled,
            source_id=source_id,
            pause_after_echo_seconds=pause,
            attenuation_db=attenuation,
        )

    @property
    def source_id_bytes(self) -> bytes:
        return self.source_id.to_bytes(3, "big")


def telemetry_tokens(rf_quality: Optional[Mapping[str, object]], slot: int):
    """Return the existing BER/RSSI/timeslot report without the old 73s sign-off."""
    return [token for token in _impl.telemetry_tokens(rf_quality, slot) if token != "seventy_threes"]


def _attenuate_pcm(pcm: bytes, attenuation_db: float) -> bytes:
    if len(pcm) != 320:
        raise ValueError("OpenDMR PCM telemetry frame must contain 160 signed samples")
    gain = math.pow(10.0, -float(attenuation_db) / 20.0)
    samples = struct.unpack("<160h", pcm)
    adjusted = [max(-32768, min(32767, int(round(sample * gain)))) for sample in samples]
    return struct.pack("<160h", *adjusted)


def _pcm_reencode_frames(tokens, assets, attenuation_db: float):
    codec = OpenDMRParrotCodec()
    frames = []
    try:
        codec.reset_encoder()
        for _ in range(2):
            frames.extend(_impl._SILENCE_AMBE_FRAMES)
        for token in tokens:
            if token not in assets:
                raise KeyError(f"missing AMBE voice asset: {token}")
            payload = bytes(assets[token])
            _impl._validate_asset(token, payload)
            codec.reset_decoder()
            for offset in range(0, len(payload), _impl.AMBE_FRAME_BYTES):
                canonical = payload[offset:offset + _impl.AMBE_FRAME_BYTES]
                pcm = codec.decode(canonical)
                encoded = codec.encode(_attenuate_pcm(pcm, attenuation_db))
                frames.append(_impl._interleave_ambe_frame(encoded))
        for _ in range(2):
            frames.extend(_impl._SILENCE_AMBE_FRAMES)
        while len(frames) % _impl.AMBE_FRAMES_PER_BURST:
            frames.append(_impl._SILENCE_AMBE_FRAMES[len(frames) % 3])
        return frames
    finally:
        codec.close()


def build_telemetry_packets(
    *,
    rf_quality,
    slot: int,
    dst_id: bytes,
    repeater_id: bytes,
    colour_code: int,
    source_id: bytes,
    assets=None,
    stream_id=None,
):
    """Build a PCM-gain-adjusted report, retaining direct AMBE as safe fallback."""
    if assets is None:
        assets = _impl.load_bundled_assets()
    tokens = telemetry_tokens(rf_quality, slot)
    try:
        frames = _pcm_reencode_frames(tokens, assets, _RUNTIME_ATTENUATION_DB)
    except ParrotCodecUnavailable:
        frames = _impl.assemble_ambe_frames(tokens, assets)
    packets = _impl.build_dmr_voice_packets(
        frames=frames,
        rf_src=source_id,
        dst_id=dst_id,
        repeater_id=repeater_id,
        slot=slot,
        colour_code=colour_code,
        stream_id=stream_id,
    )
    return tokens, packets
