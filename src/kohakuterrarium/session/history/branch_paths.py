"""Branch paths and strict branch-view resolution over recorded session events."""

from typing import Any, Iterable


def _coerce_path(raw: Any) -> tuple[tuple[int, int], ...]:
    """Normalize a parent_branch_path payload into a tuple of pairs.

    Accept JSON-friendly lists or tuples of integer pairs. Invalid or missing
    input produces an empty path.
    """
    if not raw:
        return ()
    out: list[tuple[int, int]] = []
    try:
        for item in raw:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                t, b = item
                if isinstance(t, int) and isinstance(b, int):
                    out.append((t, b))
    except TypeError:
        return ()
    return tuple(out)


def index_parent_paths(
    events_list: list[dict[str, Any]],
) -> dict[int, tuple[tuple[int, int], ...]]:
    """Map each event_id → its parent_branch_path.

    Explicit paths take precedence. Legacy events inherit the latest branch seen
    for each earlier turn at that point in event order.
    Public: shared by replay selection and ``session.resume`` branch-state
    restore.
    """
    paths: dict[int, tuple[tuple[int, int], ...]] = {}
    latest_by_turn: dict[int, int] = {}
    shared: dict[tuple, tuple] = {}
    legacy: dict[int, tuple] = {}
    latest_cached_turn: int | None = None
    canonical = True
    for evt in events_list:
        ti = evt.get("turn_index")
        bi = evt.get("branch_id")
        eid = evt.get("event_id")
        if canonical and any(
            isinstance(value, int) and type(value) is not int for value in (ti, bi)
        ):
            canonical = False
            legacy.clear()
        explicit = _coerce_path(evt.get("parent_branch_path"))
        if isinstance(eid, int):
            if explicit:
                if all(type(t) is int and type(b) is int for t, b in explicit):
                    explicit = shared.setdefault(explicit, explicit)
                paths[eid] = explicit
            elif isinstance(ti, int):
                path = legacy.get(ti) if canonical else None
                if path is None:
                    path = tuple(
                        sorted(
                            ((t, b) for t, b in latest_by_turn.items() if t < ti),
                            key=lambda p: p[0],
                        )
                    )
                    if canonical:
                        path = shared.setdefault(path, path)
                        legacy[ti] = path
                        latest_cached_turn = (
                            ti
                            if latest_cached_turn is None
                            else max(latest_cached_turn, ti)
                        )
                paths[eid] = path
        if isinstance(ti, int) and isinstance(bi, int):
            prev = latest_by_turn.get(ti, 0)
            if bi > prev:
                latest_by_turn[ti] = bi
                if (
                    canonical
                    and latest_cached_turn is not None
                    and ti < latest_cached_turn
                ):
                    legacy = {t: path for t, path in legacy.items() if t <= ti}
                    latest_cached_turn = max(legacy, default=None)
    return paths


def _path_matches(
    parent_path: tuple[tuple[int, int], ...],
    selected: dict[int, int],
) -> bool:
    """Return whether a parent path is compatible with selected branches.

    Path turns absent from ``selected`` remain unconstrained until resolution
    reaches them.
    """
    for t, b in parent_path:
        if t in selected and selected[t] != b:
            return False
    return True


class InvalidBranchViewError(ValueError):
    """Raised when a requested branch view cannot identify a coherent path."""


def _coerce_branch_view(branch_view: dict[int, int] | None) -> dict[int, int]:
    """Normalize branch selections without accepting lossy values."""
    if branch_view is None:
        return {}
    if not isinstance(branch_view, dict):
        raise InvalidBranchViewError("branch_view must be a mapping")

    selected: dict[int, int] = {}
    for raw_turn, raw_branch in branch_view.items():
        values = (raw_turn, raw_branch)
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, str))
            or (
                isinstance(value, str)
                and (not value.isdigit() or str(int(value)) != value)
            )
            for value in values
        ):
            raise InvalidBranchViewError("branch_view keys and values must be integers")
        turn_index = int(raw_turn)
        branch_id = int(raw_branch)
        if turn_index < 1 or branch_id < 1:
            raise InvalidBranchViewError("turn indices and branch ids must be positive")
        selected[turn_index] = branch_id
    return selected


