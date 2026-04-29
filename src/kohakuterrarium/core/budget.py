"""Shared budget primitives for agent and sub-agent loops."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class BudgetExhausted(Exception):
    """Raised when a legacy hard budget is drained."""


class AlarmState(Enum):
    """Budget alarm severity."""

    OK = "ok"
    SOFT = "soft"
    HARD = "hard"
    CRASH = "crash"


_SEVERITY = {
    AlarmState.OK: 0,
    AlarmState.SOFT: 1,
    AlarmState.HARD: 2,
    AlarmState.CRASH: 3,
}


@dataclass
class BudgetAxis:
    """Mutable usage counter for one budget dimension."""

    name: str
    used: float = 0
    soft: float = 0
    hard: float = 0
    last_alarm: AlarmState = AlarmState.OK
    pending_transitions: list[AlarmState] = field(default_factory=list)

    def consume(self, n: float = 1) -> None:
        """Increment usage and record one-shot severity transitions."""
        self.used += n
        if self.hard <= 0:
            return

        new = AlarmState.OK
        if self.used >= self.hard * 1.5:
            new = AlarmState.CRASH
        elif self.used >= self.hard:
            new = AlarmState.HARD
        elif self.soft > 0 and self.used >= self.soft:
            new = AlarmState.SOFT

        if _SEVERITY[new] > _SEVERITY[self.last_alarm] and new is not AlarmState.OK:
            self.pending_transitions.append(new)
            self.last_alarm = new

    def snapshot(self) -> dict[str, Any]:
        """Return serialisable state for logging and tests."""
        return {
            "name": self.name,
            "used": self.used,
            "soft": self.soft,
            "hard": self.hard,
            "last_alarm": self.last_alarm.value,
            "pending_transitions": [state.value for state in self.pending_transitions],
        }


@dataclass
class BudgetSet:
    """Multi-axis budget state shared by runtime budget plugins."""

    turn: BudgetAxis | None = None
    walltime: BudgetAxis | None = None
    tool_call: BudgetAxis | None = None

    def tick(
        self,
        *,
        turns: float = 0,
        seconds: float = 0.0,
        tool_calls: float = 0,
    ) -> None:
        """Consume one or more budget dimensions."""
        if self.turn is not None and turns:
            self.turn.consume(turns)
        if self.walltime is not None and seconds:
            self.walltime.consume(seconds)
        if self.tool_call is not None and tool_calls:
            self.tool_call.consume(tool_calls)

    def drain_alarms(self) -> list[tuple[str, AlarmState]]:
        """Return and clear pending alarm transitions from all axes."""
        drained: list[tuple[str, AlarmState]] = []
        for axis in self._axes():
            for state in axis.pending_transitions:
                drained.append((axis.name, state))
            axis.pending_transitions.clear()
        return drained

    def is_hard_walled(self) -> bool:
        """True once any axis reaches the hard wall or crash limit."""
        return any(
            axis.last_alarm in {AlarmState.HARD, AlarmState.CRASH}
            for axis in self._axes()
        )

    def is_crashed(self) -> bool:
        """True once any axis reaches the crash limit."""
        return any(axis.last_alarm is AlarmState.CRASH for axis in self._axes())

    def exhausted_axis(self) -> str:
        """Return the highest-severity exhausted axis name."""
        for state in (AlarmState.CRASH, AlarmState.HARD, AlarmState.SOFT):
            for axis in self._axes():
                if axis.last_alarm is state:
                    return axis.name
        return ""

    def snapshot(self) -> dict[str, Any]:
        """Return serialisable state for every enabled axis."""
        return {
            name: axis.snapshot()
            for name, axis in (
                ("turn", self.turn),
                ("walltime", self.walltime),
                ("tool_call", self.tool_call),
            )
            if axis is not None
        }

    def _axes(self) -> list[BudgetAxis]:
        return [
            axis
            for axis in (self.turn, self.walltime, self.tool_call)
            if axis is not None
        ]


@dataclass
class IterationBudget:
    """Backward-compatible single-axis turn budget.

    ``consume`` preserves the historic semantics: a call is allowed while
    ``remaining >= n`` and exhaustion raises only on a later over-consume.
    The internal :class:`BudgetSet` mirrors usage so new budget-aware code can
    inspect the same state when needed.
    """

    remaining: int
    total: int = 0

    def __post_init__(self) -> None:
        if self.total <= 0:
            self.total = max(self.remaining, 0)
        used = max(self.total - self.remaining, 0)
        self.budgets = BudgetSet(
            turn=BudgetAxis(name="turn", used=used, soft=0, hard=self.total)
        )

    def consume(self, n: int = 1) -> None:
        """Decrement remaining by ``n`` or raise ``BudgetExhausted``."""
        if self.remaining < n:
            raise BudgetExhausted(
                f"Iteration budget exhausted "
                f"(remaining={self.remaining}, requested={n}, total={self.total})"
            )
        self.remaining -= n
        self.budgets.tick(turns=n)

    @property
    def exhausted(self) -> bool:
        """True when no iterations are left."""
        return self.remaining <= 0

    def snapshot(self) -> dict[str, int]:
        """Return the legacy snapshot shape."""
        return {
            "remaining": self.remaining,
            "total": self.total,
            "consumed": max(self.total - self.remaining, 0),
        }
