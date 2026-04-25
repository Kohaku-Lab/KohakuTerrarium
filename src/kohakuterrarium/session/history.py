from typing import Any, Iterable

# ---------------------------------------------------------------------
# Parent-branch-path resolution (nested branching).
#
# Each event records ``(turn_index, branch_id)`` natively. Nested
# branching adds a third dimension: the *path of branches* on prior
# turns at the time the event was recorded. New backend code stamps
# this path explicitly via the ``parent_branch_path`` field; pre-
# existing events do not carry it, so we derive it from event order
# (the latest branch of each prior turn seen before this event).
#
# This lets a branch switch on turn N hide every follow-up turn whose
# implicit/explicit parent path no longer matches the user's view.
# ---------------------------------------------------------------------


def _coerce_path(raw: Any) -> tuple[tuple[int, int], ...]:
    """Normalize a parent_branch_path payload into a tuple of pairs.

    Accepts list-of-pairs (JSON friendly) and tuple-of-pairs. Returns
    an empty tuple for invalid / missing input.
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

    Explicit ``parent_branch_path`` on the event wins. Otherwise we
    walk events in order and snapshot the latest branch_id seen on
    every prior turn — that snapshot is the implicit path.
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
    """A parent path is consistent with ``selected`` iff every (t, b)
    in the path matches what the user selected for turn ``t``.

    Turns the path mentions but selected does not are treated as a
    match — those turns simply have not been overridden yet, and the
    default-latest resolver below will pick a branch that matches the
    path on its next pass.
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

    Walks turns in ascending order. For each turn:

    * If ``branch_view`` overrides this turn, use that branch (when it
      exists in the recorded set).
    * Otherwise, take the highest ``branch_id`` whose ``parent_path``
      is consistent with the branches already selected for prior
      turns. This is the natural "latest" within the user's chosen
      subtree.

    Turns whose every branch is incompatible with the selected prior
    turns are simply absent from the result, which removes their
    events from the live set entirely.
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
        # Pick the highest branch_id among compatible candidates.
        selected[ti] = max(bi for _, bi in candidates)
    return selected


