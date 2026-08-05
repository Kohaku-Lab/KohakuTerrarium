"""Normalize, branch-select, and replay persisted session events."""

import json
from collections.abc import Hashable, Mapping
from typing import Any, Iterable

from kohakuterrarium.core.job_label import make_job_label

# Parent paths identify the selected branches of earlier turns. Legacy events
# derive this ancestry from event order when no explicit path was persisted.


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


def _index_parent_paths(
    events_list: list[dict[str, Any]],
) -> dict[int, tuple[tuple[int, int], ...]]:
    """Map each event_id → its parent_branch_path.

    Explicit paths take precedence. Legacy events inherit the latest branch seen
    for each earlier turn at that point in event order.
    """
    paths: dict[int, tuple[tuple[int, int], ...]] = {}
    latest_by_turn: dict[int, int] = {}
    for evt in events_list:
        ti = evt.get("turn_index")
        bi = evt.get("branch_id")
        eid = evt.get("event_id")
        explicit = _coerce_path(evt.get("parent_branch_path"))
        if isinstance(eid, int):
            if explicit:
                paths[eid] = explicit
            elif isinstance(ti, int):
                paths[eid] = tuple(
                    sorted(
                        ((t, b) for t, b in latest_by_turn.items() if t < ti),
                        key=lambda p: p[0],
                    )
                )
        if isinstance(ti, int) and isinstance(bi, int):
            prev = latest_by_turn.get(ti, 0)
            if bi > prev:
                latest_by_turn[ti] = bi
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


def _resolve_selected_branches(
    events_list: list[dict[str, Any]],
    parent_paths: dict[int, tuple[tuple[int, int], ...]],
    branch_view: dict[int, int] | None,
) -> dict[int, int]:
    """Pick a live branch for each turn while respecting nested paths.

    Turns resolve in ascending order. Valid overrides win; otherwise the highest
    compatible branch is selected. Turns with no compatible branch are omitted
    from the live subtree.
    """
    branches_by_turn: dict[int, list[tuple[int, int]]] = {}
    for evt in events_list:
        ti = evt.get("turn_index")
        bi = evt.get("branch_id")
        eid = evt.get("event_id")
        if not isinstance(ti, int) or not isinstance(bi, int):
            continue
        path = parent_paths.get(eid, ()) if isinstance(eid, int) else ()
        bucket = branches_by_turn.setdefault(ti, [])
        if not any(b == bi for _, b in bucket):
            bucket.append((path, bi))

    selected: dict[int, int] = {}
    override = dict(branch_view or {})
    for ti in sorted(branches_by_turn.keys()):
        candidates = [
            (path, bi)
            for path, bi in branches_by_turn[ti]
            if _path_matches(path, selected)
        ]
        if not candidates:
            continue
        if ti in override:
            requested = override[ti]
            match = next(
                ((path, bi) for path, bi in candidates if bi == requested), None
            )
            if match is not None:
                selected[ti] = match[1]
                continue
        selected[ti] = max(bi for _, bi in candidates)
    return selected


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
    parent_paths = _index_parent_paths(events_list)

    pairs: set[tuple[int, int]] = set()
    pair_paths: dict[tuple[int, int], tuple[tuple[int, int], ...]] = {}
    candidates: dict[int, list[tuple[tuple[tuple[int, int], ...], int]]] = {}
    for evt in events_list:
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

    for pair in requested.items():
        if pair not in pairs:
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
    return selected


