"""Tests for the shared iteration budget (H.1 extension point).

Exercises:
  * ``IterationBudget.consume`` accounting and exhaustion
  * ``SubAgentManager._resolve_child_budget`` legacy inheritance/allocation
  * ``SubAgent`` loop exits with ``success=False`` and
    ``metadata.budget_exhausted=True`` when drained
  * No budget → legacy behavior preserved (unbounded by budget)
"""

from pathlib import Path

import pytest

from kohakuterrarium.core.budget import (
    BudgetExhausted,
    IterationBudget,
)
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.modules.subagent.base import SubAgent
from kohakuterrarium.modules.subagent.config import SubAgentConfig
from kohakuterrarium.modules.subagent.manager import SubAgentManager
from kohakuterrarium.testing.llm import ScriptedLLM

# ---------------------------------------------------------------------------
# IterationBudget unit behaviour
# ---------------------------------------------------------------------------


def test_consume_decrements_remaining():
    budget = IterationBudget(remaining=3, total=3)
    budget.consume()
    assert budget.remaining == 2
    assert budget.total == 3
    assert not budget.exhausted


def test_total_defaults_to_remaining():
    budget = IterationBudget(remaining=5)
    assert budget.total == 5


def test_consume_zero_raises():
    budget = IterationBudget(remaining=0, total=3)
    with pytest.raises(BudgetExhausted):
        budget.consume()


def test_consume_more_than_remaining_raises():
    budget = IterationBudget(remaining=1, total=4)
    with pytest.raises(BudgetExhausted):
        budget.consume(2)


def test_snapshot_reports_progress():
    budget = IterationBudget(remaining=2, total=5)
    snap = budget.snapshot()
    assert snap == {"remaining": 2, "total": 5, "consumed": 3}


# ---------------------------------------------------------------------------
# SubAgentManager propagation
# ---------------------------------------------------------------------------


def _make_manager(parent_budget: IterationBudget | None = None) -> SubAgentManager:
    mgr = SubAgentManager(
        parent_registry=Registry(),
        llm=ScriptedLLM(["ok"]),
        agent_path=None,
    )
    mgr.iteration_budget = parent_budget
    return mgr


def test_resolve_child_budget_inherits_by_default():
    parent = IterationBudget(remaining=10, total=10)
    mgr = _make_manager(parent)
    config = SubAgentConfig(name="child")  # defaults: inherit=True, allocation=None
    resolved = mgr._resolve_child_budget(config)
    assert resolved is parent  # same reference → shared counter


def test_resolve_child_budget_allocation_is_isolated():
    parent = IterationBudget(remaining=10, total=10)
    mgr = _make_manager(parent)
    config = SubAgentConfig(name="child", budget_allocation=3)
    resolved = mgr._resolve_child_budget(config)
    assert resolved is not None
    assert resolved is not parent
    assert resolved.remaining == 3
    assert resolved.total == 3
    # Consuming the child's isolated budget does NOT touch the parent.
    resolved.consume()
    assert resolved.remaining == 2
    assert parent.remaining == 10


def test_resolve_child_budget_no_inherit_no_allocation_is_none():
    parent = IterationBudget(remaining=10, total=10)
    mgr = _make_manager(parent)
    config = SubAgentConfig(name="child", budget_inherit=False)
    assert mgr._resolve_child_budget(config) is None


def test_resolve_child_budget_no_parent_is_none():
    mgr = _make_manager(None)  # parent has no budget
    config = SubAgentConfig(name="child")
    assert mgr._resolve_child_budget(config) is None


def test_inherited_child_consume_decrements_parent():
    parent = IterationBudget(remaining=10, total=10)
    mgr = _make_manager(parent)
    child_budget = mgr._resolve_child_budget(SubAgentConfig(name="c"))
    assert child_budget is parent
    child_budget.consume(4)
    assert parent.remaining == 6


# ---------------------------------------------------------------------------
# End-to-end: SubAgent loop respects the budget
# ---------------------------------------------------------------------------


def _make_subagent(
    budget: IterationBudget | None, tool_format: str = "bracket"
) -> SubAgent:
    """Create a SubAgent with a trivial LLM script that would loop if unchecked."""
    # Script always emits a "would call tool" line — but since we don't
    # register any tools, the parser never matches a tool call, so the
    # run_internal loop sees no tool_calls and exits on its own. To
    # actually force the loop to iterate multiple times, we use a
    # max_turns budget via iteration_budget.
    #
    # For budget tests we pass a script with many entries and rely on
    # the budget to break out early.
    script = ["keep going"] * 20
    llm = ScriptedLLM(script)
    config = SubAgentConfig(
        name="t",
        tools=[],
        max_turns=0,  # unlimited — we want the budget to be the stopper
        # Force the loop to actually call LLM again by emitting a text-only
        # turn. Since no tool was called, the subagent finishes after turn
        # 1 naturally. Use a fake tool call format that the parser won't
        # match to keep iterations going? Simpler: just test the direct
        # consume path.
    )
    sub = SubAgent(
        config=config,
        parent_registry=Registry(),
        llm=llm,
        agent_path=Path("."),
        tool_format=tool_format,
    )
    sub.iteration_budget = budget
    return sub


