#!/usr/bin/env python3
"""One-shot feature-branch patch helper; removed before review."""

from pathlib import Path


path = Path("hblink4/parrot.py")
text = path.read_text()


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    text = text.replace(old, new, 1)


replace_once(
    "from typing import Any, Dict, List, Optional, Tuple\n\nLOGGER = logging.getLogger(__name__)",
    """from typing import Any, Dict, List, Optional, Tuple

from .parrot_voice import (
    DMR_VOICE_INTERVAL_SECONDS,
    VoiceTelemetrySettings,
    build_telemetry_packets,
    extract_colour_code,
    load_bundled_assets,
)

LOGGER = logging.getLogger(__name__)""",
    "voice telemetry imports",
)

replace_once(
    """    max_duration_seconds: float = 120.0

    @classmethod""",
    """    max_duration_seconds: float = 120.0
    voice_telemetry: VoiceTelemetrySettings = field(
        default_factory=VoiceTelemetrySettings
    )

    @classmethod""",
    "ParrotSettings voice field",
)

replace_once(
    """            max_duration_seconds=number(
                \"max_duration_seconds\", maximum, 1.0, 300.0
            ),
        )""",
    """            max_duration_seconds=number(
                \"max_duration_seconds\", maximum, 1.0, 300.0
            ),
            voice_telemetry=VoiceTelemetrySettings.from_parrot_config(
                raw, talkgroup
            ),
        )""",
    "ParrotSettings voice parser",
)

replace_once(
    """        self._recordings: Dict[Tuple[bytes, int, bytes], ParrotRecording] = {}
        self._playback_locks: Dict[Tuple[bytes, int], asyncio.Lock] = {}

        if self.settings.enabled:""",
    """        self._recordings: Dict[Tuple[bytes, int, bytes], ParrotRecording] = {}
        self._playback_locks: Dict[Tuple[bytes, int], asyncio.Lock] = {}
        self._voice_assets = (
            load_bundled_assets()
            if self.settings.voice_telemetry.enabled
            else {}
        )

        if self.settings.enabled:""",
    "ParrotService asset loader",
)

replace_once(
    """            LOGGER.info(
                \"[PARROT] enabled on TG%d (delay %.1fs, max %.0fs)\",
                self.settings.talkgroup,
                self.settings.delay_seconds,
                self.settings.max_duration_seconds,
            )

    def is_network_talkgroup""",
    """            LOGGER.info(
                \"[PARROT] enabled on TG%d (delay %.1fs, max %.0fs)\",
                self.settings.talkgroup,
                self.settings.delay_seconds,
                self.settings.max_duration_seconds,
            )
            if self.settings.voice_telemetry.enabled:
                if self._voice_assets:
                    LOGGER.info(
                        \"[PARROT] voice telemetry enabled with %d AMBE assets\",
                        len(self._voice_assets),
                    )
                else:
                    LOGGER.warning(
                        \"[PARROT] voice telemetry enabled but AMBE assets are unavailable\"
                    )

    def is_network_talkgroup""",
    "ParrotService telemetry logging",
)

replace_once(
    "\n\ndef install_parrot(protocol_cls) -> None:\n",
    """

def _slot_conflicts_with_parrot(current: Any, original_stream_id: bytes) -> bool:
    \"\"\"True only when the originating slot is genuinely occupied/reused.

    ``None`` means normal HBlink4 hang-time cleanup has released the slot and is
    safe for continued parrot playback. The old implementation treated that
    normal cleanup as a cancellation, so long echoes failed once playback
    outlived the stream-state hang time.
    \"\"\"
    return current is not None and (
        current.stream_id != original_stream_id or not current.ended
    )


def install_parrot(protocol_cls) -> None:
""",
    "slot conflict helper",
)

replace_once(
    """            current = repeater.get_slot_stream(recording.slot)
            if (
                current is None
                or current.stream_id != recording.stream_id
                or not current.ended
            ):""",
    """            current = repeater.get_slot_stream(recording.slot)
            if _slot_conflicts_with_parrot(current, recording.stream_id):""",
    "pre-playback slot guard",
)

replace_once(
    """                current = repeater.get_slot_stream(recording.slot)
                if current is None or current.stream_id != recording.stream_id:
                    LOGGER.info(
                        \"[PARROT] playback stopped; slot became active stream=%s\",""",
    """                current = repeater.get_slot_stream(recording.slot)
                if _slot_conflicts_with_parrot(current, recording.stream_id):
                    LOGGER.info(
                        \"[PARROT] playback stopped; slot was reused stream=%s\",""",
    "during-playback slot guard",
)

replace_once(
    '                        {**activity, "reason": "slot became active during playback"},',
    '                        {**activity, "reason": "slot was reused during playback"},',
    "playback cancellation reason",
)

