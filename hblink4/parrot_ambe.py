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

from typing import List, Sequence, Tuple


AMBE_FRAME_BYTES = 9

# OpenDMR opendmr.cpp historically serialized b[0..8] contiguously instead of
# using the encoder's own encode_49bit() mapping. All bundled HBlink4 voice
# assets were produced by that pinned wrapper revision, so their nine encoder
# parameters can be recovered losslessly from this legacy layout.
_LEGACY_PARAMETER_WIDTHS = (7, 5, 5, 9, 7, 5, 4, 4, 3)

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


def _raw49_to_canonical_frame(raw: Sequence[int]) -> bytes:
    """Build clean canonical A24+B23+C25 channel coding from 49 voice bits."""

    if len(raw) != 49 or any(bit not in (0, 1) for bit in raw):
        raise ValueError("raw AMBE voice parameters must be exactly 49 bits")

    c0 = 0
    c1 = 0
    c_block = 0
    for bit in raw[:12]:
        c0 = (c0 << 1) | bit
    for bit in raw[12:24]:
        c1 = (c1 << 1) | bit
    for bit in raw[24:]:
        c_block = (c_block << 1) | bit

    a_block = _canonical_golay24128(c0)
    b_block = _canonical_golay23127(c1) ^ _canonical_prng_mask(c0)
    value = (a_block << 48) | (b_block << 25) | c_block
    frame = value.to_bytes(AMBE_FRAME_BYTES, "big")
    validate_canonical_frame(frame)
    return frame


def _legacy_raw49_to_parameters(raw: Sequence[int]) -> Tuple[int, ...]:
    """Recover b[0..8] from OpenDMR wrapper's historical sequential layout."""

    if len(raw) != 49 or any(bit not in (0, 1) for bit in raw):
        raise ValueError("raw AMBE voice parameters must be exactly 49 bits")

    values = []
    offset = 0
    for width in _LEGACY_PARAMETER_WIDTHS:
        value = 0
        for bit in raw[offset:offset + width]:
            value = (value << 1) | bit
        values.append(value)
        offset += width
    return tuple(values)


def _parameters_to_encoder_raw49(parameters: Sequence[int]) -> List[int]:
    """Apply OpenDMR/OP25 MBEEncoder encode_49bit() bit placement."""

    if len(parameters) != 9:
        raise ValueError("AMBE encoder parameter set must contain exactly nine values")
    b = [int(value) for value in parameters]
    limits = tuple((1 << width) - 1 for width in _LEGACY_PARAMETER_WIDTHS)
    if any(value < 0 or value > limit for value, limit in zip(b, limits)):
        raise ValueError("AMBE encoder parameter value is outside its encoded width")

    raw = [0] * 49
    raw[0:4] = [(b[0] >> bit) & 1 for bit in (6, 5, 4, 3)]
    raw[4:8] = [(b[1] >> bit) & 1 for bit in (4, 3, 2, 1)]
    raw[8:12] = [(b[2] >> bit) & 1 for bit in (4, 3, 2, 1)]
    raw[12:20] = [(b[3] >> bit) & 1 for bit in (8, 7, 6, 5, 4, 3, 2, 1)]
    raw[20:24] = [(b[4] >> bit) & 1 for bit in (6, 5, 4, 3)]
    raw[24:28] = [(b[5] >> bit) & 1 for bit in (4, 3, 2, 1)]
    raw[28:31] = [(b[6] >> bit) & 1 for bit in (3, 2, 1)]
    raw[31:34] = [(b[7] >> bit) & 1 for bit in (3, 2, 1)]
    raw[34] = (b[8] >> 2) & 1
    raw[35] = b[1] & 1
    raw[36] = b[2] & 1
    raw[37:40] = [(b[0] >> bit) & 1 for bit in (2, 1, 0)]
    raw[40] = b[3] & 1
    raw[41:44] = [(b[4] >> bit) & 1 for bit in (2, 1, 0)]
    raw[44] = b[5] & 1
    raw[45] = b[6] & 1
    raw[46] = b[7] & 1
    raw[47:49] = [(b[8] >> bit) & 1 for bit in (1, 0)]
    return raw


def repair_legacy_opendmr_parameter_packing(frame: bytes) -> bytes:
    """Losslessly repair OpenDMR wrapper's historical 49-bit packing defect.

    Pinned OpenDMR ``opendmr.cpp`` wrote the nine encoder b[] values as simple
    contiguous fields. Its own OP25-derived ``MBEEncoder::encode_49bit()``
    deliberately scatters several low-order bits into positions 35..48. The
    wrapper therefore produced valid Golay/FEC around voice bits with the wrong
    semantic placement. Recover the original b[] values from that sequential
    representation, apply the encoder's canonical mapping, and rebuild FEC.
    """

    legacy_raw = _canonical_to_raw49(frame)
    parameters = _legacy_raw49_to_parameters(legacy_raw)
    corrected_raw = _parameters_to_encoder_raw49(parameters)
    return _raw49_to_canonical_frame(corrected_raw)


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