@pytest.mark.asyncio
async def test_subagent_exhausts_preallocated_budget():
    """A budget with zero remaining short-circuits on the very first turn.

    Instead of running the full conversation loop (which exits early when
    no tool call is parsed), we use a zero-remaining budget to force the
    budget-check branch to fire before the LLM is touched. The result
    should surface as a failed SubAgentResult with ``budget_exhausted``
    metadata — exactly what the parent controller will see as a
    tool-result error.
    """
    budget = IterationBudget(remaining=0, total=5)
    sub = _make_subagent(budget)

    result = await sub.run("do nothing")

    assert result.success is False
    assert result.error is not None and "BudgetExhausted" in result.error
    assert result.metadata.get("budget_exhausted") is True
    snap = result.metadata.get("budget")
    assert snap == {"remaining": 0, "total": 5, "consumed": 5}


@pytest.mark.asyncio
async def test_subagent_with_budget_decrements_counter():
    """When budget allows, consumption is observed on the shared object."""
    budget = IterationBudget(remaining=3, total=3)
    sub = _make_subagent(budget)

    result = await sub.run("hi")
    # The scripted LLM returns text with no tool calls → loop exits after
    # one turn, having consumed one slot from the budget.
    assert result.success is True
    assert budget.remaining == 2


@pytest.mark.asyncio
async def test_subagent_without_budget_is_unbounded():
    """Legacy behavior: no budget == no enforcement."""
    sub = _make_subagent(None)
    result = await sub.run("hi")
    assert result.success is True
    # No budget attribute errors — iteration_budget stayed None.
    assert sub.iteration_budget is None


# ---------------------------------------------------------------------------
# Parent-loop exhaustion: documented via the IterationBudget contract.
#
# Per the Wave 1-γ spec, the parent agent's main-loop consumption is wired
# by Wave 1-β (pluggable TerminationDecision, see Cluster 3.2). Until β
# lands, we still want a regression-proof check that the exception class
# and message make it out of ``consume`` in a shape β can hand to the
# termination system.
# ---------------------------------------------------------------------------


def test_parent_loop_exhaustion_raises_budget_exhausted():
    parent = IterationBudget(remaining=1, total=2)
    parent.consume()  # first turn
    # Simulate the parent trying to spend a second turn when the budget
    # is dry. β's TerminationDecision path will catch this exception and
    # translate it into a stop signal.
    with pytest.raises(BudgetExhausted) as exc_info:
        parent.consume()
    assert "Iteration budget exhausted" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Parent-loop budget consumption (H.1 final wiring — Cluster 6.1)
# ---------------------------------------------------------------------------


def test_force_terminate_sets_state_and_reason():
    """``TerminationChecker.force_terminate`` flips the terminated flag."""
    from kohakuterrarium.core.termination import TerminationChecker, TerminationConfig

    checker = TerminationChecker(TerminationConfig())
    checker.start()
    assert not checker.should_terminate()
    checker.force_terminate("Iteration budget exhausted")
    assert checker.should_terminate()
    assert checker.reason == "Iteration budget exhausted"


def test_parent_loop_check_termination_consumes_budget():
    """``AgentHandlersMixin._check_termination`` consumes 1 slot per turn
    and terminates cleanly when the shared budget is drained."""
    from types import SimpleNamespace

    from kohakuterrarium.core.agent_handlers import AgentHandlersMixin
    from kohakuterrarium.core.termination import TerminationChecker, TerminationConfig

    checker = TerminationChecker(TerminationConfig())
    checker.start()
    budget = IterationBudget(remaining=2, total=2)

    # Minimal stub with just the attributes _check_termination touches.
    stub = SimpleNamespace(
        _termination_checker=checker,
        iteration_budget=budget,
        _running=True,
        config=SimpleNamespace(name="stub"),
    )

    # Turn 1 — consumes 1, still alive.
    assert AgentHandlersMixin._check_termination(stub, round_text=[""]) is False
    assert budget.remaining == 1
    assert stub._running is True

    # Turn 2 — consumes last slot, still alive (consume succeeded).
    assert AgentHandlersMixin._check_termination(stub, round_text=[""]) is False
    assert budget.remaining == 0

    # Turn 3 — consume raises BudgetExhausted → force_terminate + _running=False.
    assert AgentHandlersMixin._check_termination(stub, round_text=[""]) is True
    assert stub._running is False
    assert "Iteration budget exhausted" in checker.reason


def test_parent_loop_no_budget_skips_consumption():
    """No budget configured → parent loop never raises or flips _running."""
    from types import SimpleNamespace

    from kohakuterrarium.core.agent_handlers import AgentHandlersMixin
    from kohakuterrarium.core.termination import TerminationChecker, TerminationConfig

    checker = TerminationChecker(TerminationConfig())
    checker.start()
    stub = SimpleNamespace(
        _termination_checker=checker,
        iteration_budget=None,
        _running=True,
        config=SimpleNamespace(name="stub"),
    )

    for _ in range(5):
        assert AgentHandlersMixin._check_termination(stub, round_text=[""]) is False
    assert stub._running is True
    assert checker.turn_count == 5
