"""Pre-encoded DMR voice telemetry helpers for the local TG9990 parrot.

The live HBlink4 process never vocodes PCM audio.  Voice prompts are encoded to
raw 72-bit AMBE+2 frames offline and committed as small data assets.  This
module selects the required prompt fragments, assembles a spoken RF-quality
report, and wraps the AMBE frames in valid HomeBrew DMRD voice packets.

DMR burst construction follows the long-established HBlink playback/mk_voice
layout: three VHEAD frames, A-F voice bursts carrying three 72-bit AMBE frames
per 60 ms burst, then one VTERM frame.  Link Control is generated with the
project's existing dmr_utils3 dependency.  Colour Code is derived from the
originating parrot call instead of assuming CC1.
"""

from __future__ import annotations

import math
import secrets
from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from bitarray import bitarray
from dmr_utils3 import bptc, golay, qr
from dmr_utils3.const import BS_DATA_SYNC, BS_VOICE_SYNC

from .lc import LC_OPT_GROUP_DEFAULT


AMBE_FRAME_BYTES = 9
AMBE_FRAMES_PER_BURST = 3
DMR_VOICE_INTERVAL_SECONDS = 0.06

# DMR AMBE+2 on-air interleave schedule. OpenDMR emits each 72-bit frame in
# canonical/DVSI A(24)+B(23)+C2(11)+C3(14) order; HomeBrew DMRD voice payloads
# carry those channel-coded bits in the ETSI/DSD 36-dibit interleaved order.
# These fixed indices match dmr_utils3.ambe_utils and the long-standing DSD
# decoder schedule.
_AMBE_INTERLEAVE_W = (
    0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1,
    0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 2,
    0, 2, 0, 2, 0, 2, 0, 2, 0, 2, 0, 2,
)
_AMBE_INTERLEAVE_X = (
    23, 10, 22, 9, 21, 8, 20, 7, 19, 6, 18, 5,
    17, 4, 16, 3, 15, 2, 14, 1, 13, 0, 12, 10,
    11, 9, 10, 8, 9, 7, 8, 6, 7, 5, 6, 4,
)
_AMBE_INTERLEAVE_Y = (
    0, 2, 0, 2, 0, 2, 0, 2, 0, 3, 0, 3,
    1, 3, 1, 3, 1, 3, 1, 3, 1, 3, 1, 3,
    1, 3, 1, 3, 1, 3, 1, 3, 1, 3, 1, 3,
)
_AMBE_INTERLEAVE_Z = (
    5, 3, 4, 2, 3, 1, 2, 0, 1, 13, 0, 12,
    22, 11, 21, 10, 20, 9, 19, 8, 18, 7, 17, 6,
    16, 5, 15, 4, 14, 3, 13, 2, 12, 1, 11, 0,
)

# HomeBrew DMRD byte-15 values before the TS2 bit is applied.
_HEAD_BITS = 0b00100001
_BURST_BITS = (0b00010000, 0b00000001, 0b00000010,
               0b00000011, 0b00000100, 0b00000101)
_TERM_BITS = 0b00100010
_TAIL = b"\x00\x00"  # generated TX has no server-side BER/RSSI sample

# A known DMR silence voice burst, historically used by HBlink voice playback.
# The two halves are 108 AMBE bits either side of the 48-bit sync/EMB field.
_SILENCE_LEFT_BITS = (
    "101011000000101010100000010000000000001000000000000000000000"
    "010001000000010000000000100000000000100000000000"
)
_SILENCE_RIGHT_BITS = (
    "001010110000001010101000000100000000000010000000000000000000"
    "000100010000000100000000001000000000001000000000"
)
_SILENCE_216 = bitarray(_SILENCE_LEFT_BITS + _SILENCE_RIGHT_BITS, endian="big")
_SILENCE_AMBE_FRAMES = tuple(
    _SILENCE_216[offset:offset + 72].tobytes()
    for offset in range(0, 216, 72)
)