def collect_branch_metadata(
    events: Iterable[dict[str, Any]],
    *,
    branch_view: dict[int, int] | None = None,
) -> dict[int, dict[str, Any]]:
    """Extract per-turn branch metadata from an event stream.

    Returns a dict ``{turn_index: {"branches": [branch_id, ...],
    "latest_branch": int, "events_by_branch": {branch_id: [event_ids]}}}``.
    Events without ``turn_index`` / ``branch_id`` are ignored — they
    are non-state events (audit) or pre-branch legacy events that
    treat the whole stream as branch 1.

    When ``branch_view`` is provided, the per-turn ``branches`` list
    is filtered to those whose ``parent_branch_path`` is consistent
    with the user's selections on prior turns. ``latest_branch`` is
    the largest such branch (so the navigator shows ``<x/N>`` based on
    the visible subtree, not the global branch population).
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
        # Only count branches whose parent path is compatible with the
        # current view of prior turns. This is what makes the navigator
        # show <x/N> within the user's subtree, not globally.
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

    Two branches sharing identical user_message content are siblings
    of a single user turn — they differ only in the assistant
    response (regen). Branches with different user_message content
    represent distinct user-side alternatives (edit + rerun).

    Returns ``{turn_index: {"groups": [{"content": str, "branches":
    [int, ...]}], "selected_group_idx": int}}`` for every turn that
    has at least one branch. Empty when the stream has no branched
    turns.

    The grouping mirrors what the frontend's ``_collectBranchMetadata``
    derives so CLI, TUI, and programmatic surfaces show the same
    user-vs-assistant navigator placement.
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

    "Live" means: belongs to the user's selected branch of its turn
    AND its ``parent_branch_path`` matches the user's selected
    branches on every prior turn. Events without turn/branch metadata
    are treated as live (legacy / non-state events).

    Without ``branch_view``, the live subtree is the latest branch at
    every level — i.e. the freshest leaf of the branch tree.
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


def replay_conversation(
    events: Iterable[dict[str, Any]],
    *,
    branch_view: dict[int, int] | None = None,
) -> list[dict[str, Any]]:
    """Rebuild an OpenAI-shape message list from the event log.

    Walks the event stream in order and emits a deterministic message
    list ready to be fed to an LLM provider. Consecutive ``text_chunk``
    events collapse into a single assistant message so the user-facing
    conversation stays clean even though streaming storage is per-chunk.

    By default, when a turn has multiple branches (regenerate /
    edit+rerun), only the latest branch is replayed; sibling branches
    are kept on disk for the ``<1/N>`` navigator. Pass ``branch_view``
    as ``{turn_index: branch_id}`` to override per-turn selection.

    Supported event types:

    - ``user_message``: role/content pair. ``content`` may be a plain
      str or a list of multimodal content parts.
    - ``text_chunk``: accumulator. ``content`` is concatenated with
      subsequent ``text_chunk`` events until a non-chunk event arrives,
      then the buffer is flushed as one assistant message.
    - ``assistant_tool_calls``: attaches the ``tool_calls`` list to the
      pending assistant message (or emits a tool-call-only assistant
      message when no text buffer is present).
    - ``tool_result``: role=tool message carrying ``content`` (from the
      event's ``output`` field) plus ``tool_call_id`` and ``name``.
    - ``system_prompt_set``: role=system message. The most recent one
      wins — replay keeps all of them in order so the caller can see
      the full history.
    - ``compact_replace``: replaces every event whose ``event_id`` falls
      inside ``[replaced_from_event_id, replaced_to_event_id]`` with a
      single assistant summary message.

    Unknown event types are ignored (they are observability-only).
    """
    events_list = list(events)
    # Path-aware live filter. Replaces the old per-turn-only selector
    # so nested branches (turn N has its own siblings under turn N-1's
    # selected branch) are honored.
    live_ids = select_live_event_ids(events_list, branch_view=branch_view)

    # Pre-pass: ``compact_replace`` ranges replace covered events with
    # a single summary message in place.
    replaced_ids: set[int] = set()
    for evt in events_list:
        if evt.get("type") == "compact_replace":
            frm = evt.get("replaced_from_event_id")
            to = evt.get("replaced_to_event_id")
            if isinstance(frm, int) and isinstance(to, int):
                for eid in range(frm, to + 1):
                    replaced_ids.add(eid)

    messages: list[dict[str, Any]] = []
    text_buf: list[str] = []

    def _flush_text() -> None:
        if not text_buf:
            return
        content = "".join(text_buf)
        if content:
            messages.append({"role": "assistant", "content": content})
        text_buf.clear()

    for evt in events_list:
        etype = evt.get("type", "")
        eid = evt.get("event_id")

        # Skip events outside the live subtree (wrong branch / wrong
        # parent path). Events without an event_id (synthetic / inline)
        # always replay.
        if isinstance(eid, int) and eid not in live_ids:
            continue

        if isinstance(eid, int) and eid in replaced_ids and etype != "compact_replace":
            continue

        if etype in ("text_chunk", "text"):
            chunk = evt.get("content", "")
            if isinstance(chunk, str):
                text_buf.append(chunk)
            continue

        # Any non-chunk event flushes the buffer first.
        _flush_text()

        if etype == "user_message":
            messages.append({"role": "user", "content": evt.get("content", "")})
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
                    "name": evt.get("name", ""),
                }
            )
        elif etype == "system_prompt_set":
            messages.append({"role": "system", "content": evt.get("content", "")})
        elif etype == "compact_replace":
            messages.append(
                {
                    "role": "assistant",
                    "content": evt.get("summary_text", ""),
                }
            )

    _flush_text()
    return messages


def normalize_resumable_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mark unfinished tool/sub-agent work as interrupted for history replay."""
    normalized = [dict(evt) for evt in events]
    started_tools: dict[str, dict[str, Any]] = {}
    finished_tools: set[str] = set()
    started_subagents: dict[str, dict[str, Any]] = {}
    finished_subagents: set[str] = set()

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
        if job_id in finished_tools:
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
        if job_id in finished_subagents:
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

    if not synthetic_events:
        return normalized

    return normalized + synthetic_events