replace_once(
    """                self._send_packet(packet, repeater.sockaddr)
                if index + 1 < len(recording.packets):
                    await asyncio.sleep(service.settings.packet_interval_seconds)

            LOGGER.info(
                \"[PARROT] playback complete repeater=%d TS%d stream=%s\",""",
    """                self._send_packet(packet, repeater.sockaddr)
                if index + 1 < len(recording.packets):
                    await asyncio.sleep(service.settings.packet_interval_seconds)

            telemetry_status = \"disabled\"
            voice = service.settings.voice_telemetry
            if voice.enabled:
                telemetry_status = \"unavailable\"
                if service._voice_assets:
                    await asyncio.sleep(voice.pause_after_echo_seconds)
                    repeater = self._repeaters.get(recording.repeater_id)
                    current = (
                        repeater.get_slot_stream(recording.slot)
                        if repeater is not None
                        and repeater.connection_state == \"connected\"
                        else None
                    )
                    if repeater is None or repeater.connection_state != \"connected\":
                        telemetry_status = \"cancelled\"
                        _emit_parrot_event(
                            self,
                            \"parrot_telemetry_cancelled\",
                            {
                                **activity,
                                \"reason\": \"repeater disconnected before voice report\",
                                \"voice_telemetry_status\": telemetry_status,
                            },
                        )
                    elif _slot_conflicts_with_parrot(current, recording.stream_id):
                        telemetry_status = \"cancelled\"
                        _emit_parrot_event(
                            self,
                            \"parrot_telemetry_cancelled\",
                            {
                                **activity,
                                \"reason\": \"slot became active before voice report\",
                                \"voice_telemetry_status\": telemetry_status,
                            },
                        )
                    else:
                        try:
                            colour_code = extract_colour_code(recording.packets)
                            _tokens, telemetry_packets = build_telemetry_packets(
                                rf_quality=activity.get(\"rf_quality\"),
                                slot=recording.slot,
                                dst_id=recording.dst_id,
                                repeater_id=recording.repeater_id,
                                colour_code=colour_code,
                                source_id=voice.source_id_bytes,
                                assets=service._voice_assets,
                            )
                            telemetry_status = \"playing\"
                            _emit_parrot_event(
                                self,
                                \"parrot_telemetry_started\",
                                {
                                    **activity,
                                    \"voice_telemetry_status\": telemetry_status,
                                },
                            )

                            for telemetry_index, telemetry_packet in enumerate(
                                telemetry_packets
                            ):
                                repeater = self._repeaters.get(recording.repeater_id)
                                if (
                                    repeater is None
                                    or repeater.connection_state != \"connected\"
                                ):
                                    telemetry_status = \"cancelled\"
                                    _emit_parrot_event(
                                        self,
                                        \"parrot_telemetry_cancelled\",
                                        {
                                            **activity,
                                            \"reason\": \"repeater disconnected during voice report\",
                                            \"voice_telemetry_status\": telemetry_status,
                                        },
                                    )
                                    break

                                current = repeater.get_slot_stream(recording.slot)
                                if _slot_conflicts_with_parrot(
                                    current, recording.stream_id
                                ):
                                    telemetry_status = \"cancelled\"
                                    _emit_parrot_event(
                                        self,
                                        \"parrot_telemetry_cancelled\",
                                        {
                                            **activity,
                                            \"reason\": \"slot became active during voice report\",
                                            \"voice_telemetry_status\": telemetry_status,
                                        },
                                    )
                                    break

                                self._send_packet(
                                    telemetry_packet, repeater.sockaddr
                                )
                                if telemetry_index + 1 < len(telemetry_packets):
                                    await asyncio.sleep(DMR_VOICE_INTERVAL_SECONDS)
                            else:
                                telemetry_status = \"complete\"
                                _emit_parrot_event(
                                    self,
                                    \"parrot_telemetry_complete\",
                                    {
                                        **activity,
                                        \"voice_telemetry_status\": telemetry_status,
                                    },
                                )
                        except Exception:
                            telemetry_status = \"failed\"
                            LOGGER.exception(
                                \"[PARROT] voice telemetry generation/playback failed\"
                            )
                            _emit_parrot_event(
                                self,
                                \"parrot_telemetry_cancelled\",
                                {
                                    **activity,
                                    \"reason\": \"voice report unavailable\",
                                    \"voice_telemetry_status\": telemetry_status,
                                },
                            )

            LOGGER.info(
                \"[PARROT] playback complete repeater=%d TS%d stream=%s\",""",
    "voice telemetry playback integration",
)

replace_once(
    '            _emit_parrot_event(self, "parrot_playback_complete", activity)\n',
    """            _emit_parrot_event(
                self,
                \"parrot_playback_complete\",
                {**activity, \"voice_telemetry_status\": telemetry_status},
            )
""",
    "final playback event telemetry status",
)

path.write_text(text)