@dataclass(frozen=True)
class VoiceTelemetrySettings:
    """Runtime controls for the optional spoken report."""

    enabled: bool = False
    source_id: int = 9990
    pause_after_echo_seconds: float = 0.45

    @classmethod
    def from_parrot_config(cls, raw: Mapping[str, object], talkgroup: int) -> "VoiceTelemetrySettings":
        enabled = raw.get("voice_telemetry_enabled", False)
        source_id = raw.get("voice_telemetry_source_id", talkgroup)
        pause = raw.get("voice_telemetry_pause_seconds", 0.45)

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

        return cls(enabled=enabled, source_id=source_id, pause_after_echo_seconds=pause)

    @property
    def source_id_bytes(self) -> bytes:
        return self.source_id.to_bytes(3, "big")


def load_bundled_assets() -> Dict[str, bytes]:
    """Return the generated AMBE vocabulary, or an empty dict when absent.

    Keeping this import lazy means development/tests and ordinary HBlink4 use
    remain healthy even before a generated asset module has been installed.
    """

    try:
        from .parrot_voice_assets import AMBE_ASSETS
    except ImportError:
        return {}
    if not isinstance(AMBE_ASSETS, dict):
        return {}
    return {
        str(name): bytes(value)
        for name, value in AMBE_ASSETS.items()
        if isinstance(name, str) and isinstance(value, (bytes, bytearray))
    }


def _number_token(value: int) -> str:
    if not 0 <= value <= 200:
        raise ValueError(f"spoken number outside bundled vocabulary: {value}")
    return f"number_{value}"


def _ber_value_tokens(value: float) -> List[str]:
    """Speak BER compactly while retaining useful low-BER resolution."""

    value = max(0.0, min(100.0, float(value)))
    decimals = 2 if value < 1.0 else 1
    rendered = f"{value:.{decimals}f}".rstrip("0").rstrip(".")
    if "." not in rendered:
        return [_number_token(int(rendered))]

    whole, fraction = rendered.split(".", 1)
    tokens = [_number_token(int(whole)), "point"]
    tokens.extend(_number_token(int(digit)) for digit in fraction)
    return tokens


def telemetry_tokens(rf_quality: Optional[Mapping[str, object]], slot: int) -> List[str]:
    """Build the requested spoken report vocabulary.

    Example:
      Bit Error Rate zero point four percent.
      Received Signal Strength Indication minus seventy-two D B M.
      Timeslot two. Seventy threes.
    """

    quality = rf_quality or {}
    tokens: List[str] = ["bit_error_rate"]

    ber = quality.get("ber_average_percent")
    if isinstance(ber, (int, float)) and not isinstance(ber, bool) and math.isfinite(float(ber)):
        tokens.extend(_ber_value_tokens(float(ber)))
        tokens.append("percent")
    else:
        tokens.append("unavailable")

    tokens.append("received_signal_strength_indication")
    rssi = quality.get("rssi_average_dbm")
    if isinstance(rssi, (int, float)) and not isinstance(rssi, bool) and math.isfinite(float(rssi)):
        rounded = int(round(float(rssi)))
        magnitude = abs(rounded)
        if magnitude <= 200:
            if rounded < 0:
                tokens.append("minus")
            tokens.append(_number_token(magnitude))
            tokens.append("dbm")
        else:
            tokens.append("unavailable")
    else:
        tokens.append("unavailable")

    tokens.append("timeslot")
    tokens.append(_number_token(2 if slot == 2 else 1))
    tokens.append("seventy_threes")
    return tokens


def _validate_asset(name: str, payload: bytes) -> None:
    if not payload or len(payload) % AMBE_FRAME_BYTES:
        raise ValueError(
            f"AMBE asset {name!r} must contain a whole number of 9-byte frames"
        )


def _interleave_ambe_frame(frame: bytes) -> bytes:
    """Convert one OpenDMR canonical 72-bit frame to DMR on-air bit order."""

    if len(frame) != AMBE_FRAME_BYTES:
        raise ValueError("canonical AMBE frame must be exactly 9 bytes")

    canonical = [
        (byte >> (7 - bit_index)) & 1
        for byte in frame
        for bit_index in range(8)
    ]
    rows = [[0] * 24 for _ in range(4)]

    # A and B are already MSB-first codewords in OpenDMR's canonical stream.
    rows[0][:24] = canonical[:24]
    rows[1][:23] = canonical[24:47]

    # The historical DMR interleave matrix indexes C2/C3 in the opposite
    # direction to their canonical serial representation.
    for index, bit in enumerate(canonical[47:58]):
        rows[2][10 - index] = bit
    for index, bit in enumerate(canonical[58:72]):
        rows[3][13 - index] = bit

    interleaved = bitarray(endian="big")
    for index in range(36):
        interleaved.append(
            rows[_AMBE_INTERLEAVE_W[index]][_AMBE_INTERLEAVE_X[index]]
        )
        interleaved.append(
            rows[_AMBE_INTERLEAVE_Y[index]][_AMBE_INTERLEAVE_Z[index]]
        )
    return interleaved.tobytes()


