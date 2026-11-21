"""Canonical branch-coordinate and legacy round-handoff semantics.

Event coordinates describe *visibility*, not whether a physical event belongs
to the currently open conversational round. Interpret both independently and
never turn a malformed coordinate into an unscoped legacy event.
"""

from enum import Enum
from typing import Any


class BranchScope(Enum):
    """Semantic categories for the two coordinates stored on an event."""

    LEGACY = "legacy"  # Neither coordinate was recorded.
    BOOKKEEPING = "bookkeeping"  # Historical global (0, 0) record.
    EXPLICIT = "explicit"  # Valid positive turn / positive branch.
    INVALID = "invalid"  # Half-present, mixed-zero, negative, or malformed.


def classify_branch_scope(turn: Any, branch: Any) -> BranchScope:
    """Classify raw coordinates without coercion or information loss."""
    if turn is None and branch is None:
        return BranchScope.LEGACY
    if type(turn) is not int or type(branch) is not int:
        return BranchScope.INVALID
    if turn == 0 and branch == 0:
        return BranchScope.BOOKKEEPING
    if turn > 0 and branch > 0:
        return BranchScope.EXPLICIT
    return BranchScope.INVALID


def strict_parent_branch_path(
    raw: Any, *, turn: int | None = None
) -> tuple[tuple[int, int], ...] | None:
    """Validate recorded ancestry without Replay's lossy coercion.

    None denotes an absent or invalid path; callers distinguish a missing
    field from malformed explicit input before inferring legacy ancestry.
    No remote or external-history identity proof is attempted here.
    """
    if not isinstance(raw, (list, tuple)):
        return None
    path: list[tuple[int, int]] = []
    previous_turn = 0
    for pair in raw:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            return None
        ancestor_turn, branch = pair
        if (
            type(ancestor_turn) is not int
            or type(branch) is not int
            or ancestor_turn <= previous_turn
            or branch <= 0
            or (type(turn) is int and ancestor_turn >= turn)
        ):
            return None
        path.append((ancestor_turn, branch))
        previous_turn = ancestor_turn
    return tuple(path)


def eligible_branch_history(
    events: list[dict[str, Any]],
) -> list[tuple[int, dict[str, Any]]]:
    """Retain eligible events and *original* physical positions.

    Memory-only strict projection; raw Replay continues accepting historical
    noncanonical coordinates. Consumer-specific filters must not independently
    invent which events can influence ancestry or branch selection.
    """
    return [
        (position, event)
        for position, event in enumerate(events)
        if classify_branch_scope(event.get("turn_index"), event.get("branch_id"))
        is not BranchScope.INVALID
    ]


# A modern user_message accompanies its user_input; it does not reopen
# an existing Round or reset ancestry when its own path is missing.
MEMORY_ROUND_OPENERS = frozenset({"user_input", "trigger_fired"})


_CONTINUATIONS = frozenset(
    {"text", "text_chunk", "tool_call", "tool_result", "processing_end"}
)


def can_continue_legacy_round(
    event_type: str,
    *,
    legacy_open: bool,
    legacy_round: int,
    explicit_turn: int,
    other_open_scopes: int,
) -> bool:
    """Allow an unambiguous legacy opener to hand its round to a stamped tail.

    A stamped turn must agree with the known legacy round number; an alternate
    open scoped round makes the assignment ambiguous and must not be guessed.
    The caller retains the original event coordinates when materializing.
    """
    return (
        event_type in _CONTINUATIONS
        and legacy_open
        and explicit_turn == legacy_round
        and other_open_scopes == 0
    )
