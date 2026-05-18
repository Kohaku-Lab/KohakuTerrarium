"""Tests for :mod:`builtins.cli_rich.creature_status`.

The derivation is a pure function — tests use a fake creature with
just the attributes the production code reads, no real Agent / engine.
"""

from types import SimpleNamespace

from kohakuterrarium.builtins.cli_rich.creature_status import (
    CreatureStatus,
    STATE_PRIORITY,
    derive_status,
)


def _fake_creature(**kw) -> SimpleNamespace:
    """Build a Creature-shaped namespace with sensible defaults."""
    defaults = dict(
        creature_id="c1",
        name="alpha",
        is_running=True,
        _last_turn_failed=False,
        _last_turn_error="",
        agent=SimpleNamespace(
            _processing_task=None,
            _active_handles={},
            _last_activity_ts=None,
            _last_generation_tokens=0,
            output_router=SimpleNamespace(_pending_replies={}),
        ),
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


class TestStoppedCreature:
    def test_stopped_renders_stopped_state(self):
        c = _fake_creature(is_running=False)
        s = derive_status(c, now=100.0)
        assert isinstance(s, CreatureStatus)
        assert s.state == "stopped"
        assert "stopped" in s.activity

    def test_stopped_with_last_activity_shows_duration(self):
        c = _fake_creature(is_running=False)
        c.agent._last_activity_ts = 100.0
        s = derive_status(c, now=400.0)
        assert s.state == "stopped"
        assert s.duration_seconds == 300
        assert "5m" in s.activity


class TestFailedCreature:
    def test_failed_flag_wins_over_idle(self):
        c = _fake_creature(_last_turn_failed=True, _last_turn_error="boom")
        s = derive_status(c)
        assert s.state == "failed"
        assert s.activity == "boom"


class TestWaitingCreature:
    def test_pending_reply_shows_waiting_with_prompt(self):
        reply = SimpleNamespace(prompt="double jump or wall climb?")
        c = _fake_creature()
        c.agent.output_router._pending_replies = {"r1": reply}
        s = derive_status(c)
        assert s.state == "waiting"
        assert "needs:" in s.activity
        assert "double jump" in s.activity

    def test_waiting_beats_working(self):
        # Both pending reply AND active processing — waiting wins.

        c = _fake_creature()
        c.agent._processing_task = SimpleNamespace(done=lambda: False)
        c.agent.output_router._pending_replies = {
            "r1": SimpleNamespace(prompt="confirm?")
        }
        s = derive_status(c)
        assert s.state == "waiting"


class TestWorkingCreature:
    def test_processing_task_drives_working_state(self):
        c = _fake_creature()
        c.agent._processing_task = SimpleNamespace(done=lambda: False)
        s = derive_status(c)
        assert s.state == "working"

    def test_working_with_active_handle_describes_job(self):
        c = _fake_creature()
        c.agent._processing_task = SimpleNamespace(done=lambda: False)
        handle = SimpleNamespace(name="bash", args={"command": "pytest"})
        c.agent._active_handles = {"j1": handle}
        s = derive_status(c)
        assert s.state == "working"
        assert "bash" in s.activity
        assert "pytest" in s.activity

    def test_working_with_no_handle_falls_back_to_generating(self):
        c = _fake_creature()
        c.agent._processing_task = SimpleNamespace(done=lambda: False)
        c.agent._last_generation_tokens = 1234
        s = derive_status(c)
        assert s.state == "working"
        assert "Generating" in s.activity


class TestIdleCreature:
    def test_no_activity_is_idle(self):
        c = _fake_creature()
        s = derive_status(c, now=100.0)
        assert s.state == "idle"
        assert s.activity == "idle"

    def test_idle_with_last_event_shows_duration(self):
        c = _fake_creature()
        c.agent._last_activity_ts = 100.0
        s = derive_status(c, now=400.0)
        assert s.state == "idle"
        assert s.duration_seconds == 300
        assert "5m" in s.activity


class TestPriorityOrder:
    def test_priority_keys_are_complete(self):
        assert set(STATE_PRIORITY) == {
            "waiting",
            "working",
            "failed",
            "stopped",
            "idle",
        }

    def test_waiting_has_highest_priority(self):
        assert STATE_PRIORITY["waiting"] < STATE_PRIORITY["working"]
        assert STATE_PRIORITY["working"] < STATE_PRIORITY["idle"]


class TestActivityTruncation:
    def test_long_activity_is_truncated(self):
        reply = SimpleNamespace(prompt="x" * 500)
        c = _fake_creature()
        c.agent.output_router._pending_replies = {"r1": reply}
        s = derive_status(c)
        # _ACTIVITY_MAX is 40; truncation appends "…"
        assert len(s.activity) <= 50
        assert s.activity.endswith("…")
