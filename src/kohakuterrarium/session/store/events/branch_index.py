"""Rebuildable per-store branch maxima derived from native event rows."""

import threading
from typing import Any

from kohakuterrarium.session.vault.io import read_prefix


class BranchIndex:
    """Track maximum branch IDs without retaining event payloads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._generation = 0
        self._invalidation_epoch = 0
        self._agents: dict[str, dict[int, int]] = {}
        self._upper_turn: dict[str, int] = {}

    @property
    def invalidation_epoch(self) -> int:
        """Return the epoch of raw-history resets and failed publications."""
        with self._lock:
            return self._invalidation_epoch

    def clear(self) -> None:
        """Invalidate derived maxima after bulk copies or counter restoration."""
        with self._lock:
            self._generation += 1
            self._invalidation_epoch += 1
            self._agents.clear()
            self._upper_turn.clear()

    def upper_turn(self, agent: str) -> int | None:
        """Return the last occupied turn when a complete bound is available."""
        with self._lock:
            index = self._agents.get(agent)
            return (
                max(index, default=0)
                if index is not None
                else self._upper_turn.get(agent)
            )

    def restore_upper_turn(self, agent: str, turn: int) -> None:
        """Install the last occupied turn from a consumed clean continuation point."""
        with self._lock:
            self._upper_turn[agent] = turn

    @staticmethod
    def _record(index: dict[int, int], event: dict) -> None:
        turn, branch = event.get("turn_index"), event.get("branch_id")
        if isinstance(turn, int) and isinstance(branch, int):
            index[turn] = max(index.get(turn, 0), branch)

    def append(self, table: Any, key: str, agent: str, event: dict) -> None:
        """Publish a native event row and update derived branch maxima under lock."""
        with self._lock:
            try:
                table[key] = event
            except BaseException:
                self._generation += 1
                self._invalidation_epoch += 1
                self._agents.pop(agent, None)
                self._upper_turn.pop(agent, None)
                raise
            self._generation += 1
            index = self._agents.get(agent)
            if index is not None:
                self._record(index, event)
            upper = self._upper_turn.get(agent)
            turn = event.get("turn_index")
            if upper is not None and type(turn) is int:
                self._upper_turn[agent] = max(upper, turn)

    def maximum(self, table: Any, agent: str, turn: int) -> int:
        """Read a maximum, rebuilding a cold namespace without partial publication."""
        while True:
            with self._lock:
                index = self._agents.get(agent)
                if index is not None:
                    return index.get(turn, 0)
                if turn > self._upper_turn.get(agent, float("inf")):
                    return 0
                generation = self._generation
            table.flush_cache()
            built: dict[int, int] = {}
            # A skipped row could hold the maximum, so read failures propagate.
            for _, event in read_prefix(table, f"{agent}:e", strict=True):
                if isinstance(event, dict):
                    self._record(built, event)
            with self._lock:
                if generation == self._generation:
                    self._agents[agent] = built
                    return built.get(turn, 0)


class BranchIndexMixin:
    """Expose branch allocation queries on a native session store."""

    def max_branch_id(self, agent: str, turn_index: int) -> int:
        """Return the largest persisted branch ID for one agent and turn."""
        self._flush_events_cache()
        return self._branch_maxima.maximum(self.events, agent, turn_index)
