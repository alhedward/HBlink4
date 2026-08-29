from types import SimpleNamespace

import pytest

from hblink4.parrot import ParrotService, ParrotSettings, install_parrot


RID = (5050001).to_bytes(4, "big")
TG9990 = (9990).to_bytes(3, "big")
TG8 = (8).to_bytes(3, "big")
SRC = (5051234).to_bytes(3, "big")
STREAM = b"\x12\x34\x56\x78"


def parrot_config(**overrides):
    values = {
        "enabled": True,
        "talkgroup": 9990,
        "delay_seconds": 2.0,
        "packet_interval_seconds": 0.06,
        "max_duration_seconds": 120.0,
    }
    values.update(overrides)
    return {"parrot": values}


def test_parrot_settings_validate_bounds():
    settings = ParrotSettings.from_config(parrot_config())
    assert settings.enabled is True
    assert settings.talkgroup == 9990
    assert settings.talkgroup_bytes == TG9990

    with pytest.raises(ValueError):
        ParrotSettings.from_config(parrot_config(talkgroup=0))
    with pytest.raises(ValueError):
        ParrotSettings.from_config(parrot_config(packet_interval_seconds=0.001))
    with pytest.raises(ValueError):
        ParrotSettings.from_config({"parrot": "not-an-object"})


def test_parrot_records_through_voice_terminator():
    service = ParrotService(parrot_config())
    first = b"DMRD-first"
    final = b"DMRD-terminator"

    assert service.capture(RID, 2, SRC, TG9990, STREAM, first, False) is None
    recording = service.capture(RID, 2, SRC, TG9990, STREAM, final, True)

    assert recording is not None
    assert recording.repeater_id == RID
    assert recording.slot == 2
    assert recording.rf_src == SRC
    assert recording.dst_id == TG9990
    assert recording.stream_id == STREAM
    assert recording.packets == [first, final]
    assert service._recordings == {}


def test_parrot_discards_overlong_recording():
    service = ParrotService(
        parrot_config(max_duration_seconds=1.0, packet_interval_seconds=0.20)
    )

    for index in range(service.settings.max_packets + 1):
        service.capture(
            RID,
            1,
            SRC,
            TG9990,
            STREAM,
            f"packet-{index}".encode(),
            False,
        )

    assert service.capture(
        RID, 1, SRC, TG9990, STREAM, b"terminator", True
    ) is None
    assert service._recordings == {}


def test_parrot_timeout_discard_releases_recording():
    service = ParrotService(parrot_config())
    service.capture(RID, 1, SRC, TG9990, STREAM, b"packet", False)
    assert service._recordings

    service.discard(RID, 1, STREAM, "stream_timeout")
    assert service._recordings == {}


def test_protocol_hooks_accept_local_parrot_but_never_route_it():
    class DummyProtocol:
        def __init__(self, config):
            self._config = config
            self._repeaters = {
                RID: SimpleNamespace(inbound_map={}, connection_state="connected")
            }
            self._tasks = []

        def _check_inbound_routing(self, repeater_id, slot, dst_id):
            return False

        def _calculate_stream_targets(self, source, slot, dst_id, stream_id, rf_src):
            return {b"normal-target"}

        def _handle_dmr_data(self, data, addr):
            return None

        def _end_stream(self, stream, repeater_id, slot, end_time, reason):
            return None

        def _remove_repeater(self, repeater_id, reason):
            self._repeaters.pop(repeater_id, None)

    install_parrot(DummyProtocol)
    protocol = DummyProtocol(parrot_config())

    assert protocol._check_inbound_routing(RID, 1, TG9990) is True
    assert protocol._check_inbound_routing(RID, 1, TG8) is False

    assert protocol._calculate_stream_targets(RID, 1, TG9990, STREAM, SRC) == set()
    assert protocol._calculate_stream_targets(RID, 1, TG8, STREAM, SRC) == {
        b"normal-target"
    }

    # A matching numeric TG received from an external trunk is not the local
    # echo service and must retain the core's normal routing decision.
    assert protocol._calculate_stream_targets(
        ("openbridge", "external"), 1, TG9990, STREAM, SRC
    ) == {b"normal-target"}


def test_protocol_hook_respects_local_to_network_tg_translation():
    class DummyProtocol:
        def __init__(self, config):
            self._config = config
            self._repeaters = {
                RID: SimpleNamespace(
                    inbound_map={(2, (1234).to_bytes(3, "big")): (1, TG9990)},
                    connection_state="connected",
                )
            }
            self._tasks = []

        def _check_inbound_routing(self, repeater_id, slot, dst_id):
            return False

        def _calculate_stream_targets(self, source, slot, dst_id, stream_id, rf_src):
            return {b"normal-target"}

        def _handle_dmr_data(self, data, addr):
            return None

        def _end_stream(self, stream, repeater_id, slot, end_time, reason):
            return None

        def _remove_repeater(self, repeater_id, reason):
            return None

    install_parrot(DummyProtocol)
    protocol = DummyProtocol(parrot_config())
    local_alias = (1234).to_bytes(3, "big")

    assert protocol._check_inbound_routing(RID, 2, local_alias) is True
