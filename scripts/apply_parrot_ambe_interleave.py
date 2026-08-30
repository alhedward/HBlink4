#!/usr/bin/env python3
from pathlib import Path

voice_path = Path("hblink4/parrot_voice.py")
source = voice_path.read_text(encoding="utf-8")

constants_anchor = '''AMBE_FRAME_BYTES = 9
AMBE_FRAMES_PER_BURST = 3
DMR_VOICE_INTERVAL_SECONDS = 0.06
'''
constants_replacement = '''AMBE_FRAME_BYTES = 9
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
'''
if constants_anchor not in source:
    raise SystemExit("AMBE constants anchor not found")
source = source.replace(constants_anchor, constants_replacement, 1)

validate_anchor = '''def _validate_asset(name: str, payload: bytes) -> None:
    if not payload or len(payload) % AMBE_FRAME_BYTES:
        raise ValueError(
            f"AMBE asset {name!r} must contain a whole number of 9-byte frames"
        )


def assemble_ambe_frames(
'''
validate_replacement = '''def _validate_asset(name: str, payload: bytes) -> None:
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
'''
if validate_anchor not in source:
    raise SystemExit("AMBE validation anchor not found")
source = source.replace(validate_anchor, validate_replacement, 1)

assembly_old = '''    """Concatenate vocabulary clips and pad to complete 60-ms DMR bursts."""

    frames: List[bytes] = []
'''
assembly_new = '''    """Interleave canonical clips and pad to complete 60-ms DMR bursts."""

    frames: List[bytes] = []
'''
if assembly_old not in source:
    raise SystemExit("assembly docstring anchor not found")
source = source.replace(assembly_old, assembly_new, 1)

frames_old = '''        frames.extend(
            payload[offset:offset + AMBE_FRAME_BYTES]
            for offset in range(0, len(payload), AMBE_FRAME_BYTES)
        )
'''
frames_new = '''        frames.extend(
            _interleave_ambe_frame(payload[offset:offset + AMBE_FRAME_BYTES])
            for offset in range(0, len(payload), AMBE_FRAME_BYTES)
        )
'''
if frames_old not in source:
    raise SystemExit("asset frame append anchor not found")
source = source.replace(frames_old, frames_new, 1)
voice_path.write_text(source, encoding="utf-8")

test_path = Path("tests/test_parrot_voice.py")
test_source = test_path.read_text(encoding="utf-8")
import_old = '''from hblink4.parrot_voice import (
    _emb_bits,
    _slot_type_bits,
'''
import_new = '''from hblink4.parrot_voice import (
    _emb_bits,
    _interleave_ambe_frame,
    _slot_type_bits,
'''
if import_old not in test_source:
    raise SystemExit("test import anchor not found")
test_source = test_source.replace(import_old, import_new, 1)

reference_test = '''

def test_opendmr_canonical_silence_interleaves_to_dmr_reference_frame():
    # dmr_utils3.ambe_utils documents ACAA40200044408080 as the standard
    # on-air AMBE silence frame. Deinterleaving it yields this canonical
    # A+B+C2+C3 frame, which is the representation emitted by OpenDMR.
    canonical = bytes.fromhex("49400f09a0e0000000")
    assert _interleave_ambe_frame(canonical) == bytes.fromhex(
        "acaa40200044408080"
    )
'''
insert_before = '\n\ndef test_telemetry_tokens_use_full_metric_names_natural_numbers_and_73s():'
if insert_before not in test_source:
    raise SystemExit("test insertion anchor not found")
test_source = test_source.replace(insert_before, reference_test + insert_before, 1)
test_path.write_text(test_source, encoding="utf-8")
