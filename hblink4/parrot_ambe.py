"""AMBE+2 channel-coding conversion for HBlink4 parrot telemetry.

OpenDMR's codec file format is DVSI/canonical channel order:
A(24) + B(23) + C(25). DMR HomeBrew voice payloads use the historical
ETSI/DSD bit interleave, whose A/B matrix orientation is different from that
canonical serial representation. Convert through the 49 protected voice
parameter bits so the two representations cannot be accidentally conflated.

The canonical Golay/PRNG rules below match MMDVM-Host ``AMBEFEC.cpp``. The
DMR on-air interleave matches ``dmr_utils3.ambe_utils``.
"""

from __future__ import annotations

from typing import List, Tuple


AMBE_FRAME_BYTES = 9

# DMR/DSD 36-dibit interleave schedule, from dmr_utils3.ambe_utils.
_INTERLEAVE_W = (
    0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1,
    0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 2,
    0, 2, 0, 2, 0, 2, 0, 2, 0, 2, 0, 2,
)
_INTERLEAVE_X = (
    23, 10, 22, 9, 21, 8, 20, 7, 19, 6, 18, 5,
    17, 4, 16, 3, 15, 2, 14, 1, 13, 0, 12, 10,
    11, 9, 10, 8, 9, 7, 8, 6, 7, 5, 6, 4,
)
_INTERLEAVE_Y = (
    0, 2, 0, 2, 0, 2, 0, 2, 0, 3, 0, 3,
    1, 3, 1, 3, 1, 3, 1, 3, 1, 3, 1, 3,
    1, 3, 1, 3, 1, 3, 1, 3, 1, 3, 1, 3,
)
_INTERLEAVE_Z = (
    5, 3, 4, 2, 3, 1, 2, 0, 1, 13, 0, 12,
    22, 11, 21, 10, 20, 9, 19, 8, 18, 7, 17, 6,
    16, 5, 15, 4, 14, 3, 13, 2, 12, 1, 11, 0,
)


def _canonical_golay23127(data: int) -> int:
    """Return the right-aligned systematic Golay(23,12) codeword."""

    data &= 0xFFF
    work = data << 11
    remainder = work
    polynomial = 0xC75
    for bit in range(22, 10, -1):
        if remainder & (1 << bit):
            remainder ^= polynomial << (bit - 11)
    return work | (remainder & 0x7FF)


def _canonical_golay24128(data: int) -> int:
    """Return extended Golay(24,12), including its overall parity bit."""

    code23 = _canonical_golay23127(data)
    return (code23 << 1) | (code23.bit_count() & 1)


def _canonical_prng_mask(c0: int) -> int:
    """Return the 23-bit DMR B-block whitening mask."""

    state = 16 * (c0 & 0xFFF)
    mask = 0
    for index in range(1, 24):
        state = (173 * state + 13849) % 65536
        if state >= 32768:
            mask |= 1 << (23 - index)
    return mask


def _canonical_fields(frame: bytes) -> Tuple[int, int, int]:
    if len(frame) != AMBE_FRAME_BYTES:
        raise ValueError("canonical AMBE frame must be exactly 9 bytes")
    value = int.from_bytes(frame, "big")
    return (
        (value >> 48) & 0xFFFFFF,
        (value >> 25) & 0x7FFFFF,
        value & 0x1FFFFFF,
    )


def validate_canonical_frame(frame: bytes) -> None:
    """Reject canonical frames whose A/B channel coding is inconsistent."""

    a_block, b_block, _c_block = _canonical_fields(frame)
    c0 = a_block >> 12
    if a_block != _canonical_golay24128(c0):
        raise ValueError("canonical AMBE A block has invalid Golay(24,12) coding")

    b_codeword = b_block ^ _canonical_prng_mask(c0)
    c1 = b_codeword >> 11
    if b_codeword != _canonical_golay23127(c1):
        raise ValueError(
            "canonical AMBE B block has invalid Golay(23,12) alignment/coding"
        )


