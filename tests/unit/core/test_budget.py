"""Unit tests for :mod:`kohakuterrarium.core.budget`."""

import pytest

from kohakuterrarium.core.budget import (
    AlarmState,
    BudgetAxis,
    BudgetExhausted,
    BudgetSet,
    IterationBudget,
)

# ── BudgetAxis ───────────────────────────────────────────────────────


class TestBudgetAxisAlarms:
    def test_no_hard_limit_disables_alarms(self):
        a = BudgetAxis(name="x", hard=0)
        a.consume(10_000)
        # No alarms transition when hard <= 0.
        assert a.last_alarm is AlarmState.OK
        assert a.pending_transitions == []

    def test_under_soft_stays_ok(self):
        a = BudgetAxis(name="x", soft=5, hard=10)
        a.consume(3)
        assert a.last_alarm is AlarmState.OK

    def test_soft_alarm_fires_exactly_once(self):
        a = BudgetAxis(name="x", soft=5, hard=10)
        a.consume(5)
        assert a.last_alarm is AlarmState.SOFT
        assert a.pending_transitions == [AlarmState.SOFT]
        # Re-cross the soft threshold — no new transition.
        a.consume(1)
        assert a.pending_transitions == [AlarmState.SOFT]

    def test_hard_alarm_supersedes_soft(self):
        a = BudgetAxis(name="x", soft=5, hard=10)
        a.consume(5)
        a.consume(5)  # reaches hard
        assert a.last_alarm is AlarmState.HARD
        assert a.pending_transitions == [AlarmState.SOFT, AlarmState.HARD]

    def test_crash_supersedes_hard(self):
        a = BudgetAxis(name="x", soft=5, hard=10)
        a.consume(16)  # 16 >= 15 (1.5x hard) → CRASH
        assert a.last_alarm is AlarmState.CRASH
        # Just CRASH (the chain skips intermediate alarms when one
        # tick crosses multiple thresholds).
        assert a.pending_transitions == [AlarmState.CRASH]

    def test_soft_threshold_zero_disables_soft(self):
        a = BudgetAxis(name="x", soft=0, hard=10)
        a.consume(5)
        # No soft alarm because soft=0.
        assert a.last_alarm is AlarmState.OK
        a.consume(5)
        assert a.last_alarm is AlarmState.HARD

    def test_alarm_doesnt_downgrade(self):
        a = BudgetAxis(name="x", soft=5, hard=10)
        a.consume(10)  # HARD
        a.consume(0.1)  # still HARD; should not re-transition
        assert a.pending_transitions == [AlarmState.HARD]

    def test_snapshot_shape(self):
        a = BudgetAxis(name="turn", soft=5, hard=10)
        a.consume(7)
        snap = a.snapshot()
        assert snap == {
            "name": "turn",
            "used": 7,
            "soft": 5,
            "hard": 10,
            "last_alarm": "soft",
            "pending_transitions": ["soft"],
        }


# ── BudgetSet ────────────────────────────────────────────────────────


def _set_all(*, turn_hard=10, walltime_hard=60, tool_hard=5) -> BudgetSet:
    return BudgetSet(
        turn=BudgetAxis(name="turn", hard=turn_hard),
        walltime=BudgetAxis(name="walltime", hard=walltime_hard),
        tool_call=BudgetAxis(name="tool_call", hard=tool_hard),
    )


class TestBudgetSet:
    def test_tick_routes_to_correct_axes(self):
        s = _set_all()
        s.tick(turns=2, seconds=10, tool_calls=1)
        assert s.turn.used == 2
        assert s.walltime.used == 10
        assert s.tool_call.used == 1

    def test_tick_ignores_disabled_axes(self):
        s = BudgetSet(turn=BudgetAxis(name="turn", hard=10))
        s.tick(turns=1, seconds=5, tool_calls=3)
        assert s.turn.used == 1
        # No walltime/tool_call axes to consume — must not crash.

    def test_zero_increments_skip(self):
        s = _set_all()
        s.tick(turns=0, seconds=0, tool_calls=0)
        assert s.turn.used == 0

    def test_drain_alarms_returns_and_clears(self):
        s = _set_all(turn_hard=2, tool_hard=1)
        s.tick(turns=2)  # hard on turn
        s.tick(tool_calls=1)  # hard on tool_call
        alarms = s.drain_alarms()
        names = [name for name, _ in alarms]
        assert "turn" in names
        assert "tool_call" in names
        # Second drain — empty (cleared after first).
        assert s.drain_alarms() == []

    def test_is_hard_walled_after_hard_hit(self):
        s = _set_all(turn_hard=2)
        assert s.is_hard_walled() is False
        s.tick(turns=2)
        assert s.is_hard_walled() is True

    def test_is_crashed_when_over_1_5x(self):
        s = _set_all(turn_hard=2)
        s.tick(turns=3)  # 3 >= 1.5*2
        assert s.is_crashed() is True
        # Hard-walled is also True (CRASH ⊃ HARD severity).
        assert s.is_hard_walled() is True

    def test_exhausted_axis_returns_highest_severity(self):
        s = _set_all(turn_hard=10, tool_hard=2)
        # Turn hits soft only (no soft set → no soft alarm), tool hits hard.
        s.turn.soft = 3
        s.tick(turns=3)  # soft on turn
        s.tick(tool_calls=2)  # hard on tool_call
        # ``hard`` outranks ``soft``.
        assert s.exhausted_axis() == "tool_call"

    def test_exhausted_axis_none_when_idle(self):
        s = _set_all()
        assert s.exhausted_axis() == ""

    def test_snapshot_only_includes_enabled_axes(self):
        s = BudgetSet(turn=BudgetAxis(name="turn", hard=10))
        snap = s.snapshot()
        assert "turn" in snap
        assert "walltime" not in snap
        assert "tool_call" not in snap


# ── IterationBudget ──────────────────────────────────────────────────


class TestIterationBudget:
    def test_initial_state(self):
        ib = IterationBudget(remaining=5)
        assert ib.remaining == 5
        # total auto-populated from remaining when 0.
        assert ib.total == 5
        assert ib.exhausted is False

    def test_explicit_total(self):
        ib = IterationBudget(remaining=2, total=10)
        assert ib.total == 10
        # ``used`` derived from total - remaining = 8.
        assert ib.budgets.turn.used == 8

    def test_consume_decrements(self):
        ib = IterationBudget(remaining=3, total=5)
        ib.consume()
        assert ib.remaining == 2
        ib.consume(2)
        assert ib.remaining == 0
        assert ib.exhausted is True

    def test_consume_over_raises(self):
        ib = IterationBudget(remaining=1)
        with pytest.raises(BudgetExhausted, match="exhausted"):
            ib.consume(2)

    def test_snapshot_shape(self):
        ib = IterationBudget(remaining=3, total=10)
        ib.consume(2)
        snap = ib.snapshot()
        assert snap == {"remaining": 1, "total": 10, "consumed": 9}

    def test_budgets_mirror_usage(self):
        ib = IterationBudget(remaining=5)
        ib.consume(2)
        # BudgetSet axis ``turn`` should mirror the consume call.
        assert ib.budgets.turn.used == 2
