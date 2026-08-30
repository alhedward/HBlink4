#!/usr/bin/env python3
"""Align generated parrot voice LC with HBlink4's canonical group-voice options."""

from pathlib import Path

path = Path("hblink4/parrot_voice.py")
text = path.read_text()

replacements = [
    (
        "dmr_utils const import",
        "from dmr_utils3.const import BS_DATA_SYNC, BS_VOICE_SYNC, LC_OPT\n",
        "from dmr_utils3.const import BS_DATA_SYNC, BS_VOICE_SYNC\n\nfrom .lc import LC_OPT_GROUP_DEFAULT\n",
    ),
    (
        "generated LC options",
        "    lc = LC_OPT + dst_id + rf_src\n",
        "    lc = LC_OPT_GROUP_DEFAULT + dst_id + rf_src\n",
    ),
]

for label, old, new in replacements:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    text = text.replace(old, new, 1)

path.write_text(text)
