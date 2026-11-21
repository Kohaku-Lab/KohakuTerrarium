"""Branch-scoped search visibility and bounded, exact ranked candidate filtering.

The vault backends currently expose ranked top-k without metadata predicates.
Widening preserves rank order, but exhausting the candidate budget must fail
closed: returning fewer hits would silently misreport a valid branch as empty.
"""

from collections.abc import Callable
from typing import Any, TypeVar

from kohakuterrarium.session.history.semantics import (
    BranchScope,
    classify_branch_scope,
    eligible_branch_history,
    strict_parent_branch_path,
)
from kohakuterrarium.session.history.branch_paths import (
    _coerce_branch_view,
    index_eligible_parent_paths_by_position,
)
from kohakuterrarium.session.history.selection import SelectionCompatibility
from kohakuterrarium.session.history.replay import (
    resolve_selected_branches,
)

DEFAULT_MAX_CANDIDATES = 8192
T = TypeVar("T")


class MemoryCandidateLimitError(RuntimeError):
    """Cannot prove the branch-filtered top-k within the candidate budget."""


class MemoryLineageUnavailableError(RuntimeError):
    """The source event lacks ancestry needed for safe branch filtering."""


class BranchVisibility:
    """Filter stored block metadata against a fully resolved branch view."""

    def __init__(self, branch_view: dict[int, int]):
        self.selected = _coerce_branch_view(branch_view)
        self._ancestry = SelectionCompatibility(self.selected)

    def matches(self, meta: dict[str, Any]) -> bool:
        turn, branch = meta.get("turn_index"), meta.get("branch_id")
        scope = classify_branch_scope(turn, branch)
        if scope in (BranchScope.LEGACY, BranchScope.BOOKKEEPING):
            return True
        if scope is BranchScope.INVALID:
            return False
        if self.selected.get(turn) != branch:
            return False
        raw_path = meta.get("parent_branch_path")
        if raw_path is None:
            if any(parent_turn < turn for parent_turn in self.selected):
                raise MemoryLineageUnavailableError(
                    "Cannot verify this historical event's ancestor lineage. "
                    "Rebuild its memory index before searching this branch."
                )
            return True
        path = strict_parent_branch_path(raw_path, turn=turn)
        return path is not None and self._ancestry.matches(path, turn)


def ranked_filtered(
    ranked_search: Callable[[int], list[T]],
    *,
    k: int,
    matches: Callable[[T], bool],
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
) -> list[T]:
    """Return exact filtered top-k or fail rather than return incomplete hits.

    Searches are assumed to be ranked and prefix-stable as k grows. Finding k
    matching candidates proves the top-k result, even if other matches follow.
    Exhaustion is proved by fewer than the requested candidates being returned.
    """
    if type(k) is not int or k <= 0:
        raise ValueError("k must be a positive integer")
    if k > max_candidates:
        raise ValueError(f"k exceeds filtered search candidate cap {max_candidates}")
    limit = min(max_candidates, max(k * 2, 32))
    while True:
        candidates = ranked_search(limit)
        eligible = [hit for hit in candidates if matches(hit)]
        if len(eligible) >= k or len(candidates) < limit:
            return eligible[:k]
        if limit == max_candidates:
            raise MemoryCandidateLimitError(
                f"Cannot prove filtered top-{k} within {max_candidates} ranked "
                "candidates. Narrow the query or use a more selective scope."
            )
        limit = min(limit * 2, max_candidates)


class AgentBranchVisibility:
    """A session-level visibility projection, with one view per agent."""

    def __init__(self, branch_views: dict[str, dict[int, int]]):
        self._views = {
            name: BranchVisibility(view) for name, view in branch_views.items()
        }

    def matches(self, meta: dict[str, Any]) -> bool:
        view = self._views.get(meta.get("agent"))
        return view is not None and view.matches(meta)


def resolve_search_view(
    events: list[dict],
    requested_view: dict[int, int] | None = None,
) -> dict[int, int]:
    """Resolve explicit selections and implicit latest compatible branches.

    Keep this consistent with conversation replay; callers compute it when
    reading source events, not by interpreting only ranked search candidates.
    """
    # Replay intentionally accepts some noncanonical legacy coordinates for
    # backwards compatibility. Strict Memory selection must exclude invalid
    # events *before* they can influence branch choices or inferred ancestry,
    # not merely delete invalid values from the selected mapping afterwards.
    eligible = [event for _, event in eligible_branch_history(events)]
    ancestries = index_eligible_parent_paths_by_position(eligible)
    # Ambiguous missing ancestry cannot introduce a branch candidate.
    projected = [
        (event, ancestries[position])
        for position, event in enumerate(eligible)
        if ancestries[position] is not None
    ]
    selected = resolve_selected_branches(
        [event for event, _ in projected],
        [path for _, path in projected],
        requested_view,
    )
    # Resolved history can contain (0, 0) bookkeeping rows. Explicit
    # selections remain strictly positive, but these unscoped coordinates
    # must never reach BranchVisibility's strict branch-view validator.
    return {
        turn: branch
        for turn, branch in selected.items()
        if type(turn) is int and turn > 0 and type(branch) is int and branch > 0
    }


def requested_store_view(store: Any, agent: str) -> dict[int, int] | None:
    """Read a validated persisted branch selection from the owning store."""
    try:
        view = store.state.get(f"{agent}:branch_view")
        return _coerce_branch_view(view) if view else None
    except (KeyError, ValueError, TypeError):
        return None
