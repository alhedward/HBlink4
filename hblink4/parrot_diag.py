"""Temporary deterministic TG9990 voice-report diagnostic.

This module deliberately changes only the spoken token selection used by the
parrot voice telemetry path. It leaves the AMBE assets, DMR packet builder,
colour code, slot handling, source/destination IDs, timing and admin On/Off
control untouched so an RF capture can isolate the generated voice stream.

Remove this module and its run.py hook after the diagnostic capture is complete.
"""

from __future__ import annotations

import logging
from typing import List, Mapping, Optional


LOGGER = logging.getLogger("hblink4.hblink.parrot")

DIAGNOSTIC_TOKENS = (
    "bit_error_rate",
    "number_0",
    "point",
    "number_4",
    "percent",
    "received_signal_strength_indication",
    "minus",
    "number_72",
    "dbm",
    "timeslot",
    "number_2",
    "seventy_threes",
)


def fixed_telemetry_tokens(
    _rf_quality: Optional[Mapping[str, object]], _slot: int
) -> List[str]:
    """Return the fixed diagnostic phrase regardless of measured RF values."""

    return list(DIAGNOSTIC_TOKENS)


def install_fixed_parrot_voice_report() -> None:
    """Install the temporary deterministic report into parrot_voice."""

    from . import parrot_voice

    parrot_voice.telemetry_tokens = fixed_telemetry_tokens
    LOGGER.warning(
        "[PARROT] DIAGNOSTIC voice report enabled: BER 0.4%%, RSSI -72 dBm, TS2"
    )
