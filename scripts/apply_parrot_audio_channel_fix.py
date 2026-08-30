#!/usr/bin/env python3
"""Apply the parrot AMBE channel-coding source/test updates on the feature branch."""

from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


# Runtime: canonical OpenDMR -> protected voice parameters -> DMR on-air frame.
path = Path("hblink4/parrot_voice.py")
text = path.read_text()
text = replace_once(
    text,
    "from .lc import LC_OPT_GROUP_DEFAULT\n",
    "from .parrot_ambe import canonical_to_dmr_frame\nfrom .lc import LC_OPT_GROUP_DEFAULT\n",
    "parrot voice import",
)
start = text.index("# DMR AMBE+2 on-air interleave schedule.")
end = text.index("# HomeBrew DMRD byte-15 values", start)
text = (
    text[:start]
    + "# OpenDMR assets are canonical A+B+C frames. Conversion to DMR on-air\n"
      "# channel coding is isolated in parrot_ambe.py and validated independently.\n"
    + text[end:]
)
start = text.index("def _interleave_ambe_frame(frame: bytes) -> bytes:")
end = text.index("\ndef assemble_ambe_frames", start)
text = (
    text[:start]
    + "def _interleave_ambe_frame(frame: bytes) -> bytes:\n"
      "    \"\"\"Convert one validated OpenDMR canonical frame to DMR on-air order.\"\"\"\n\n"
      "    return canonical_to_dmr_frame(frame)\n\n"
    + text[end:]
)
path.write_text(text)


# Asset loader: require corrected provenance and validate every canonical frame.
path = Path("hblink4/parrot_voice_assets.py")
text = path.read_text()
text = replace_once(
    text,
    "from typing import Any, Dict\n\n\nOPENDMR_COMMIT",
    "from typing import Any, Dict\n\nfrom .parrot_ambe import validate_canonical_frame\n\n\nOPENDMR_COMMIT",
    "asset validator import",
)
text = replace_once(
    text,
    "        if manifest.get(\"opendmr_commit\") != OPENDMR_COMMIT:\n"
    "            raise RuntimeError(\"bundled parrot voice archive was encoded with an unexpected OpenDMR revision\")\n\n"
    "        metadata = manifest.get(\"assets\")",
    "        if manifest.get(\"opendmr_commit\") != OPENDMR_COMMIT:\n"
    "            raise RuntimeError(\"bundled parrot voice archive was encoded with an unexpected OpenDMR revision\")\n"
    "        channel_coding = manifest.get(\"channel_coding\") or {}\n"
    "        if channel_coding.get(\"b_block\") != \"golay23-right-aligned-before-prng\":\n"
    "            raise RuntimeError(\"bundled parrot voice archive has unverified B-block channel coding\")\n\n"
    "        metadata = manifest.get(\"assets\")",
    "asset manifest channel marker",
)
text = replace_once(
    text,
    "            if entry.get(\"frames\") != len(payload) // 9:\n"
    "                raise RuntimeError(f\"parrot voice asset {name!r} frame count does not match manifest\")\n"
    "            assets[name] = payload",
    "            if entry.get(\"frames\") != len(payload) // 9:\n"
    "                raise RuntimeError(f\"parrot voice asset {name!r} frame count does not match manifest\")\n"
    "            try:\n"
    "                for offset in range(0, len(payload), 9):\n"
    "                    validate_canonical_frame(payload[offset:offset + 9])\n"
    "            except ValueError as exc:\n"
    "                raise RuntimeError(f\"parrot voice asset {name!r} has invalid channel coding: {exc}\") from exc\n"
    "            assets[name] = payload",
    "asset frame validation",
)
path.write_text(text)