def project_branch_metadata(
    events: Iterable[dict[str, Any]],
    branch_view: dict[int, int] | None = None,
) -> dict[int, dict[str, Any]]:
    """Project branch choices, ancestry, and the selected coherent path."""
    events_list = list(events)
    selected = resolve_branch_view_strict(events_list, branch_view)
    parent_paths = _index_parent_paths(events_list)
    branches: dict[int, dict[int, set[tuple[tuple[int, int], ...]]]] = {}

    for evt in events_list:
        if evt.get("type") not in ("user_message", "user_input"):
            continue
        try:
            turn_index = int(evt.get("turn_index"))
            branch_id = int(evt.get("branch_id"))
        except (TypeError, ValueError):
            continue
        event_id = evt.get("event_id")
        path = (
            parent_paths.get(event_id, ())
            if isinstance(event_id, int)
            else _coerce_path(evt.get("parent_branch_path"))
        )
        branches.setdefault(turn_index, {}).setdefault(branch_id, set()).add(path)

    return {
        turn_index: {
            "branches": [
                {
                    "branch_id": branch_id,
                    "parent_branch_paths": [
                        [
                            [parent_turn, parent_branch]
                            for parent_turn, parent_branch in path
                        ]
                        for path in sorted(paths)
                    ],
                    "selected": selected.get(turn_index) == branch_id,
                }
                for branch_id, paths in sorted(branches_by_id.items())
            ],
            "latest": max(branches_by_id),
            "selected": selected.get(turn_index),
        }
        for turn_index, branches_by_id in sorted(branches.items())
    }


def collect_branch_metadata(
    events: Iterable[dict[str, Any]],
    *,
    branch_view: dict[int, int] | None = None,
) -> dict[int, dict[str, Any]]:
    """Extract per-turn branch metadata from an event stream.

    Events lacking turn or branch identifiers are ignored. With ``branch_view``,
    only branches compatible with selected prior ancestry are reported, so
    navigator counts reflect the visible subtree.
    """
    events_list = list(events)
    parent_paths = _index_parent_paths(events_list)
    selected = _resolve_selected_branches(events_list, parent_paths, branch_view)

    out: dict[int, dict[str, Any]] = {}
    for evt in events_list:
        ti = evt.get("turn_index")
        bi = evt.get("branch_id")
        eid = evt.get("event_id")
        if not isinstance(ti, int) or not isinstance(bi, int):
            continue
        path = parent_paths.get(eid, ()) if isinstance(eid, int) else ()
        # Navigator counts must remain local to the selected ancestry.
        prior_selected = {t: b for t, b in selected.items() if t < ti}
        if not _path_matches(path, prior_selected):
            continue
        bucket = out.setdefault(
            ti, {"branches": [], "latest_branch": 0, "events_by_branch": {}}
        )
        if bi not in bucket["events_by_branch"]:
            bucket["events_by_branch"][bi] = []
            bucket["branches"].append(bi)
        if isinstance(eid, int):
            bucket["events_by_branch"][bi].append(eid)
        if bi > bucket["latest_branch"]:
            bucket["latest_branch"] = bi
    for bucket in out.values():
        bucket["branches"].sort()
    return out


def collect_user_groups(
    events: Iterable[dict[str, Any]],
    *,
    branch_view: dict[int, int] | None = None,
) -> dict[int, dict[str, Any]]:
    """Per-turn grouping of branches by ``user_message`` content.

    Identical user content groups response regenerations together; distinct user
    content represents edited alternatives. Each turn reports its groups and the
    selected group index.
    """
    events_list = list(events)
    meta = collect_branch_metadata(events_list, branch_view=branch_view)
    parent_paths = _index_parent_paths(events_list)
    selected = _resolve_selected_branches(events_list, parent_paths, branch_view)
    contents: dict[int, dict[int, str]] = {}
    for evt in events_list:
        if evt.get("type") not in ("user_message", "user_input"):
            continue
        ti = evt.get("turn_index")
        bi = evt.get("branch_id")
        if not isinstance(ti, int) or not isinstance(bi, int):
            continue
        contents.setdefault(ti, {})
        if bi not in contents[ti]:
            c = evt.get("content")
            contents[ti][bi] = c if isinstance(c, str) else str(c)
    out: dict[int, dict[str, Any]] = {}
    for ti, info in meta.items():
        groups: list[dict[str, Any]] = []
        for branch in info["branches"]:
            content = contents.get(ti, {}).get(branch, "")
            existing = next((g for g in groups if g["content"] == content), None)
            if existing is None:
                groups.append({"content": content, "branches": [branch]})
            else:
                existing["branches"].append(branch)
        sel = selected.get(ti)
        sel_idx = next((i for i, g in enumerate(groups) if sel in g["branches"]), 0)
        out[ti] = {"groups": groups, "selected_group_idx": sel_idx}
    return out


