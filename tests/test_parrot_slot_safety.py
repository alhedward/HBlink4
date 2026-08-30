from types import SimpleNamespace

from hblink4.parrot import _slot_conflicts_with_parrot


STREAM = b"\x12\x34\x56\x78"
OTHER_STREAM = b"\xaa\xbb\xcc\xdd"


def test_free_slot_after_normal_hang_time_cleanup_is_safe():
    # This is the production bug: HBlink4 legitimately clears an ended
    # StreamState after hang time. An empty slot must not cancel a long echo.
    assert _slot_conflicts_with_parrot(None, STREAM) is False


def test_original_ended_stream_is_safe_during_hang_time():
    current = SimpleNamespace(stream_id=STREAM, ended=True)
    assert _slot_conflicts_with_parrot(current, STREAM) is False


def test_same_stream_marked_active_is_a_conflict():
    current = SimpleNamespace(stream_id=STREAM, ended=False)
    assert _slot_conflicts_with_parrot(current, STREAM) is True


def test_reused_slot_is_a_conflict_even_if_new_stream_has_just_ended():
    current = SimpleNamespace(stream_id=OTHER_STREAM, ended=True)
    assert _slot_conflicts_with_parrot(current, STREAM) is True
