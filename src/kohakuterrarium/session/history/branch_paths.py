"""Branch paths and strict branch-view resolution over recorded session events."""

from typing import Any, Iterable


from kohakuterrarium.session.history.semantics import eligible_branch_history


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


def index_parent_paths_by_position(
    events_list: list[dict[str, Any]],
) -> list[tuple[tuple[int, int], ...]]:
    """Return each physical event's ancestry in input order.

    A position is unique within this history snapshot, even when mirrored
    events reuse a logical event_id.
    """
    paths: list[tuple[tuple[int, int], ...]] = []
    latest_by_turn: dict[int, int] = {}
    # Persisted parent paths on a turn opener remain authoritative for
    # unstamped continuation records even when another root branch interleaves.
    # This mirrors the memory indexer's per-(turn, branch) open-round state.
    open_paths: dict[tuple[int, int], tuple[tuple[int, int], ...]] = {}
    shared: dict[tuple, tuple] = {}
    legacy: dict[int, tuple] = {}
    latest_cached_turn: int | None = None
    canonical = True
    for evt in events_list:
        ti = evt.get("turn_index")
        bi = evt.get("branch_id")
        if canonical and any(
            isinstance(value, int) and type(value) is not int for value in (ti, bi)
        ):
            canonical = False
            legacy.clear()
        explicit = _coerce_path(evt.get("parent_branch_path"))
        kind = evt.get("type")
        scope = (ti, bi) if type(ti) is int and type(bi) is int else None
        opener = kind in ("user_input", "user_message", "trigger_fired")
        if explicit or (kind is not None and evt.get("parent_branch_path") in ([], ())):
            # A modern explicit empty path is still authoritative: it denotes
            # an independent turn, not an unstamped legacy continuation.
            if explicit and all(type(t) is int and type(b) is int for t, b in explicit):
                explicit = shared.setdefault(explicit, explicit)
            path = explicit
        elif kind is not None and scope in open_paths and not opener:
            path = open_paths[scope]
        elif isinstance(ti, int):
            path = legacy.get(ti) if canonical else None
            if path is None:
                path = tuple(
                    sorted(
                        ((t, b) for t, b in latest_by_turn.items() if t < ti),
                        key=lambda pair: pair[0],
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
        else:
            path = ()
        paths.append(path)
        if scope is not None and kind is not None and ti > 0 and bi > 0:
            if evt.get("type") == "processing_end":
                open_paths.pop(scope, None)
            elif opener or scope in open_paths or explicit:
                open_paths[scope] = path
        if isinstance(ti, int) and isinstance(bi, int):
            prev = latest_by_turn.get(ti, 0)
            if bi > prev:
                latest_by_turn[ti] = bi
                if (
                    canonical
                    and latest_cached_turn is not None
                    and ti < latest_cached_turn
                ):
                    legacy = {t: p for t, p in legacy.items() if t <= ti}
                    latest_cached_turn = max(legacy, default=None)
    return paths


def index_eligible_parent_paths_by_position(
    events_list: list[dict[str, Any]],
) -> dict[int, tuple[tuple[int, int], ...]]:
    """Strict Memory ancestry, keyed by original event position.

    Exclude invalid source coordinates before inferring ancestry; retain
    original physical positions so Index and Context navigation can resolve
    refs correctly without modifying Replay's compatibility projection.
    """
    eligible = eligible_branch_history(events_list)
    inferred = index_parent_paths_by_position([event for _, event in eligible])
    return {position: path for (position, _), path in zip(eligible, inferred)}


def index_parent_paths(
    events_list: list[dict[str, Any]],
) -> dict[int, tuple[tuple[int, int], ...]]:
    """Legacy event_id lookup. Prefer positional paths when IDs may repeat."""
    ordered = index_parent_paths_by_position(events_list)
    return {
        event["event_id"]: path
        for event, path in zip(events_list, ordered)
        if isinstance(event.get("event_id"), int)
        and (
            _coerce_path(event.get("parent_branch_path"))
            or isinstance(event.get("turn_index"), int)
        )
    }


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
    parent_paths = index_parent_paths_by_position(events_list)

    pairs: set[tuple[int, int]] = set()
    pair_paths: dict[tuple[int, int], list[tuple[tuple[int, int], ...]]] = {}
    candidates: dict[int, list[tuple[tuple[tuple[int, int], ...], int]]] = {}
    seen_pairs: set[tuple[Any, Any]] = set()
    for position, evt in enumerate(events_list):
        seen_pairs.add((evt.get("turn_index"), evt.get("branch_id")))
        if evt.get("type") not in ("user_message", "user_input"):
            continue
        try:
            pair = (int(evt.get("turn_index")), int(evt.get("branch_id")))
        except (TypeError, ValueError):
            continue
        path = (
            parent_paths[position]
            if isinstance(evt.get("event_id"), int)
            else _coerce_path(evt.get("parent_branch_path"))
        )
        pairs.add(pair)
        alternatives = pair_paths.setdefault(pair, [])
        if path not in alternatives:
            alternatives.append(path)
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

    def compatible_path(
        pair: tuple[int, int], selected: dict[int, int]
    ) -> tuple[tuple[int, int], ...] | None:
        # Mirrored child pairs can have multiple ancestral lineages. Choose a
        # consistent recorded path; never let the first occurrence override
        # a later compatible copy.
        for path in reversed(pair_paths.get(pair, [()])):
            if not _path_matches(path, selected):
                continue
            if all(
                (t, b) in pairs if t not in userless else (t, b) in seen_pairs
                for t, b in path
            ):
                return path
        return None

    # A repeated (turn, branch) pair can have several recorded ancestries.
    # Resolve the requested pairs together rather than committing to the first
    # compatible path, which may conflict with a later requested descendant.
    # The common unique-path case stays iterative and does not branch.
    work = [(dict(requested), list(requested.items()), set())]
    constraints = None
    while work:
        chosen, pending, visited = work.pop()
        while pending:
            pair = pending.pop()
            if pair in visited:
                continue
            options = [
                path
                for path in pair_paths.get(pair, [()])
                if _path_matches(path, chosen)
                and all(
                    (t, b) in pairs if t not in userless else (t, b) in seen_pairs
                    for t, b in path
                )
            ]
            if not options:
                break

            def extend(
                path: tuple[tuple[int, int], ...],
                selected: dict[int, int],
                queue: list[tuple[int, int]],
                checked: set[tuple[int, int]],
            ) -> bool:
                for parent_turn, parent_branch in path:
                    existing = selected.get(parent_turn)
                    if existing is not None and existing != parent_branch:
                        return False
                    if existing is None:
                        selected[parent_turn] = parent_branch
                        if parent_turn not in userless:
                            queue.append((parent_turn, parent_branch))
                checked.add(pair)
                return True

            # Prefer the most recently recorded compatible path; keep other
            # possibilities for backtracking if a descendant rules it out.
            for alternative in options[:-1]:
                other, other_pending, other_visited = (
                    chosen.copy(),
                    pending.copy(),
                    visited.copy(),
                )
                if extend(alternative, other, other_pending, other_visited):
                    work.append((other, other_pending, other_visited))
            if not extend(options[-1], chosen, pending, visited):
                break
        else:
            constraints = chosen
            break

    if constraints is None:
        raise InvalidBranchViewError(
            "Requested branches are incompatible or reference missing ancestry"
        )
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
        path = compatible_path(pair, selected)
        if path is None:
            continue
        for parent_turn, parent_branch in path:
            if parent_turn not in userless:
                continue
            if passthrough.setdefault(parent_turn, parent_branch) != parent_branch:
                raise InvalidBranchViewError(
                    f"branch {passthrough[parent_turn]} at turn {parent_turn} is "
                    f"incompatible with branch {pair[1]} at turn {pair[0]}"
                )
    return {**selected, **passthrough}
