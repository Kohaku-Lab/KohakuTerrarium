"""Select uncompacted persisted history for a branch-aware edit or rerun."""

from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, TypeAlias

BranchView: TypeAlias = Mapping[int | str, int | str]


class RawHistorySelectionError(ValueError):
    """Base error for fail-closed raw history selection."""


class MissingRawHistoryError(RawHistorySelectionError):
    """Raised when original persisted history is incomplete."""


class TargetUserMessageNotFoundError(RawHistorySelectionError):
    """Raised when the requested persisted user event does not exist."""


class TargetUserMessageConflictError(RawHistorySelectionError):
    """Raised when canonical locator coordinates disagree."""


class TargetUserMessageLineageError(RawHistorySelectionError):
    """Raised when the requested target is outside the selected lineage."""


class InvalidRawHistoryEventError(RawHistorySelectionError):
    """Raised when persisted event identity is missing or ambiguous."""


@dataclass(frozen=True, slots=True)
class UserMessageSelector:
    """Canonical identity for a persisted turn-root user message."""

    event_id: int
    turn_index: int
    branch_id: int


@dataclass(frozen=True, slots=True)
class RawHistoryPrefix:
    """Detached original event prefix and its canonical target."""

    events: list[dict[str, Any]]
    target: dict[str, Any]
    branch_view: dict[int, int]


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _normalize_view(branch_view: BranchView | None) -> dict[int, int]:
    selected: dict[int, int] = {}
    for raw_turn, raw_branch in (branch_view or {}).items():
        try:
            turn = int(raw_turn)
            branch = int(raw_branch)
        except (TypeError, ValueError) as exc:
            raise TargetUserMessageLineageError(
                "branch_view must contain positive integer turn/branch pairs"
            ) from exc
        if turn <= 0 or branch <= 0:
            raise TargetUserMessageLineageError(
                "branch_view must contain positive integer turn/branch pairs"
            )
        prior = selected.get(turn)
        if prior is not None and prior != branch:
            raise TargetUserMessageLineageError(
                f"branch_view selects conflicting branches for turn {turn}"
            )
        selected[turn] = branch
    return selected


def _coordinates(event: Mapping[str, Any]) -> tuple[int | None, int | None]:
    return _positive_int(event.get("turn_index")), _positive_int(event.get("branch_id"))


def _validate_events(events: list[dict[str, Any]]) -> dict[int, int]:
    positions: dict[int, int] = {}
    for position, event in enumerate(events):
        event_id = _positive_int(event.get("event_id"))
        if event_id is None:
            raise InvalidRawHistoryEventError(
                f"raw event at position {position} lacks a positive integer event_id"
            )
        if event_id in positions:
            raise InvalidRawHistoryEventError(f"duplicate raw event_id {event_id}")
        positions[event_id] = position
    return positions


def _target_lineage(
    target: Mapping[str, Any],
    *,
    target_turn: int,
) -> dict[int, int]:
    raw_path = target.get("parent_branch_path")
    if raw_path is None:
        return {}
    if not isinstance(raw_path, (list, tuple)):
        raise TargetUserMessageLineageError(
            "target parent_branch_path must be a sequence of positive integer pairs"
        )

    lineage: dict[int, int] = {}
    for item in raw_path:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise TargetUserMessageLineageError(
                "target parent_branch_path must contain positive integer pairs"
            )
        turn = _positive_int(item[0])
        branch = _positive_int(item[1])
        if turn is None or branch is None or turn >= target_turn:
            raise TargetUserMessageLineageError(
                "target parent_branch_path must contain positive earlier-turn pairs"
            )
        prior = lineage.get(turn)
        if prior is not None and prior != branch:
            raise TargetUserMessageLineageError(
                f"target ancestry selects conflicting branches for turn {turn}"
            )
        lineage[turn] = branch
    return lineage


def select_raw_history_prefix(
    events: Iterable[Mapping[str, Any]],
    *,
    selector: UserMessageSelector,
    branch_view: BranchView | None = None,
) -> RawHistoryPrefix:
    """Return original persisted events strictly before one canonical user event."""

    raw = [deepcopy(dict(event)) for event in events]
    positions = _validate_events(raw)
    position = positions.get(selector.event_id)
    if position is None:
        raise TargetUserMessageNotFoundError(
            f"user_message event_id {selector.event_id} does not exist"
        )

    target = raw[position]
    if target.get("type") != "user_message":
        raise TargetUserMessageConflictError(
            f"event_id {selector.event_id} is not an editable user_message"
        )
    actual = _coordinates(target)
    expected = (selector.turn_index, selector.branch_id)
    if actual != expected:
        raise TargetUserMessageConflictError(
            f"event_id {selector.event_id} belongs to turn/branch {actual}, not {expected}"
        )

    lineage = _target_lineage(target, target_turn=selector.turn_index)
    selected = _normalize_view(branch_view)
    required = {**lineage, selector.turn_index: selector.branch_id}
    for turn, branch in selected.items():
        expected_branch = required.get(turn)
        if expected_branch is not None and expected_branch != branch:
            raise TargetUserMessageLineageError(
                f"branch_view selects branch {branch} for turn {turn}, "
                f"but target lineage requires branch {expected_branch}"
            )
    selected.update(required)

    prefix: list[dict[str, Any]] = []
    for event in raw[:position]:
        turn, branch = _coordinates(event)
        if (turn is None) != (branch is None):
            raise MissingRawHistoryError(
                f"raw event_id {event['event_id']} has incomplete turn/branch metadata"
            )
        if turn is not None:
            selected_branch = selected.get(turn)
            if selected_branch is not None and selected_branch != branch:
                continue
        prefix.append(event)

    return RawHistoryPrefix(
        events=prefix,
        target=deepcopy(target),
        branch_view=dict(sorted(selected.items())),
    )