def _canonical_to_raw49(frame: bytes) -> List[int]:
    validate_canonical_frame(frame)
    a_block, b_block, c_block = _canonical_fields(frame)
    c0 = a_block >> 12
    b_codeword = b_block ^ _canonical_prng_mask(c0)
    c1 = b_codeword >> 11

    raw: List[int] = []
    raw.extend((c0 >> bit) & 1 for bit in range(11, -1, -1))
    raw.extend((c1 >> bit) & 1 for bit in range(11, -1, -1))
    raw.extend((c_block >> bit) & 1 for bit in range(24, -1, -1))
    return raw


def _dmr_golay2312(data: int) -> int:
    """Return dmr_utils3/DSD matrix-oriented Golay(23,12) codeword."""

    data &= 0xFFF
    original = data
    work = data
    polynomial = 0xAE3
    for _ in range(12):
        if work & 1:
            work ^= polynomial
        work >>= 1
    return (work << 12) | original


def _raw49_to_dmr_frame(raw: List[int]) -> bytes:
    if len(raw) != 49 or any(bit not in (0, 1) for bit in raw):
        raise ValueError("raw AMBE voice parameters must be exactly 49 bits")

    rows = [[0] * 24 for _ in range(4)]

    c0 = 0
    for index in range(11, -1, -1):
        c0 = (c0 << 1) | raw[index]
    code0 = _dmr_golay2312(c0)
    code0 |= (code0.bit_count() & 1) << 23
    for index in range(23, -1, -1):
        rows[0][index] = code0 & 1
        code0 >>= 1

    c1 = 0
    for index in range(23, 11, -1):
        c1 = (c1 << 1) | raw[index]
    code1 = _dmr_golay2312(c1)
    for index in range(22, -1, -1):
        rows[1][index] = code1 & 1
        code1 >>= 1

    for index in range(10, -1, -1):
        rows[2][index] = raw[34 - index]
    for index in range(13, -1, -1):
        rows[3][index] = raw[48 - index]

    # dmr_utils3 seeds whitening by reading row 0 from index 23 down to 12.
    whitening_seed = 0
    for index in range(23, 11, -1):
        whitening_seed = (whitening_seed << 1) | rows[0][index]
    state = 16 * whitening_seed
    for index in range(22, -1, -1):
        state = (173 * state + 13849) % 65536
        rows[1][index] ^= 1 if state >= 32768 else 0

    value = 0
    for index in range(36):
        value = (value << 1) | rows[_INTERLEAVE_W[index]][_INTERLEAVE_X[index]]
        value = (value << 1) | rows[_INTERLEAVE_Y[index]][_INTERLEAVE_Z[index]]
    return value.to_bytes(AMBE_FRAME_BYTES, "big")


def canonical_to_dmr_frame(frame: bytes) -> bytes:
    """Convert one clean OpenDMR canonical frame to DMR on-air bit order."""

    return _raw49_to_dmr_frame(_canonical_to_raw49(frame))


def repair_legacy_opendmr_frame(frame: bytes) -> bytes:
    """Losslessly repair OpenDMR's historical unshifted B-block output.

    The affected encoder used the 24-bit-aligned return value of
    ``CGolay24128::encode23127`` directly instead of shifting it right once
    before applying the 23-bit PRNG mask. The top codeword bit was therefore
    dropped. Golay redundancy makes that missing bit uniquely recoverable.
    """

    a_block, bad_b_block, c_block = _canonical_fields(frame)
    c0 = a_block >> 12
    if a_block != _canonical_golay24128(c0):
        raise ValueError("legacy AMBE frame has invalid A-block coding")

    mask = _canonical_prng_mask(c0)
    aligned_low23 = bad_b_block ^ mask
    known_low22 = aligned_low23 >> 1

    candidates = []
    for top_bit in (0, 1):
        codeword = known_low22 | (top_bit << 22)
        c1 = codeword >> 11
        if _canonical_golay23127(c1) == codeword:
            candidates.append(codeword)

    if len(candidates) != 1:
        raise ValueError(
            f"legacy AMBE B block has {len(candidates)} valid Golay reconstructions"
        )

    repaired_b = candidates[0] ^ mask
    value = (a_block << 48) | (repaired_b << 25) | c_block
    repaired = value.to_bytes(AMBE_FRAME_BYTES, "big")
    validate_canonical_frame(repaired)
    return repaired