def resolve_branch_view_strict(
    events: Iterable[dict[str, Any]],
    branch_view: dict[int, int] | None,
) -> dict[int, int]:
    """Validate a branch view and return its authoritative branch projection."""
    events_list = list(events)
    requested = _coerce_branch_view(branch_view)
    parent_paths = index_parent_paths(events_list)

    pairs: set[tuple[int, int]] = set()
    pair_paths: dict[tuple[int, int], tuple[tuple[int, int], ...]] = {}
    candidates: dict[int, list[tuple[tuple[tuple[int, int], ...], int]]] = {}
    seen_pairs: set[tuple[Any, Any]] = set()
    for evt in events_list:
        seen_pairs.add((evt.get("turn_index"), evt.get("branch_id")))
        if evt.get("type") not in ("user_message", "user_input"):
            continue
        try:
            pair = (int(evt.get("turn_index")), int(evt.get("branch_id")))
        except (TypeError, ValueError):
            continue
        event_id = evt.get("event_id")
        path = (
            parent_paths.get(event_id, ())
            if isinstance(event_id, int)
            else _coerce_path(evt.get("parent_branch_path"))
        )
        pairs.add(pair)
        pair_paths.setdefault(pair, path)
        candidate = (path, pair[1])
        if candidate not in candidates.setdefault(pair[0], []):
            candidates[pair[0]].append(candidate)

    # Turns opened by a trigger or a background result have no user message and a
    # single branch; they are validated against recorded events and passed through.
    userless = {turn for turn, _ in seen_pairs} - set(candidates)
    passthrough = {t: b for t, b in requested.items() if t in userless}
    requested = {t: b for t, b in requested.items() if t not in userless}
    for pair in [*requested.items(), *passthrough.items()]:
        if pair not in (pairs if pair[0] not in userless else seen_pairs):
            raise InvalidBranchViewError(
                f"branch {pair[1]} does not exist for turn {pair[0]}"
            )

    constraints = dict(requested)
    pending = list(requested.items())
    visited: set[tuple[int, int]] = set()
    while pending:
        pair = pending.pop()
        if pair in visited:
            continue
        visited.add(pair)
        for parent_turn, parent_branch in pair_paths.get(pair, ()):
            if parent_turn in userless:
                continue
            existing = constraints.get(parent_turn)
            if existing is not None and existing != parent_branch:
                raise InvalidBranchViewError(
                    f"branch {pair[1]} at turn {pair[0]} is incompatible with "
                    f"branch {existing} at turn {parent_turn}"
                )
            parent_pair = (parent_turn, parent_branch)
            if parent_pair not in pairs:
                raise InvalidBranchViewError(
                    f"branch {pair[1]} at turn {pair[0]} references missing "
                    f"branch {parent_branch} at turn {parent_turn}"
                )
            if existing is None:
                constraints[parent_turn] = parent_branch
                pending.append(parent_pair)

    selected = dict(constraints)
    for turn_index in sorted(candidates):
        required = selected.get(turn_index)
        compatible = [
            branch_id
            for path, branch_id in candidates[turn_index]
            if _path_matches(path, selected)
            and (required is None or branch_id == required)
        ]
        if required is not None and not compatible:
            raise InvalidBranchViewError(
                f"branch {required} at turn {turn_index} is incompatible with the view"
            )
        if compatible:
            selected[turn_index] = max(compatible)
    for pair in list(selected.items()):
        for parent_turn, parent_branch in pair_paths.get(pair, ()):
            if parent_turn not in userless:
                continue
            if passthrough.setdefault(parent_turn, parent_branch) != parent_branch:
                raise InvalidBranchViewError(
                    f"branch {passthrough[parent_turn]} at turn {parent_turn} is "
                    f"incompatible with branch {pair[1]} at turn {pair[0]}"
                )
    return {**selected, **passthrough}