# Future generator: fail closed if an unpatched OpenDMR codec is used.
path = Path("scripts/generate_parrot_voice_assets.py")
text = path.read_text()
text = replace_once(
    text,
    "from num2words import num2words\n\nSAMPLE_RATE",
    "from num2words import num2words\n\nfrom hblink4.parrot_ambe import validate_canonical_frame\n\nSAMPLE_RATE",
    "generator validator import",
)
text = replace_once(
    text,
    "    if not payload or len(payload) % AMBE_FRAME_BYTES:\n"
    "        raise RuntimeError(f\"OpenDMR produced invalid AMBE data for {text!r}\")\n"
    "    frames = len(payload) // AMBE_FRAME_BYTES",
    "    if not payload or len(payload) % AMBE_FRAME_BYTES:\n"
    "        raise RuntimeError(f\"OpenDMR produced invalid AMBE data for {text!r}\")\n"
    "    try:\n"
    "        for offset in range(0, len(payload), AMBE_FRAME_BYTES):\n"
    "            validate_canonical_frame(payload[offset:offset + AMBE_FRAME_BYTES])\n"
    "    except ValueError as exc:\n"
    "        raise RuntimeError(\n"
    "            \"OpenDMR emitted invalid DMR B-block channel coding. The pinned \"\n"
    "            \"encoder requires encode23127(c1) >> 1 before applying the PRNG mask.\"\n"
    "        ) from exc\n"
    "    frames = len(payload) // AMBE_FRAME_BYTES",
    "generator channel validation",
)
text = replace_once(
    text,
    "            \"opendmr_commit\": OPENDMR_COMMIT,\n"
    "            \"source_tts\": {",
    "            \"opendmr_commit\": OPENDMR_COMMIT,\n"
    "            \"channel_coding\": {\n"
    "                \"canonical_format\": \"A24+B23+C25\",\n"
    "                \"b_block\": \"golay23-right-aligned-before-prng\",\n"
    "                \"reference\": \"MMDVM-Host AMBEFEC.cpp: encode23127(datb) >> 1 before PRNG\",\n"
    "            },\n"
    "            \"source_tts\": {",
    "generator manifest marker",
)
path.write_text(text)


# Regression: replace the self-derived silence vector with the real canonical
# MMDVM/OpenDMR vector and pin the lossless legacy repair.
path = Path("tests/test_parrot_voice.py")
text = path.read_text()
text = replace_once(
    text,
    "from hblink4.lc import decode_lc_from_vhead\n",
    "from hblink4.lc import decode_lc_from_vhead\n"
    "from hblink4.parrot_ambe import repair_legacy_opendmr_frame, validate_canonical_frame\n",
    "voice test imports",
)
old_test = '''def test_opendmr_canonical_silence_interleaves_to_dmr_reference_frame():\n    # dmr_utils3.ambe_utils documents ACAA40200044408080 as the standard\n    # on-air AMBE silence frame. Deinterleaving it yields this canonical\n    # A+B+C2+C3 frame, which is the representation emitted by OpenDMR.\n    canonical = bytes.fromhex("49400f09a0e0000000")\n    assert _interleave_ambe_frame(canonical) == bytes.fromhex(\n        "acaa40200044408080"\n    )\n\n\n'''
new_test = '''def test_opendmr_canonical_silence_converts_to_dmr_reference_frame():\n    # MMDVM-Host's DMR FEC fallback gives canonical A=F00292, B=0E0B20,\n    # C=0 for the standard silence voice parameters. dmr_utils3 documents\n    # ACAA40200044408080 as the corresponding DMR on-air AMBE frame.\n    canonical = bytes.fromhex("f002920e0b20000000")\n    validate_canonical_frame(canonical)\n    assert _interleave_ambe_frame(canonical) == bytes.fromhex(\n        "acaa40200044408080"\n    )\n\n\ndef test_legacy_opendmr_b_block_is_losslessly_repaired():\n    legacy = bytes.fromhex("1230acbe6856000000")\n    corrected = bytes.fromhex("1230acbe4168000000")\n    assert repair_legacy_opendmr_frame(legacy) == corrected\n    validate_canonical_frame(corrected)\n\n\n'''
text = replace_once(text, old_test, new_test, "silence/reference test")
path.write_text(text)


