"""Local DMR parrot/echo service for HBlink4.

The service records an accepted group-voice stream on one configured talkgroup
and replays the original DMRD frames only to the originating repeater/hotspot.
It deliberately never participates in normal HBlink4 fan-out or external trunk
routing.

HBlink4's core is kept unchanged by installing small method wrappers on
``HBProtocol`` from ``run.py``.  That keeps the feature isolated and easy to
remove or upstream later while preserving the existing hot-path implementation.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from time import time
from typing import Any, Dict, List, Optional, Tuple

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParrotSettings:
    enabled: bool = False
    talkgroup: int = 9990
    delay_seconds: float = 2.0
    packet_interval_seconds: float = 0.06
    max_duration_seconds: float = 120.0

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "ParrotSettings":
        raw = config.get("parrot", {})
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise ValueError("parrot configuration must be a JSON object")

        enabled = raw.get("enabled", False)
        talkgroup = raw.get("talkgroup", 9990)
        delay = raw.get("delay_seconds", 2.0)
        interval = raw.get("packet_interval_seconds", 0.06)
        maximum = raw.get("max_duration_seconds", 120.0)

        if not isinstance(enabled, bool):
            raise ValueError("parrot.enabled must be true or false")
        if isinstance(talkgroup, bool) or not isinstance(talkgroup, int):
            raise ValueError("parrot.talkgroup must be an integer")
        if not 1 <= talkgroup <= 0xFFFFFF:
            raise ValueError("parrot.talkgroup must be in the DMR ID range 1..16777215")

        def number(name: str, value: Any, minimum: float, maximum_value: float) -> float:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"parrot.{name} must be numeric")
            value = float(value)
            if not minimum <= value <= maximum_value:
                raise ValueError(
                    f"parrot.{name} must be between {minimum} and {maximum_value}"
                )
            return value

        return cls(
            enabled=enabled,
            talkgroup=talkgroup,
            delay_seconds=number("delay_seconds", delay, 0.0, 10.0),
            packet_interval_seconds=number(
                "packet_interval_seconds", interval, 0.02, 0.20
            ),
            max_duration_seconds=number(
                "max_duration_seconds", maximum, 1.0, 300.0
            ),
        )

    @property
    def talkgroup_bytes(self) -> bytes:
        return self.talkgroup.to_bytes(3, "big")

    @property
    def max_packets(self) -> int:
        # DMRD voice normally arrives every 60 ms. Allow a little framing
        # headroom above the configured audio duration without permitting an
        # unbounded in-memory recording.
        return int(self.max_duration_seconds / self.packet_interval_seconds) + 16


@dataclass
class ParrotRecording:
    repeater_id: bytes
    slot: int
    rf_src: bytes
    dst_id: bytes
    stream_id: bytes
    started_at: float
    packets: List[bytes] = field(default_factory=list)
    overflowed: bool = False

    @property
    def key(self) -> Tuple[bytes, int, bytes]:
        return (self.repeater_id, self.slot, self.stream_id)


class ParrotService:
    """Record bounded local parrot streams and return completed recordings."""

    def __init__(self, config: Dict[str, Any]):
        self.settings = ParrotSettings.from_config(config)
        self._recordings: Dict[Tuple[bytes, int, bytes], ParrotRecording] = {}
        self._playback_locks: Dict[Tuple[bytes, int], asyncio.Lock] = {}

        if self.settings.enabled:
            LOGGER.info(
                "[PARROT] enabled on TG%d (delay %.1fs, max %.0fs)",
                self.settings.talkgroup,
                self.settings.delay_seconds,
                self.settings.max_duration_seconds,
            )

    def is_network_talkgroup(self, dst_id: bytes) -> bool:
        return self.settings.enabled and dst_id == self.settings.talkgroup_bytes

    def capture(
        self,
        repeater_id: bytes,
        slot: int,
        rf_src: bytes,
        dst_id: bytes,
        stream_id: bytes,
        packet: bytes,
        is_terminator: bool,
    ) -> Optional[ParrotRecording]:
        """Capture one accepted DMRD frame.

        Returns the completed recording only when a proper voice terminator has
        arrived. Overlong recordings are discarded rather than replayed without
        a complete call boundary.
        """
        if not self.settings.enabled:
            return None

        key = (repeater_id, slot, stream_id)
        recording = self._recordings.get(key)
        if recording is None:
            recording = ParrotRecording(
                repeater_id=repeater_id,
                slot=slot,
                rf_src=rf_src,
                dst_id=dst_id,
                stream_id=stream_id,
                started_at=time(),
            )
            self._recordings[key] = recording
            LOGGER.info(
                "[PARROT] recording started repeater=%d TS%d src=%d stream=%s",
                int.from_bytes(repeater_id, "big"),
                slot,
                int.from_bytes(rf_src, "big"),
                stream_id.hex(),
            )

        if not recording.overflowed:
            if len(recording.packets) >= self.settings.max_packets:
                recording.overflowed = True
                recording.packets.clear()
                LOGGER.warning(
                    "[PARROT] recording exceeded %.0fs limit; discarding stream=%s",
                    self.settings.max_duration_seconds,
                    stream_id.hex(),
                )
            else:
                recording.packets.append(bytes(packet))

        if not is_terminator:
            return None

        self._recordings.pop(key, None)
        if recording.overflowed or not recording.packets:
            return None

        LOGGER.info(
            "[PARROT] recording complete repeater=%d TS%d packets=%d stream=%s",
            int.from_bytes(repeater_id, "big"),
            slot,
            len(recording.packets),
            stream_id.hex(),
        )
        return recording

    def discard(self, repeater_id: bytes, slot: int, stream_id: bytes, reason: str) -> None:
        recording = self._recordings.pop((repeater_id, slot, stream_id), None)
        if recording is not None:
            LOGGER.info(
                "[PARROT] recording discarded stream=%s reason=%s",
                stream_id.hex(),
                reason,
            )

    def discard_repeater(self, repeater_id: bytes) -> None:
        keys = [key for key in self._recordings if key[0] == repeater_id]
        for key in keys:
            self._recordings.pop(key, None)

    def playback_lock(self, repeater_id: bytes, slot: int) -> asyncio.Lock:
        key = (repeater_id, slot)
        lock = self._playback_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._playback_locks[key] = lock
        return lock


def install_parrot(protocol_cls) -> None:
    """Install the parrot hooks onto HBProtocol once.

    The wrappers intentionally touch only four narrow seams:
      * accept the configured local parrot TG even when it is absent from the
        normal repeater routing allow-list;
      * give that TG an empty normal route-cache so it never fans out;
      * record accepted local DMRD voice frames and schedule echo playback;
      * discard incomplete recordings when a stream times out or disconnects.
    """
    if getattr(protocol_cls, "_parrot_patch_installed", False):
        return

    original_init = protocol_cls.__init__
    original_check_inbound = protocol_cls._check_inbound_routing
    original_calculate_targets = protocol_cls._calculate_stream_targets
    original_handle_dmr = protocol_cls._handle_dmr_data
    original_end_stream = protocol_cls._end_stream
    original_remove_repeater = protocol_cls._remove_repeater

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self._parrot_service = ParrotService(self._config)

    def network_destination(self, repeater_id: bytes, slot: int, dst_id: bytes):
        repeater = self._repeaters.get(repeater_id)
        if repeater is not None and repeater.inbound_map:
            return repeater.inbound_map.get((slot, dst_id), (slot, dst_id))
        return (slot, dst_id)

    def is_local_parrot(self, repeater_id: bytes, slot: int, dst_id: bytes) -> bool:
        service: ParrotService = self._parrot_service
        if not service.settings.enabled:
            return False
        _net_slot, net_dst = network_destination(self, repeater_id, slot, dst_id)
        return service.is_network_talkgroup(net_dst)

    def patched_check_inbound(self, repeater_id: bytes, slot: int, dst_id: bytes) -> bool:
        if is_local_parrot(self, repeater_id, slot, dst_id):
            return True
        return original_check_inbound(self, repeater_id, slot, dst_id)

    def patched_calculate_targets(
        self, source, slot: int, dst_id: bytes, stream_id: bytes, rf_src: bytes
    ):
        service: ParrotService = self._parrot_service
        # Only local repeater/hotspot sources invoke the parrot. External
        # OpenBridge/outbound traffic using the same numeric TG remains normal
        # network traffic and cannot trigger an echo service.
        if isinstance(source, bytes) and service.is_network_talkgroup(dst_id):
            return set()
        return original_calculate_targets(self, source, slot, dst_id, stream_id, rf_src)

    async def playback(self, recording: ParrotRecording) -> None:
        service: ParrotService = self._parrot_service
        await asyncio.sleep(service.settings.delay_seconds)

        async with service.playback_lock(recording.repeater_id, recording.slot):
            repeater = self._repeaters.get(recording.repeater_id)
            if repeater is None or repeater.connection_state != "connected":
                LOGGER.info(
                    "[PARROT] playback cancelled; originating repeater disconnected stream=%s",
                    recording.stream_id.hex(),
                )
                return

            current = repeater.get_slot_stream(recording.slot)
            if (
                current is None
                or current.stream_id != recording.stream_id
                or not current.ended
            ):
                LOGGER.info(
                    "[PARROT] playback cancelled; originating slot was reused stream=%s",
                    recording.stream_id.hex(),
                )
                return

            LOGGER.info(
                "[PARROT] playback started repeater=%d TS%d packets=%d stream=%s",
                int.from_bytes(recording.repeater_id, "big"),
                recording.slot,
                len(recording.packets),
                recording.stream_id.hex(),
            )

            for index, packet in enumerate(recording.packets):
                repeater = self._repeaters.get(recording.repeater_id)
                if repeater is None or repeater.connection_state != "connected":
                    LOGGER.info(
                        "[PARROT] playback stopped; repeater disconnected stream=%s",
                        recording.stream_id.hex(),
                    )
                    return

                current = repeater.get_slot_stream(recording.slot)
                if current is None or current.stream_id != recording.stream_id:
                    LOGGER.info(
                        "[PARROT] playback stopped; slot became active stream=%s",
                        recording.stream_id.hex(),
                    )
                    return

                self._send_packet(packet, repeater.sockaddr)
                if index + 1 < len(recording.packets):
                    await asyncio.sleep(service.settings.packet_interval_seconds)

            LOGGER.info(
                "[PARROT] playback complete repeater=%d TS%d stream=%s",
                int.from_bytes(recording.repeater_id, "big"),
                recording.slot,
                recording.stream_id.hex(),
            )

    def schedule_playback(self, recording: ParrotRecording) -> None:
        task = asyncio.create_task(playback(self, recording))
        self._tasks.append(task)

        def cleanup(done_task) -> None:
            try:
                self._tasks.remove(done_task)
            except ValueError:
                pass
            try:
                done_task.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                LOGGER.exception("[PARROT] playback task failed")

        task.add_done_callback(cleanup)

    def patched_handle_dmr(self, data: bytes, addr) -> None:
        packet = self._parse_dmr_packet(data)
        original_handle_dmr(self, data, addr)
        if not packet:
            return

        repeater_id = packet["repeater_id"]
        slot = packet["slot"]
        dst_id = packet["dst_id"]
        call_type_bit = packet["call_type"]

        # Parrot is group voice only and only for a locally connected source.
        if call_type_bit != 0 or not is_local_parrot(self, repeater_id, slot, dst_id):
            return

        repeater = self._repeaters.get(repeater_id)
        if repeater is None or repeater.connection_state != "connected":
            return
        current = repeater.get_slot_stream(slot)
        if (
            current is None
            or current.stream_id != packet["stream_id"]
            or current.call_type == "data"
        ):
            return

        is_terminator = self._is_dmr_terminator(data, packet["frame_type"])
        recording = self._parrot_service.capture(
            repeater_id=repeater_id,
            slot=slot,
            rf_src=packet["rf_src"],
            dst_id=dst_id,
            stream_id=packet["stream_id"],
            packet=data,
            is_terminator=is_terminator,
        )
        if recording is not None:
            schedule_playback(self, recording)

    def patched_end_stream(self, stream, repeater_id: bytes, slot: int, end_time, reason: str):
        result = original_end_stream(self, stream, repeater_id, slot, end_time, reason)
        if reason != "terminator" and is_local_parrot(self, repeater_id, slot, stream.dst_id):
            self._parrot_service.discard(repeater_id, slot, stream.stream_id, reason)
        return result

    def patched_remove_repeater(self, repeater_id: bytes, reason: str) -> None:
        if hasattr(self, "_parrot_service"):
            self._parrot_service.discard_repeater(repeater_id)
        return original_remove_repeater(self, repeater_id, reason)

    protocol_cls.__init__ = patched_init
    protocol_cls._check_inbound_routing = patched_check_inbound
    protocol_cls._calculate_stream_targets = patched_calculate_targets
    protocol_cls._handle_dmr_data = patched_handle_dmr
    protocol_cls._end_stream = patched_end_stream
    protocol_cls._remove_repeater = patched_remove_repeater
    protocol_cls._parrot_patch_installed = True
