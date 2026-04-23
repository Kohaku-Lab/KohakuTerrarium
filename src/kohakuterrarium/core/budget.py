"""
Shared iteration budget primitives for agent / sub-agent loops.

Per the extension-point cluster 6.1 decision, a parent agent and its child
sub-agents can share a single pool of "turns" (LLM calls). The parent passes
an :class:`IterationBudget` reference to each child by default; a child
``budget_allocation=N`` opts the child out into its own fresh counter.
When the counter reaches zero, :class:`BudgetExhausted` is raised at the
next consume site — inside a sub-agent it becomes a failed
:class:`~kohakuterrarium.modules.subagent.result.SubAgentResult`; in the
parent's main loop it surfaces as a termination signal.
"""

from dataclasses import dataclass


class BudgetExhausted(Exception):
    """Raised when a shared iteration budget is drained.

    The surrounding sub-agent (or parent controller) is expected to catch
    this, translate it into a tool-result / termination signal, and let
    the controller decide how to proceed.
    """


@dataclass
class IterationBudget:
    """Mutable shared counter for LLM iterations.

    Attributes:
        remaining: Iterations still available. Decremented by ``consume``.
        total: Original size of the budget, for reporting. Never decreases.
    """

    remaining: int
    total: int = 0

    def __post_init__(self) -> None:
        # ``total`` defaults to whatever ``remaining`` was on construction so
        # callers can ``IterationBudget(remaining=50)`` and still get a sensible
        # value back out of ``total`` for logging / UI without having to pass
        # both fields explicitly.
        if self.total <= 0:
            self.total = max(self.remaining, 0)

    def consume(self, n: int = 1) -> None:
        """Decrement the remaining count by ``n``.

        Raises:
            BudgetExhausted: When ``remaining < n`` (including remaining=0).
        """
        if self.remaining < n:
            raise BudgetExhausted(
                f"Iteration budget exhausted "
                f"(remaining={self.remaining}, requested={n}, total={self.total})"
            )
        self.remaining -= n

    @property
    def exhausted(self) -> bool:
        """True when no iterations are left."""
        return self.remaining <= 0

    def snapshot(self) -> dict[str, int]:
        """Return a plain dict for logging / session metadata."""
        return {
            "remaining": self.remaining,
            "total": self.total,
            "consumed": max(self.total - self.remaining, 0),
        }
