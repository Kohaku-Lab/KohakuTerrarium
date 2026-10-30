"""Normalize, branch-select, and replay persisted session events."""

import json
from collections.abc import Hashable
from typing import Any, Iterable

from kohakuterrarium.core.compact.branch import (
    CompactRule,
    compact_rule_from_event,
)
from kohakuterrarium.core.jobs.label import canonical_tool_name, make_job_label
from kohakuterrarium.llm.message import coalesce_user_contents
from kohakuterrarium.session.assistant_history import (
    _coerce_tool_args_to_json,
    assistant_round_messages,
    project_assistant_rounds,
    replay_assistant_event,
)
from kohakuterrarium.session.branch_paths import (  # noqa: F401 - re-exported
    InvalidBranchViewError,
    _coerce_branch_view,
    _coerce_path,
    _path_matches,
    index_parent_paths,
    resolve_branch_view_strict,
)
from kohakuterrarium.session.history_selection import SelectionCompatibility

# Parent paths identify the selected branches of earlier turns. Legacy events
# derive this ancestry from event order when no explicit path was persisted.


def resolve_selected_branches(
    events_list: list[dict[str, Any]],
    parent_paths: dict[int, tuple[tuple[int, int], ...]],
    branch_view: dict[int, int] | None,
) -> dict[int, int]:
    """Pick a live branch for each turn while respecting nested paths.

    Turns resolve in ascending order. Valid overrides win; otherwise the highest
    compatible branch is selected. Turns with no compatible branch are omitted
    from the live subtree.
    Public: shared by replay selection and ``session.resume`` branch-state
    restore.
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


def project_branch_metadata(
    events: Iterable[dict[str, Any]],
    branch_view: dict[int, int] | None = None,
) -> dict[int, dict[str, Any]]:
    """Project branch choices, ancestry, and the selected coherent path."""
    events_list = list(events)
    selected = resolve_branch_view_strict(events_list, branch_view)
    parent_paths = index_parent_paths(events_list)
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
    parent_paths = index_parent_paths(events_list)
    selected = resolve_selected_branches(events_list, parent_paths, branch_view)

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
    parent_paths = index_parent_paths(events_list)
    selected = resolve_selected_branches(events_list, parent_paths, branch_view)
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
    parent_paths: dict[int, tuple[tuple[int, int], ...]] | None = None,
) -> set[int]:
    """Return the event_ids that belong to the live subtree.

    Live events belong to the selected branch and compatible prior ancestry.
    Legacy or non-state events without branch metadata remain live. Without an
    override, the latest compatible branch is selected at every turn.
    Supplied parent paths must describe the same unchanged events in the same order.
    """
    events_list = list(events)
    if parent_paths is None:
        parent_paths = index_parent_paths(events_list)
    selected = resolve_selected_branches(events_list, parent_paths, branch_view)

    compatibility = SelectionCompatibility(selected)
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
        if path and not compatibility.matches(path, ti):
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
    """Prefer recorded identity, repairing legacy labels or missing names."""
    name = evt.get("tool_name") or evt.get("name", "")
    if isinstance(name, str) and name:
        return canonical_tool_name(name)
    call_id = evt.get("call_id") or evt.get("job_id") or ""
    if call_id:
        tool_name, _ = make_job_label(str(call_id))
        if tool_name:
            return tool_name
    return name if isinstance(name, str) else ""


def _covered_by_replace(
    eid: object,
    turn_index: object,
    branch_id: object,
    rules: list[CompactRule],
) -> bool:
    """Whether a replacement range and its optional branch path cover an event."""
    return any(rule.covers(eid, turn_index, branch_id) for rule in rules)


def _rule_priority(
    rule: CompactRule,
    selected_pairs: set[tuple[int, int]],
) -> tuple[int, int]:
    """Rank current-lineage summaries above legacy and incompatible summaries."""
    return rule.priority(selected_pairs)


def compact_replacement_rules(
    events: list[dict[str, Any]],
    branch_view: dict[int, int] | None = None,
    *,
    parent_paths: dict[int, tuple[tuple[int, int], ...]] | None = None,
) -> list[CompactRule]:
    """Select summary ranges; supplied paths must match unchanged events and order."""
    if parent_paths is None:
        parent_paths = index_parent_paths(events)
    selected = resolve_selected_branches(events, parent_paths, branch_view)
    selected_pairs = set(selected.items())
    rules = [
        rule for evt in events if (rule := compact_rule_from_event(evt)) is not None
    ]
    eligible = [r for r in rules if _rule_priority(r, selected_pairs)[0] > 0]
    return (
        [
            max(
                enumerate(eligible),
                key=lambda item: (*_rule_priority(item[1], selected_pairs), item[0]),
            )[1]
        ]
        if eligible
        else []
    )


def compact_covered_event_ids(
    events: list[dict[str, Any]],
    branch_view: dict[int, int] | None = None,
    *,
    parent_paths: dict[int, tuple[tuple[int, int], ...]] | None = None,
) -> set[int]:
    """Return covered identities; supplied paths must match unchanged events and order."""
    rules = compact_replacement_rules(events, branch_view, parent_paths=parent_paths)
    covered = {
        evt["event_id"]
        for evt in events
        if _covered_by_replace(
            evt.get("event_id"), evt.get("turn_index"), evt.get("branch_id"), rules
        )
    }
    for previous, event in zip(events, events[1:]):
        if (
            previous.get("event_id") not in covered
            or event.get("type") != "tool_result"
        ):
            continue
        if {k: v for k, v in previous.items() if k not in {"event_id", "ts"}} == {
            k: v for k, v in event.items() if k not in {"event_id", "ts"}
        } and isinstance(event.get("event_id"), int):
            covered.add(event["event_id"])
    return covered


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
    parent_paths = index_parent_paths(events_list)
    # Nested branch ancestry determines the live event set and the selected
    # branch projection compaction rules are validated against.
    live_ids = select_live_event_ids(
        events_list, branch_view=branch_view, parent_paths=parent_paths
    )
    provider_ids = {
        evt.get("call_id") or evt.get("job_id"): evt["tool_call_id"]
        for evt in events_list
        if evt.get("type") == "tool_call"
        and evt.get("tool_call_id")
        and (not isinstance(evt.get("event_id"), int) or evt["event_id"] in live_ids)
    }
    replaced_rules = compact_replacement_rules(
        events_list, branch_view, parent_paths=parent_paths
    )
    # Summaries are emitted where the replaced content sat, not at the
    # compact_replace event's own stream position (a compact runs after the
    # live tail it preserves). pending summaries are ordered by replaced_from
    # and flushed when the live stream reaches the first event past a range.
    pending_rules = sorted(replaced_rules, key=lambda rule: rule.replaced_from)

    messages: list[dict[str, Any]] = []
    text_buf: list[str] = []
    merge_image = False
    canonical_target = None
    # A turn/branch owns exactly one user_message. Graph merge and
    # migration can copy the same event twice; duplicate user messages
    # would break turn-targeted edit resolution and pollute the view.
    seen_user_messages: set[tuple[int, int]] = set()
    injected_at = -1

    def _flush_text() -> None:
        nonlocal merge_image
        if not text_buf:
            return
        content = "".join(text_buf)
        if content:
            if (
                merge_image
                and messages
                and isinstance(messages[-1].get("content"), list)
            ):
                messages[-1]["content"].append({"type": "text", "text": content})
            else:
                messages.append({"role": "assistant", "content": content})
            merge_image = True
        text_buf.clear()

    def _flush_pending_summaries(
        eid: int, turn_index: object, branch_id: object
    ) -> None:
        # Scan every pending rule, not just the front one: a rule whose range
        # has not passed and whose path does not intersect this branch (e.g. a
        # sibling-branch compact) must not block a later rule that DOES apply —
        # otherwise that summary is injected late or lost entirely.
        i = 0
        while i < len(pending_rules):
            rule = pending_rules[i]
            if rule.replaced_from > eid:
                break
            # Inject the summary only when the current branch actually has an
            # event covered by this rule; a rule whose path does not intersect
            # the selected branch must NOT inject a spurious summary. Rules
            # without a path (legacy migration data) cover the whole range.
            if _covered_by_replace(eid, turn_index, branch_id, [pending_rules[i]]):
                # Flush buffered assistant text that precedes the replaced
                # range before injecting the summary (a covered event does not
                # otherwise delimit the text buffer).
                _flush_text()
                if rule.summary:
                    message = {"role": "assistant", "content": rule.summary}
                    if include_metadata:
                        message["metadata"] = {
                            "compact_summary": True,
                            "compact_result_event_ids": sorted(rule.result_event_ids),
                            "compact_source_path": (
                                sorted([t, b] for t, b in rule.source_path)
                                if rule.source_recorded and rule.source_path
                                else None
                            ),
                        }
                    messages.append(message)
                pending_rules.pop(i)
                continue
            if eid > rule.replaced_to:
                # Range passed with no covered event on this branch — drop.
                pending_rules.pop(i)
                continue
            # Range not passed and event not covered — keep pending and scan
            # the next rule; it may apply to this branch right now.
            i += 1

    eligible_ids = live_ids - {
        evt.get("event_id")
        for evt in events_list
        if _covered_by_replace(
            evt.get("event_id"),
            evt.get("turn_index"),
            evt.get("branch_id"),
            replaced_rules,
        )
    }
    for evt in project_assistant_rounds(events_list, eligible_ids):
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

        if etype in {
            "processing_start",
            "processing_complete",
            "assistant_round_start",
        }:
            _flush_text()
            merge_image = False
            canonical_target = None
            continue
        if etype in (
            "assistant_round_display",
            "assistant_round_end",
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
            canonical_target = None
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
        elif etype == "user_input_injected":
            content = evt.get("content", "")
            if injected_at == len(messages) - 1 and messages:
                previous = messages[-1]
                previous["content"] = coalesce_user_contents(
                    [previous["content"], content]
                )
            else:
                message = {"role": "user", "content": content}
                if include_metadata:
                    message["metadata"] = {
                        "event_id": evt.get("event_id"),
                        "turn_index": evt.get("turn_index"),
                        "branch_id": evt.get("branch_id"),
                    }
                messages.append(message)
                injected_at = len(messages) - 1
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
            job_id = evt.get("call_id", "") or evt.get("job_id", "")
            message = {
                "role": "tool",
                "content": evt.get("output", "") or "",
                "tool_call_id": evt.get("tool_call_id")
                or provider_ids.get(job_id, job_id),
                "name": _clean_tool_name(evt),
            }
            if include_metadata:
                message["metadata"] = {
                    "job_id": job_id,
                    "result_event_id": evt.get("_source_event_id", evt.get("event_id")),
                    "tool_result_phase": "final",
                }
            messages.append(message)
        elif etype == "system_prompt_set":
            messages.append({"role": "system", "content": evt.get("content", "")})
        elif etype in {"assistant_message", "assistant_image", "assistant_reasoning"}:
            replay_assistant_event(
                messages, evt, include_metadata, merge_image, canonical_target
            )
            if etype == "assistant_message" and evt.get("_assistant_projected"):
                canonical_target = messages[-1]
        merge_image = etype == "assistant_image"

    _flush_text()
    return messages


def _inject_synthetic_announcements(
    events: list[dict[str, Any]],
    covered_event_ids: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Insert missing assistant tool-call announcements before consumption.

    Pending calls are grouped until a structural event requires them. Existing
    announcements suppress duplicates. Synthetic announcements intentionally omit
    event identifiers so replay preserves their inserted position.
    """
    uncovered_results = {
        str(evt.get("call_id") or evt.get("job_id") or "")
        for evt in events
        if evt.get("_settled_from_call")
    }
    pending: list[dict[str, Any]] = []
    announced_ids: set[str] = set()
    result: list[dict[str, Any]] = []

    def _build_announce(items: list[dict[str, Any]]) -> dict[str, Any]:
        tool_calls: list[dict[str, Any]] = []
        for tc in items:
            tool_calls.append(
                {
                    "id": _provider_call_id(tc),
                    "type": "function",
                    "function": {
                        "name": _clean_tool_name(tc),
                        "arguments": _coerce_tool_args_to_json(
                            tc.get("tool_call_arguments") or tc.get("args")
                        ),
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
            cid = _provider_call_id(tc)
            if cid:
                announced_ids.add(cid)
        pending.clear()

    eligible = {evt.get("event_id") for evt in events} - (covered_event_ids or set())
    canonical = assistant_round_messages(events, eligible)
    for evt in events:
        etype = evt.get("type", "")
        if evt.get("event_id") in canonical:
            _flush_pending()
            announced_ids.update(
                str(call.get("id") or "")
                for call in (canonical[evt["event_id"]].get("tool_calls") or [])
            )
        if etype in {"user_input", "user_message"}:
            _flush_pending()
            announced_ids.clear()
        if evt.get("event_id") in (covered_event_ids or ()) and not (
            etype == "tool_call"
            and str(evt.get("call_id") or evt.get("job_id") or "") in uncovered_results
        ):
            result.append(evt)
            continue

        if etype == "tool_call":
            cid = _provider_call_id(evt)
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
                tc for tc in pending if _provider_call_id(tc) not in announced_ids
            ]
            result.append(evt)
            continue

        # Announcements must precede the structural event that consumes the calls.
        _flush_pending()
        result.append(evt)

    # An unfinished stream still needs a valid trailing assistant announcement.
    _flush_pending()
    return result


def _provider_call_id(event: dict[str, Any]) -> str:
    return str(
        event.get("tool_call_id") or event.get("call_id") or event.get("job_id") or ""
    )


def normalize_tool_call_events(
    events: Iterable[dict[str, Any]],
    *,
    covered_event_ids: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Copy events and restore missing assistant tool-call announcements.

    Unlike resume normalization, live history replay must not finalize an
    unfinished background job as interrupted. It only repairs the declaration
    required to pair persisted tool results with their assistant call.
    """
    return _inject_synthetic_announcements(
        [dict(evt) for evt in events], covered_event_ids
    )


def normalize_resumable_events(
    events: list[dict[str, Any]],
    *,
    live_job_ids: set[str] | None = None,
    covered_event_ids: set[int] | None = None,
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
        if evt.get("event_id") in (covered_event_ids or ()):
            continue
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
    return normalize_tool_call_events(full, covered_event_ids=covered_event_ids)
