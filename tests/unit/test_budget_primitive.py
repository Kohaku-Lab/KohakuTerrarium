"""Budget primitive regression tests for the multi-axis runtime budget."""

import pytest

from kohakuterrarium.core.budget import (
    AlarmState,
    BudgetAxis,
    BudgetExhausted,
    BudgetSet,
    IterationBudget,
)


def test_budget_axis_records_soft_hard_crash_once():
    axis = BudgetAxis(name="turn", soft=2, hard=4)

    axis.consume(1)
    assert axis.pending_transitions == []
    assert axis.last_alarm is AlarmState.OK

    axis.consume(1)
    assert axis.pending_transitions == [AlarmState.SOFT]
    assert axis.last_alarm is AlarmState.SOFT

    axis.consume(1)
    assert axis.pending_transitions == [AlarmState.SOFT]

    axis.consume(1)
    assert axis.pending_transitions == [AlarmState.SOFT, AlarmState.HARD]
    assert axis.last_alarm is AlarmState.HARD

    axis.consume(2)
    assert axis.pending_transitions == [
        AlarmState.SOFT,
        AlarmState.HARD,
        AlarmState.CRASH,
    ]
    assert axis.last_alarm is AlarmState.CRASH


def test_budget_set_ticks_all_axes_and_drains_alarms():
    budgets = BudgetSet(
        turn=BudgetAxis(name="turn", soft=1, hard=2),
        walltime=BudgetAxis(name="walltime", soft=1, hard=3),
        tool_call=BudgetAxis(name="tool_call", soft=1, hard=2),
    )

    budgets.tick(turns=1, seconds=1.5, tool_calls=1)

    assert budgets.turn is not None and budgets.turn.used == 1
    assert budgets.walltime is not None and budgets.walltime.used == 1.5
    assert budgets.tool_call is not None and budgets.tool_call.used == 1
    assert budgets.drain_alarms() == [
        ("turn", AlarmState.SOFT),
        ("walltime", AlarmState.SOFT),
        ("tool_call", AlarmState.SOFT),
    ]
    assert budgets.drain_alarms() == []


def test_budget_set_hard_wall_crash_and_exhausted_axis():
    budgets = BudgetSet(
        turn=BudgetAxis(name="turn", soft=1, hard=2),
        tool_call=BudgetAxis(name="tool_call", soft=1, hard=4),
    )

    budgets.tick(turns=2)
    assert budgets.is_hard_walled()
    assert not budgets.is_crashed()
    assert budgets.exhausted_axis() == "turn"

    budgets.tick(tool_calls=6)
    assert budgets.is_crashed()
    assert budgets.exhausted_axis() == "tool_call"


def test_budget_snapshot_is_serialisable_shape():
    budgets = BudgetSet(turn=BudgetAxis(name="turn", soft=1, hard=2))
    budgets.tick(turns=1)

    assert budgets.snapshot() == {
        "turn": {
            "name": "turn",
            "used": 1,
            "soft": 1,
            "hard": 2,
            "last_alarm": "soft",
            "pending_transitions": ["soft"],
        }
    }


def test_iteration_budget_shim_keeps_legacy_semantics():
    budget = IterationBudget(remaining=1, total=1)
    assert budget.budgets.turn is not None
    budget.consume()
    assert budget.remaining == 0
    assert budget.budgets.turn.used == 1

    with pytest.raises(BudgetExhausted):
        budget.consume()