def select_live_event_ids(
    events: Iterable[dict[str, Any]],
    *,
    branch_view: dict[int, int] | None = None,
) -> set[int]:
    """Return the event_ids that belong to the live subtree.

    Live events belong to the selected branch and compatible prior ancestry.
    Legacy or non-state events without branch metadata remain live. Without an
    override, the latest compatible branch is selected at every turn.
    """
    events_list = list(events)
    parent_paths = _index_parent_paths(events_list)
    selected = _resolve_selected_branches(events_list, parent_paths, branch_view)

    live: set[int] = set()
    for evt in events_list:
        ti = evt.get("turn_index")
        bi = evt.get("branch_id")
        eid = evt.get("event_id")
        if not isinstance(eid, int):
            continue
        if not isinstance(ti, int) or not isinstance(bi, int):
            live.add(eid)
            continue
        if selected.get(ti) != bi:
            continue
        path = parent_paths.get(eid, ())
        prior_selected = {t: b for t, b in selected.items() if t < ti}
        if not _path_matches(path, prior_selected):
            continue
        live.add(eid)
    return live


def _event_signature_value(value: Any) -> Hashable:
    try:
        hash(value)
    except TypeError:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return value


def dedupe_adjacent_duplicate_events(
    events: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collapse identical adjacent persisted events.

    Duplicate sink attachment can persist equivalent neighboring rows with distinct
    identifiers and timestamps. Replay consumers collapse them while the raw log
    remains unchanged.
    """
    out: list[dict[str, Any]] = []
    previous_signature: tuple[tuple[str, Hashable], ...] | None = None
    for evt in events:
        signature = tuple(
            sorted(
                (k, _event_signature_value(v))
                for k, v in evt.items()
                if k not in {"event_id", "ts"}
            )
        )
        if signature == previous_signature:
            continue
        out.append(evt)
        previous_signature = signature
    return out


def _clean_tool_name(evt: dict) -> str:
    """Resolve the provider-safe tool name for a ``tool_result`` event.

    The event's ``name`` may be a UI display label (``bash[ff6427]``) or
    already clean (``bash``). A clean name is used as-is; a label or a
    missing name is resolved from the job id through the shared job-label
    helper (``bash_ff642767`` -> ``bash``), the same source the live
    conversation path uses. Falls back to stripping the bracketed suffix.
    """
    name = evt.get("name", "")
    if isinstance(name, str) and name and "[" not in name:
        return name
    call_id = evt.get("call_id") or evt.get("job_id") or ""
    if call_id:
        tool_name, _ = make_job_label(str(call_id))
        if tool_name:
            return tool_name
    if isinstance(name, str) and "[" in name:
        return name.split("[", 1)[0]
    return name if isinstance(name, str) else ""


def _compact_path_from_event(evt: Mapping[str, Any]) -> set[tuple[int, int]] | None:
    """Parse a compact_replace's ``compact_path`` (list of [turn, branch]).

    Returns ``None`` when absent — the legacy v1_to_v2 migration product has no
    path, so replay falls back to the global id-range behaviour for it.
    """
    raw = evt.get("compact_path")
    if not isinstance(raw, (list, tuple)):
        return None
    out: set[tuple[int, int]] = set()
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            try:
                out.add((int(item[0]), int(item[1])))
            except (TypeError, ValueError):
                continue
    return out or None


def _covered_by_replace(
    eid: object,
    turn_index: object,
    branch_id: object,
    rules: list[tuple[int, int, set[tuple[int, int]] | None, str]],
) -> bool:
    """Whether event ``eid`` is covered by a compaction replacement rule.

    A rule is ``(replaced_from, replaced_to, compact_path_or_None, summary)``.
    With a path, only events on that path are covered (branch-aware); without
    a path (legacy migration data) the whole id range is covered.
    """
    if not isinstance(eid, int):
        return False
    for frm, to, path, _summary in rules:
        if not (frm <= eid <= to):
            continue
        if path is None:
            return True
        if isinstance(turn_index, int) and isinstance(branch_id, int):
            if (turn_index, branch_id) in path:
                return True
    return False


def replay_conversation(
    events: Iterable[dict[str, Any]],
    *,
    branch_view: dict[int, int] | None = None,
    include_metadata: bool = False,
) -> list[dict[str, Any]]:
    """Rebuild an OpenAI-shape message list from the event log.

    Events are branch-filtered and emitted in provider-ready message order.
    Consecutive text chunks coalesce, tool announcements pair with tool results,
    system prompts remain ordered, and compaction ranges become one summary.
    Unknown observability events are ignored.
    """
    events_list = dedupe_adjacent_duplicate_events(events)
    # Nested branch ancestry determines the live event set.
    live_ids = select_live_event_ids(events_list, branch_view=branch_view)

    # Compaction summaries replace every covered source event. Each
    # compact_replace may carry a ``compact_path`` so a replacement recorded
    # on one branch never deletes the sibling branch's events.
    replaced_rules: list[tuple[int, int, set[tuple[int, int]] | None, str]] = []
    for evt in events_list:
        if evt.get("type") not in ("compact_replace", "compact_complete"):
            continue
        frm = evt.get("replaced_from_event_id")
        to = evt.get("replaced_to_event_id")
        if isinstance(frm, int) and isinstance(to, int):
            replaced_rules.append(
                (
                    frm,
                    to,
                    _compact_path_from_event(evt),
                    evt.get("summary", "") or evt.get("summary_text", ""),
                )
            )
    # Summaries are emitted where the replaced content sat, not at the
    # compact_replace event's own stream position (a compact runs after the
    # live tail it preserves). pending summaries are ordered by replaced_from
    # and flushed when the live stream reaches the first event past a range.
    pending_rules: list[tuple[int, int, set[tuple[int, int]] | None, str]] = sorted(
        replaced_rules, key=lambda r: r[0]
    )
    pending_next = 0

    messages: list[dict[str, Any]] = []
    text_buf: list[str] = []
    # A turn/branch owns exactly one user_message. Graph merge and
    # migration can copy the same event twice; duplicate user messages
    # would break turn-targeted edit resolution and pollute the view.
    seen_user_messages: set[tuple[int, int]] = set()

    def _flush_text() -> None:
        if not text_buf:
            return
        content = "".join(text_buf)
        if content:
            messages.append({"role": "assistant", "content": content})
        text_buf.clear()

    def _flush_pending_summaries(
        eid: int, turn_index: object, branch_id: object
    ) -> None:
        nonlocal pending_next
        while pending_next < len(pending_rules):
            rule = pending_rules[pending_next]
            frm, to, _path, summary = rule
            if frm > eid:
                break
            # Inject the summary only when the current branch actually has an
            # event covered by this rule; a rule whose path does not intersect
            # the selected branch must NOT inject a spurious summary. Rules
            # without a path (legacy migration data) cover the whole range.
            if _covered_by_replace(eid, turn_index, branch_id, [rule]):
                # Flush buffered assistant text that precedes the replaced
                # range before injecting the summary (a covered event does not
                # otherwise delimit the text buffer).
                _flush_text()
                if summary:
                    messages.append({"role": "assistant", "content": summary})
                pending_next += 1
            elif eid > to:
                # Range passed with no covered event on this branch — drop.
                pending_next += 1
            else:
                break

    for evt in events_list:
        etype = evt.get("type", "")
        eid = evt.get("event_id")

        # Synthetic events without identifiers bypass branch filtering.
        if isinstance(eid, int) and eid not in live_ids:
            continue

        if isinstance(eid, int):
            # A compact covers id range [replaced_from..replaced_to]; its
            # summary is inserted where the covered content sat, i.e. as soon
            # as the stream reaches a covered event on this branch.
            _flush_pending_summaries(eid, evt.get("turn_index"), evt.get("branch_id"))

        if etype != "compact_replace" and _covered_by_replace(
            eid,
            evt.get("turn_index"),
            evt.get("branch_id"),
            replaced_rules,
        ):
            continue

        if etype == "compact_replace":
            # Handled by _flush_pending_summaries above; never emitted inline.
            continue

        if etype in ("text_chunk", "text"):
            chunk = evt.get("content", "")
            if isinstance(chunk, str):
                text_buf.append(chunk)
            continue

        if etype in (
            "compact_start",
            "compact_complete",
            "compact_skipped",
            "compact_decision",
            "background_result",
            "token_usage",
            "turn_token_usage",
            "cache_stats",
            "scratchpad_write",
            "plugin_hook_timing",
            "processing_start",
            "processing_complete",
        ):
            continue

        # Structural events delimit streamed assistant text.
        _flush_text()

        if etype == "user_message":
            ti = evt.get("turn_index")
            bi = evt.get("branch_id")
            if isinstance(ti, int) and isinstance(bi, int):
                if (ti, bi) in seen_user_messages:
                    continue
                seen_user_messages.add((ti, bi))
            message = {"role": "user", "content": evt.get("content", "")}
            if include_metadata:
                message["metadata"] = {
                    "event_id": evt.get("event_id"),
                    "turn_index": evt.get("turn_index"),
                    "branch_id": evt.get("branch_id"),
                }
            messages.append(message)
        elif etype == "assistant_tool_calls":
            tool_calls = evt.get("tool_calls") or []
            if (
                messages
                and messages[-1].get("role") == "assistant"
                and not messages[-1].get("tool_calls")
            ):
                messages[-1]["tool_calls"] = tool_calls
            else:
                messages.append(
                    {
                        "role": "assistant",
                        "content": evt.get("content", ""),
                        "tool_calls": tool_calls,
                    }
                )
        elif etype == "tool_result":
            messages.append(
                {
                    "role": "tool",
                    "content": evt.get("output", "") or "",
                    "tool_call_id": evt.get("call_id", "") or evt.get("job_id", ""),
                    "name": _clean_tool_name(evt),
                }
            )
        elif etype == "system_prompt_set":
            messages.append({"role": "system", "content": evt.get("content", "")})

    _flush_text()
    return messages


def _coerce_tool_args_to_json(args: Any) -> str:
    """Best-effort serialisation for ``assistant_tool_calls.arguments``.

    OpenAI-shaped tool-call arguments must remain JSON strings for replay and
    downstream sanitization.
    """
    if isinstance(args, str):
        return args
    if args is None:
        return "{}"
    try:
        return json.dumps(args)
    except (TypeError, ValueError):
        return "{}"


def _inject_synthetic_announcements(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Insert missing assistant tool-call announcements before consumption.

    Pending calls are grouped until a structural event requires them. Existing
    announcements suppress duplicates. Synthetic announcements intentionally omit
    event identifiers so replay preserves their inserted position.
    """
    pending: list[dict[str, Any]] = []
    announced_ids: set[str] = set()
    result: list[dict[str, Any]] = []

    def _build_announce(items: list[dict[str, Any]]) -> dict[str, Any]:
        tool_calls: list[dict[str, Any]] = []
        for tc in items:
            tool_calls.append(
                {
                    "id": str(tc.get("call_id") or tc.get("job_id") or ""),
                    "type": "function",
                    "function": {
                        "name": tc.get("name", "") or "",
                        "arguments": _coerce_tool_args_to_json(tc.get("args")),
                    },
                }
            )
        return {
            "type": "assistant_tool_calls",
            "tool_calls": tool_calls,
            "content": "",
            "ts": items[0].get("ts", 0),
            "_synthetic_announce": True,
        }

    def _flush_pending() -> None:
        if not pending:
            return
        result.append(_build_announce(pending))
        for tc in pending:
            cid = str(tc.get("call_id") or tc.get("job_id") or "")
            if cid:
                announced_ids.add(cid)
        pending.clear()

    for evt in events:
        etype = evt.get("type", "")

        if etype == "tool_call":
            cid = str(evt.get("call_id") or evt.get("job_id") or "")
            # Calls already announced must not be synthesized again.
            if cid and cid in announced_ids:
                result.append(evt)
                continue
            pending.append(evt)
            result.append(evt)
            continue

        if etype == "subagent_call":
            # Sub-agent dispatches can interleave with calls from the same assistant
            # message and therefore do not delimit the pending call group.
            result.append(evt)
            continue

        if etype == "assistant_tool_calls":
            # Explicit announcements remove matching calls from the pending group.
            for tc in evt.get("tool_calls") or []:
                tid = str(tc.get("id") or "")
                if tid:
                    announced_ids.add(tid)
            pending = [
                tc
                for tc in pending
                if str(tc.get("call_id") or tc.get("job_id") or "") not in announced_ids
            ]
            result.append(evt)
            continue

        # Announcements must precede the structural event that consumes the calls.
        _flush_pending()
        result.append(evt)

    # An unfinished stream still needs a valid trailing assistant announcement.
    _flush_pending()
    return result


def normalize_resumable_events(
    events: list[dict[str, Any]],
    *,
    live_job_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Mark unfinished tool/sub-agent work as interrupted for history replay.

    Jobs listed in ``live_job_ids`` remain running; all other unfinished work is
    represented by synthetic interrupted results. Missing assistant tool-call
    announcements are also injected so replay preserves valid tool pairing.
    """
    normalized = [dict(evt) for evt in events]
    started_tools: dict[str, dict[str, Any]] = {}
    finished_tools: set[str] = set()
    started_subagents: dict[str, dict[str, Any]] = {}
    finished_subagents: set[str] = set()
    live = live_job_ids or set()

    for evt in normalized:
        etype = evt.get("type", "")
        if etype == "tool_call":
            job_id = evt.get("call_id") or evt.get("job_id") or ""
            if job_id:
                started_tools[str(job_id)] = evt
        elif etype == "tool_result":
            job_id = evt.get("call_id") or evt.get("job_id") or ""
            if job_id:
                finished_tools.add(str(job_id))
        elif etype == "subagent_call":
            job_id = evt.get("job_id") or ""
            if job_id:
                started_subagents[str(job_id)] = evt
        elif etype == "subagent_result":
            job_id = evt.get("job_id") or ""
            if job_id:
                finished_subagents.add(str(job_id))

    synthetic_events: list[dict[str, Any]] = []

    for job_id, start_evt in started_tools.items():
        if job_id in finished_tools or job_id in live:
            continue
        synthetic_events.append(
            {
                "type": "tool_result",
                "name": start_evt.get("name", "tool") or "tool",
                "call_id": job_id,
                "job_id": start_evt.get("job_id", "") or job_id,
                "args": start_evt.get("args", {}),
                "output": "",
                "error": "Interrupted by session resume",
                "interrupted": True,
                "final_state": "interrupted",
                "ts": start_evt.get("ts", 0),
                "_synthetic_resume": True,
            }
        )

    for job_id, start_evt in started_subagents.items():
        if job_id in finished_subagents or job_id in live:
            continue
        synthetic_events.append(
            {
                "type": "subagent_result",
                "name": start_evt.get("name", "subagent") or "subagent",
                "job_id": job_id,
                "task": start_evt.get("task", ""),
                "background": bool(start_evt.get("background", False)),
                "output": "",
                "error": "Interrupted by session resume",
                "success": False,
                "interrupted": True,
                "final_state": "interrupted",
                "ts": start_evt.get("ts", 0),
                "_synthetic_resume": True,
            }
        )

    full = normalized + synthetic_events
    return _inject_synthetic_announcements(full)
