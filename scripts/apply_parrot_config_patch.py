#!/usr/bin/env python3
"""One-shot configuration/observability patch helper; removed before review."""

from pathlib import Path


def replace_once(path_name, old, new, label):
    path = Path(path_name)
    text = path.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path_name}: {label}: expected one match, found {count}")
    path.write_text(text.replace(old, new, 1))


replace_once(
    "hblink4/parrot.py",
    "LOGGER = logging.getLogger(__name__)",
    'LOGGER = logging.getLogger("hblink4.hblink.parrot")',
    "configured logger hierarchy",
)

replace_once(
    "config/config_sample.json",
    '        "packet_interval_seconds": 0.06,\n        "max_duration_seconds": 120.0',
    '        "packet_interval_seconds": 0.06,\n        "max_duration_seconds": 120.0,\n        "voice_telemetry_enabled": false,\n        "voice_telemetry_source_id": 9990,\n        "voice_telemetry_pause_seconds": 0.45',
    "voice telemetry sample settings",
)