def assemble_ambe_frames(
    tokens: Sequence[str],
    assets: Mapping[str, bytes],
    *,
    leading_silence_bursts: int = 2,
    trailing_silence_bursts: int = 2,
) -> List[bytes]:
    """Interleave canonical clips and pad to complete 60-ms DMR bursts."""

    frames: List[bytes] = []
    for _ in range(max(0, leading_silence_bursts)):
        frames.extend(_SILENCE_AMBE_FRAMES)

    for token in tokens:
        if token not in assets:
            raise KeyError(f"missing AMBE voice asset: {token}")
        payload = bytes(assets[token])
        _validate_asset(token, payload)
        frames.extend(
            _interleave_ambe_frame(payload[offset:offset + AMBE_FRAME_BYTES])
            for offset in range(0, len(payload), AMBE_FRAME_BYTES)
        )

    for _ in range(max(0, trailing_silence_bursts)):
        frames.extend(_SILENCE_AMBE_FRAMES)

    while len(frames) % AMBE_FRAMES_PER_BURST:
        # Continue the known three-frame silence pattern at the correct phase.
        frames.append(_SILENCE_AMBE_FRAMES[len(frames) % 3])
    return frames


def _bits_from_int(value: int, width: int) -> bitarray:
    return bitarray(f"{value:0{width}b}", endian="big")


def _slot_type_bits(colour_code: int, data_type: int) -> bitarray:
    """Generate the 20-bit CC+data-type Golay slot-type codeword."""

    if not 0 <= colour_code <= 15:
        raise ValueError("DMR colour code must be 0..15")
    value = (colour_code << 4) | (data_type & 0x0F)
    encoded = golay.encode_2087(bytes([value]))
    return _bits_from_int(encoded, 20)


def _emb_bits(colour_code: int, lcss: int) -> bitarray:
    """Generate the 16-bit EMB field (PI=0) for one voice burst."""

    if not 0 <= colour_code <= 15:
        raise ValueError("DMR colour code must be 0..15")
    if not 0 <= lcss <= 3:
        raise ValueError("LCSS must be 0..3")
    seven_bits = (colour_code << 3) | lcss  # CC(4), PI(1=0), LCSS(2)
    return _bits_from_int(qr.ENCODE_1676[seven_bits], 16)


def extract_colour_code(recorded_packets: Iterable[bytes], default: int = 1) -> int:
    """Read Colour Code from a received parrot VHEAD/voice packet."""

    for packet in recorded_packets:
        if len(packet) < 53 or packet[:4] != b"DMRD":
            continue
        frame_type = (packet[15] & 0x30) >> 4
        dtype_vseq = packet[15] & 0x0F
        payload = bitarray(endian="big")
        payload.frombytes(packet[20:53])
        if frame_type == 2 and dtype_vseq in (1, 2):
            # VHEAD/VTERM: first four bits of the split 20-bit slot type.
            bits = payload[98:102]
        elif frame_type == 0 and 1 <= dtype_vseq <= 5:
            # Voice B-F: first four bits of the 16-bit EMB wrapper.
            bits = payload[108:112]
        else:
            continue
        value = 0
        for bit in bits:
            value = (value << 1) | int(bit)
        return value
    return default


def _voice_bursts(frames: Sequence[bytes]) -> Iterable[Tuple[bitarray, bitarray]]:
    if len(frames) % 3:
        raise ValueError("AMBE frame count must be a multiple of three")
    for offset in range(0, len(frames), 3):
        bits = bitarray(endian="big")
        bits.frombytes(b"".join(frames[offset:offset + 3]))
        if len(bits) != 216:
            raise ValueError("three AMBE frames must contain 216 bits")
        yield bits[:108], bits[108:216]