path = Path("tests/test_parrot_voice_assets.py")
text = path.read_text()
text = replace_once(
    text,
    "from hblink4.parrot_voice_assets import AMBE_ASSETS, ASSET_MANIFEST, OPENDMR_COMMIT\n",
    "from hblink4.parrot_ambe import validate_canonical_frame\n"
    "from hblink4.parrot_voice_assets import AMBE_ASSETS, ASSET_MANIFEST, OPENDMR_COMMIT\n",
    "asset test import",
)
text = replace_once(
    text,
    "    assert ASSET_MANIFEST[\"opendmr_commit\"] == OPENDMR_COMMIT\n"
    "    assert ASSET_MANIFEST[\"source_tts\"][\"lang\"] == \"en\"",
    "    assert ASSET_MANIFEST[\"opendmr_commit\"] == OPENDMR_COMMIT\n"
    "    assert ASSET_MANIFEST[\"channel_coding\"][\"b_block\"] == (\n"
    "        \"golay23-right-aligned-before-prng\"\n"
    "    )\n"
    "    for payload in AMBE_ASSETS.values():\n"
    "        for offset in range(0, len(payload), 9):\n"
    "            validate_canonical_frame(payload[offset:offset + 9])\n"
    "    assert ASSET_MANIFEST[\"source_tts\"][\"lang\"] == \"en\"",
    "asset test channel marker",
)
path.write_text(text)


# Documentation: record the actual codec alignment issue and correct conversion.
path = Path("docs/parrot_voice_telemetry.md")
text = path.read_text()
text = replace_once(
    text,
    "The encoder is pinned to OpenDMR commit:\n\n"
    "`d28164b39ba4d91ad5948ff22707937f8944f70f`\n\n"
    "A typical build environment needs gTTS, num2words, ffmpeg, a C/C++ build toolchain and the pinned OpenDMR source. None of those are production runtime dependencies.",
    "The encoder is pinned to OpenDMR commit:\n\n"
    "`d28164b39ba4d91ad5948ff22707937f8944f70f`\n\n"
    "That revision's encoder must be built with one DMR channel-coding correction: the result of `CGolay24128::encode23127(c1)` is 24-bit aligned, so it must be shifted right once before the 23-bit PRNG mask is applied. This matches MMDVM-Host's `AMBEFEC.cpp` (`encode23127(datb) >> 1`). The asset generator validates every encoded A/B block and fails closed if an uncorrected codec is used.\n\n"
    "The originally generated vocabulary was migrated losslessly: Golay redundancy uniquely reconstructed the dropped B-block bit in every frame, so the Australian-English speech/vocoder parameters were preserved without re-running TTS or the vocoder.\n\n"
    "A typical build environment needs gTTS, num2words, ffmpeg, a C/C++ build toolchain and the pinned OpenDMR source. None of those are production runtime dependencies.",
    "documentation encoder correction",
)
text = replace_once(
    text,
    "OpenDMR emits canonical/DVSI 72-bit AMBE frames in A+B+C order. HomeBrew DMR voice payloads carry the channel-coded voice bits in the DMR interleaved order. `hblink4/parrot_voice.py` applies the standard 36-dibit DMR AMBE interleave schedule before placing three 20 ms AMBE frames into each 60 ms voice burst.\n\n"
    "Regression coverage includes the standard DMR silence reference: canonical `49400f09a0e0000000` must interleave to `acaa40200044408080`. The generated call also verifies Voice Header/Terminator Link Control, Colour Code, timeslot, HomeBrew packet shape and A-F voice sequencing.",
    "OpenDMR emits canonical/DVSI 72-bit AMBE frames in A(24)+B(23)+C(25) order. Those serial codewords are not the same representation as the historical DMR/DSD interleave matrix. HBlink4 therefore decodes the clean canonical frame to its 49 protected voice-parameter bits and re-encodes those parameters with the established `dmr_utils3.ambe_utils` DMR Golay/whitening/interleave rules before placing three 20 ms frames into each 60 ms voice burst.\n\n"
    "Regression coverage uses an independent standard-silence oracle: canonical `f002920e0b20000000` must convert to DMR on-air `acaa40200044408080`. A second vector proves lossless repair of the legacy OpenDMR B-block alignment. The generated call also verifies Voice Header/Terminator Link Control, Colour Code, timeslot, HomeBrew packet shape and A-F voice sequencing.",
    "documentation DMR conversion",
)
path.write_text(text)

print("source/test/documentation patches applied")
