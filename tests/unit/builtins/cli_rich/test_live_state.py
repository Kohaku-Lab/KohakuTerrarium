"""Tests for :mod:`builtins.cli_rich.live_state`."""

from kohakuterrarium.builtins.cli_rich.live_state import (
    FooterState,
    LiveRegionState,
)


class TestFooterState:
    def test_defaults_are_empty(self):
        f = FooterState()
        assert f.model_identifier == ""
        assert f.max_context == 0
        assert f.prompt_tokens == 0
        assert f.completion_tokens == 0


class TestLiveRegionState:
    def test_append_text_accumulates(self):
        s = LiveRegionState(creature_id="c1")
        s.append_text("hello ")
        s.append_text("world")
        assert s.text_buffer == "hello world"

    def test_append_text_caps_at_max(self):
        s = LiveRegionState(creature_id="c1")
        s.append_text("x" * 10_000)
        s.append_text("END")
        # Trailing slice keeps the most-recent content (incl. "END").
        assert s.text_buffer.endswith("END")
        assert len(s.text_buffer) <= 8000

    def test_clear_text_resets_buffer_and_blocks(self):
        s = LiveRegionState(creature_id="c1")
        s.append_text("foo")
        s.active_blocks["b1"] = object()
        s.clear_text()
        assert s.text_buffer == ""
        assert s.active_blocks == {}

    def test_record_event_pushes_into_ring_and_bumps_counters(self):
        s = LiveRegionState(creature_id="c1")
        s.record_event("event-1", now=100.0)
        s.record_event("event-2", now=200.0)
        assert len(s.recent_events) == 2
        assert s.unread_since_focus == 2
        assert s.last_event_at == 200.0

    def test_record_event_ring_has_finite_cap(self):
        s = LiveRegionState(creature_id="c1")
        for i in range(200):
            s.record_event(f"e{i}", now=float(i))
        # maxlen=128 (production default)
        assert len(s.recent_events) <= 128
        # Oldest dropped — last event survived.
        assert s.recent_events[-1][1] == "e199"

    def test_reset_unread_zeroes_counter_without_touching_ring(self):
        s = LiveRegionState(creature_id="c1")
        s.record_event("e1")
        s.record_event("e2")
        assert s.unread_since_focus == 2
        s.reset_unread()
        assert s.unread_since_focus == 0
        assert len(s.recent_events) == 2

    def test_recent_event_payloads_filters_by_time_window(self):
        import time as _time

        s = LiveRegionState(creature_id="c1")
        now = _time.time()
        s.record_event("old", now=now - 100)
        s.record_event("fresh", now=now - 5)
        payloads = s.recent_event_payloads(seconds=30.0)
        assert payloads == ["fresh"]