def build_dmr_voice_packets(
    *,
    frames: Sequence[bytes],
    rf_src: bytes,
    dst_id: bytes,
    repeater_id: bytes,
    slot: int,
    colour_code: int,
    stream_id: Optional[bytes] = None,
) -> List[bytes]:
    """Wrap canonical 72-bit AMBE frames in a complete local DMRD call."""

    if len(rf_src) != 3 or len(dst_id) != 3 or len(repeater_id) != 4:
        raise ValueError("DMR source/destination/repeater IDs have invalid width")
    if slot not in (1, 2):
        raise ValueError("DMR timeslot must be 1 or 2")
    if stream_id is None:
        stream_id = secrets.token_bytes(4)
    if len(stream_id) != 4:
        raise ValueError("DMR stream ID must be four bytes")

    lc = LC_OPT_GROUP_DEFAULT + dst_id + rf_src
    head_lc = bptc.encode_header_lc(lc)
    term_lc = bptc.encode_terminator_lc(lc)
    emb_lc = bptc.encode_emblc(lc)
    slot_head = _slot_type_bits(colour_code, 1)
    slot_term = _slot_type_bits(colour_code, 2)

    embeds = (
        BS_VOICE_SYNC,
        _emb_bits(colour_code, 1)[:8] + emb_lc[1] + _emb_bits(colour_code, 1)[-8:],
        _emb_bits(colour_code, 3)[:8] + emb_lc[2] + _emb_bits(colour_code, 3)[-8:],
        _emb_bits(colour_code, 3)[:8] + emb_lc[3] + _emb_bits(colour_code, 3)[-8:],
        _emb_bits(colour_code, 2)[:8] + emb_lc[4] + _emb_bits(colour_code, 2)[-8:],
        _emb_bits(colour_code, 0)[:8] + bitarray("0" * 32, endian="big") + _emb_bits(colour_code, 0)[-8:],
    )

    slot_mask = 0x80 if slot == 2 else 0x00
    sdp = rf_src + dst_id + repeater_id
    sequence = 0
    packets: List[bytes] = []

    def emit(bits_byte: int, payload_bits: bitarray) -> None:
        nonlocal sequence
        packet = (
            b"DMRD"
            + bytes([sequence])
            + sdp
            + bytes([slot_mask | bits_byte])
            + stream_id
            + payload_bits.tobytes()
            + _TAIL
        )
        if len(packet) != 55:
            raise AssertionError(f"generated DMRD packet has unexpected size {len(packet)}")
        packets.append(packet)
        sequence = (sequence + 1) & 0xFF

    # Repeat the Voice Header three times, as MMDVM/HBlink playback utilities do.
    head_payload = (
        head_lc[:98]
        + slot_head[:10]
        + BS_DATA_SYNC
        + slot_head[-10:]
        + head_lc[-98:]
    )
    for _ in range(3):
        emit(_HEAD_BITS, head_payload)

    for burst_index, (left, right) in enumerate(_voice_bursts(frames)):
        seq = burst_index % 6
        emit(_BURST_BITS[seq], left + embeds[seq] + right)

    term_payload = (
        term_lc[:98]
        + slot_term[:10]
        + BS_DATA_SYNC
        + slot_term[-10:]
        + term_lc[-98:]
    )
    emit(_TERM_BITS, term_payload)
    return packets


def build_telemetry_packets(
    *,
    rf_quality: Optional[Mapping[str, object]],
    slot: int,
    dst_id: bytes,
    repeater_id: bytes,
    colour_code: int,
    source_id: bytes,
    assets: Optional[Mapping[str, bytes]] = None,
    stream_id: Optional[bytes] = None,
) -> Tuple[List[str], List[bytes]]:
    """Select spoken content and build its complete DMRD packet sequence."""

    if assets is None:
        assets = load_bundled_assets()
    tokens = telemetry_tokens(rf_quality, slot)
    frames = assemble_ambe_frames(tokens, assets)
    packets = build_dmr_voice_packets(
        frames=frames,
        rf_src=source_id,
        dst_id=dst_id,
        repeater_id=repeater_id,
        slot=slot,
        colour_code=colour_code,
        stream_id=stream_id,
    )
    return tokens, packets
