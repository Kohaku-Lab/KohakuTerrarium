"""Incremental extraction of searchable session event blocks."""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from kohakuterrarium.session.history.branch_paths import (
    index_eligible_parent_paths_by_position,
)
from kohakuterrarium.session.history.semantics import (
    BranchScope,
    MEMORY_ROUND_OPENERS,
    can_continue_legacy_round,
    classify_branch_scope,
)
from kohakuterrarium.session.history.replay import (
    _coerce_path,
    index_parent_paths,
    resolve_selected_branches,
    select_live_event_ids,
)

TOOL_RESULT_INDEX_CHARS = 50_000


class RebuildRequired(Exception):
    """An indexed prefix no longer describes the current live history."""


def _anchor(event: dict) -> str:
    return hashlib.sha256(
        json.dumps(event, sort_keys=True, default=str).encode()
    ).hexdigest()


def _dedup_signature(event: dict) -> str:
    """Stable, type-sensitive identity excluding only transport metadata.

    The JSON representation distinguishes boolean coordinates from integers;
    a digest keeps incremental cursor state bounded even for large tool output.
    """
    payload = {k: v for k, v in event.items() if k not in {"event_id", "ts"}}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()


def extract_batch(
    agent: str,
    events: list[dict],
    checkpoint: dict | None = None,
    start_from: int = 0,
    *,
    all_branches: bool = False,
):
    """Extract the append-only suffix.

    All-branch indexing is staged separately until retrieval can filter before
    applying top-k, so existing creature searches retain their old semantics.
    """
    state = dict(checkpoint or {})
    if checkpoint and bool(state.get("all_branches", False)) != all_branches:
        raise RebuildRequired("memory index extraction scope changed")
    count = state.get("count", 0)
    if count > len(events) or (
        count and state.get("anchor") != _anchor(events[count - 1])
    ):
        raise RebuildRequired
    branches = dict(state.get("branches", {}))
    legacy_conflicts = state.get("legacy_conflicts", [])
    max_turn = max((int(t) for t in branches), default=0)
    if checkpoint and not all_branches:
        for event in events[count:]:
            turn, branch = event.get("turn_index"), event.get("branch_id")
            if not isinstance(turn, int) or not isinstance(branch, int):
                continue
            key = str(turn)
            if key not in branches and turn <= max_turn:
                raise RebuildRequired
            if key in branches and branches[key] != branch:
                raise RebuildRequired
            path = _coerce_path(event.get("parent_branch_path"))
            if not path and any(t < turn for t in legacy_conflicts):
                raise RebuildRequired
            for parent_turn, parent_branch in path:
                if (
                    str(parent_turn) in branches
                    and branches[str(parent_turn)] != parent_branch
                ):
                    raise RebuildRequired
            branches[key] = branch
            max_turn = max(max_turn, turn)
        live_ids = None
    elif not all_branches:
        paths = index_parent_paths(events)
        branches = {
            str(k): v for k, v in resolve_selected_branches(events, paths, None).items()
        }
        latest = {}
        for event in events:
            turn, branch = event.get("turn_index"), event.get("branch_id")
            if isinstance(turn, int) and isinstance(branch, int):
                latest[turn] = max(latest.get(turn, 0), branch)
        legacy_conflicts = [t for t, b in latest.items() if branches.get(str(t)) != b]
        live_ids = select_live_event_ids(events)
    if count and "dedup_signature" not in state:
        # Compatibility for older selected-lineage cursors that predate the
        # bounded signature. Fresh all-branch v2 checkpoints always persist
        # the last eligible predecessor, not the last raw event.
        state["dedup_signature"] = _dedup_signature(events[count - 1])
    event_paths = None
    if all_branches:
        # Fully stamped modern events need no whole-history inference. Legacy
        # missing-path continuations use the same strict lineage projection
        # as Search selection and Context Navigation before round assignment.
        suffix = events[count:]
        if any(
            event.get("parent_branch_path") is None
            and type(event.get("turn_index")) is int
            and event["turn_index"] > 1
            and classify_branch_scope(event.get("turn_index"), event.get("branch_id"))
            is BranchScope.EXPLICIT
            for event in suffix
        ):
            event_paths = index_eligible_parent_paths_by_position(events)
        else:
            event_paths = {
                count + pos: _coerce_path(event.get("parent_branch_path"))
                for pos, event in enumerate(suffix)
                if classify_branch_scope(
                    event.get("turn_index"), event.get("branch_id")
                )
                is BranchScope.EXPLICIT
            }
    blocks = _extract_blocks(
        agent,
        events[count:],
        count,
        cursor=state,
        live_ids=None if all_branches else live_ids,
        all_branches=all_branches,
        event_paths=event_paths,
    )
    if start_from > count:
        blocks = [
            b
            for b in blocks
            if int(b.block_id.rsplit(":e", 1)[1].split(":")[0]) >= start_from
        ]
    state.update(count=len(events), anchor=_anchor(events[-1]) if events else "")
    if all_branches:
        state["all_branches"] = True
        state["source_refs_complete"] = state.get("source_refs_complete", True) and all(
            bool(block.source_ref) for block in blocks
        )
    else:
        state.update(branches=branches, legacy_conflicts=legacy_conflicts)
    return blocks, state


@dataclass
class Block:
    """A searchable block within a round."""

    round_num: int
    block_num: int
    agent: str
    block_type: str
    content: str
    ts: float = 0.0
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    channel: str = ""
    block_id: str = ""
    # The per-agent event position is a lookup hint, not a physical key.
    event_id: int | None = None
    event_position: int | None = None
    part_index: int = 0
    # Raw coordinates remain visible to the shared history classifier.
    # Invalid coordinates must never be normalized into legacy None/None.
    turn_index: Any = None
    branch_id: Any = None
    parent_branch_path: tuple[tuple[int, int], ...] | None = None
    source_fingerprint: str = ""
    source_ref: str = ""


def _content_to_text(content: Any) -> str:
    """Flatten an event's ``content`` to a single searchable string.

    Strings pass through, multimodal parts contribute searchable text or stable
    attachment markers, and dictionaries are handled as single parts.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for part in content:
            if isinstance(part, str):
                chunks.append(part)
            elif isinstance(part, dict):
                if isinstance(part.get("text"), str):
                    chunks.append(part["text"])
                elif isinstance(part.get("content"), str):
                    chunks.append(part["content"])
                else:
                    kind = part.get("type") or ""
                    if kind in ("image_url", "image"):
                        chunks.append("[image]")
                    elif kind == "file":
                        chunks.append("[file]")
        return " ".join(c for c in chunks if c)
    if isinstance(content, dict):
        return _content_to_text([content])
    return "" if content is None else str(content)


def _extract_blocks(
    agent: str,
    events: list[dict],
    event_offset: int = 0,
    *,
    cursor: dict | None = None,
    live_ids: set[int] | None = None,
    all_branches: bool = False,
    event_paths: dict[int, tuple[tuple[int, int], ...] | None] | None = None,
) -> list[Block]:
    """Extract searchable blocks from session events."""
    state = cursor if cursor is not None else {}
    if all_branches and event_paths is None:
        event_paths = {
            event_offset + pos: path
            for pos, path in index_eligible_parent_paths_by_position(events).items()
        }
    if live_ids is None and cursor is None and not all_branches:
        live_ids = select_live_event_ids(events)
    blocks: list[Block] = []
    round_num = state.get("round", 0)
    block_num = state.get("block", 0)
    in_round = state.get("in_round", False)
    previous = state.get("dedup_signature")
    # Keep only currently open modern turns; sibling branches may interleave.
    open_branches = dict(state.get("open_branches", {})) if all_branches else {}

    for i, evt in enumerate(events):
        first_block = len(blocks)
        etype = evt.get("type", "")
        ts = evt.get("ts", 0)
        eid = evt.get("event_id")
        turn = evt.get("turn_index")
        branch = evt.get("branch_id")
        coordinate_scope = classify_branch_scope(turn, branch)
        if (
            live_ids is not None
            and isinstance(eid, int)
            and eid not in live_ids
            and coordinate_scope is not BranchScope.BOOKKEEPING
        ):
            continue
        if not all_branches and coordinate_scope is BranchScope.INVALID:
            # Replay retains its historical compatibility projection, but
            # scoped memory indexing must not publish malformed coordinates.
            # Keep this strict policy the same as all-branch search visibility.
            continue
        # Invalid records retain their physical metadata but cannot open or
        # close a trusted conversational round.
        before_invalid = (
            (round_num, block_num, in_round)
            if coordinate_scope is BranchScope.INVALID
            else None
        )
        scoped = all_branches and coordinate_scope is BranchScope.EXPLICIT
        ancestry = event_paths.get(event_offset + i) if scoped else None
        if scoped and ancestry is None:
            # Missing ancestry with multiple open mirrored rounds is
            # ambiguous. It cannot continue or terminate either round.
            continue
        if coordinate_scope is not BranchScope.INVALID:
            # Strict Memory eligibility and physical-lineage selection decide
            # which events may influence deduplication. Malformed events may
            # still be materialized for unscoped provenance, but cannot
            # suppress a subsequent valid opener or poison the cursor.
            signature = _dedup_signature(evt)
            if signature == previous:
                continue
            previous = signature
        scope_key = (
            f"{turn}:{branch}:{json.dumps(ancestry, separators=(',', ':'))}"
            if scoped
            else None
        )
        if (
            scope_key is None
            and all_branches
            and coordinate_scope is BranchScope.LEGACY
            and not in_round
            and len(open_branches) == 1
            and etype
            in ("text", "text_chunk", "tool_call", "tool_result", "processing_end")
        ):
            # Some migrated sessions stamp their turn opener but not its
            # subsequent text/tools. Only associate an unstamped continuation
            # when exactly one scoped round is open; never guess between siblings.
            scope_key = next(iter(open_branches))
        if scope_key is not None:
            inherited = (
                scoped
                and scope_key not in open_branches
                and (
                    can_continue_legacy_round(
                        etype,
                        legacy_open=in_round,
                        legacy_round=round_num,
                        explicit_turn=turn,
                        other_open_scopes=len(open_branches),
                    )
                )
            )
            # Transfer ownership of an unambiguous legacy round to its newly
            # stamped continuation; do not leave a second legacy round open
            # after a stamped processing_end.
            legacy_state = (round_num, block_num, False if inherited else in_round)
            scoped_state = open_branches.get(
                scope_key,
                {"block": block_num, "in_round": True} if inherited else {},
            )
            scoped_path = (
                _coerce_path(scoped_state["parent_branch_path"])
                if "parent_branch_path" in scoped_state
                else None
            )
            round_num = turn if scoped else int(scope_key.split(":", 1)[0])
            block_num = scoped_state.get("block", 0)
            in_round = scoped_state.get("in_round", False)

        if etype == "user_input":
            if not scoped:
                round_num += 1
            block_num = 0
            in_round = True
            content = _content_to_text(evt.get("content", ""))
            if content.strip():
                blocks.append(
                    Block(
                        round_num=round_num,
                        block_num=block_num,
                        agent=agent,
                        block_type="user",
                        content=content,
                        ts=ts,
                    )
                )
                block_num += 1

        elif etype == "trigger_fired":
            if not scoped:
                round_num += 1
            block_num = 0
            in_round = True
            channel = evt.get("channel", "")
            content = _content_to_text(evt.get("content", ""))
            label = f"[trigger:{channel}] {content}" if channel else content
            if label.strip():
                blocks.append(
                    Block(
                        round_num=round_num,
                        block_num=block_num,
                        agent=agent,
                        block_type="trigger",
                        content=label,
                        ts=ts,
                        channel=channel,
                    )
                )
                block_num += 1

        elif etype in ("text", "text_chunk") and in_round:
            # Stream chunks remain separate searchable blocks in event order.
            content = _content_to_text(evt.get("content", ""))
            # Paragraph splits improve retrieval precision for long responses.
            paragraphs = content.split("\n\n") if len(content) > 300 else [content]
            for para in paragraphs:
                if para.strip():
                    blocks.append(
                        Block(
                            round_num=round_num,
                            block_num=block_num,
                            agent=agent,
                            block_type="text",
                            content=para.strip(),
                            ts=ts,
                        )
                    )
                    block_num += 1

        elif etype == "tool_call" and in_round:
            name = evt.get("name", "")
            args = evt.get("args", {})
            args_text = " ".join(
                f"{k}={v}" for k, v in args.items() if k != "_tool_call_id"
            )
            content = f"[tool:{name}] {args_text}"
            blocks.append(
                Block(
                    round_num=round_num,
                    block_num=block_num,
                    agent=agent,
                    block_type="tool",
                    content=content[:1000],
                    ts=ts,
                    tool_name=name,
                    tool_args=args,
                )
            )
            block_num += 1

        elif etype == "tool_result" and in_round:
            name = evt.get("name", "")
            output = _content_to_text(evt.get("output", ""))
            error = _content_to_text(evt.get("error", ""))
            content = f"[result:{name}] {error or output}"
            if content.strip() and len(content) > 20:
                blocks.append(
                    Block(
                        round_num=round_num,
                        block_num=block_num,
                        agent=agent,
                        block_type="tool",
                        content=content[:TOOL_RESULT_INDEX_CHARS],
                        ts=ts,
                        tool_name=name,
                    )
                )
                block_num += 1

        elif etype == "processing_end":
            in_round = False

        # Hash the physical source only once, even if it yields many paragraphs.
        event_blocks = blocks[first_block:]
        fingerprint = _anchor(evt) if event_blocks else ""
        for part, block in enumerate(event_blocks):
            block.source_fingerprint = fingerprint
            block.source_ref = getattr(evt, "source_ref", "")
            block.block_id = f"{agent}:e{event_offset + i}:p{part}"
            block.event_id = eid if type(eid) is int else None
            block.event_position = event_offset + i
            block.part_index = part
            # The original pair, even if incomplete or malformed, is part
            # of the provenance that branch projection must validate.
            block.turn_index = turn
            block.branch_id = branch
            if scoped:
                block.parent_branch_path = ancestry
            elif evt.get("parent_branch_path") is not None:
                block.parent_branch_path = _coerce_path(evt["parent_branch_path"])

        if scope_key is not None:
            if scoped:
                scoped_path = ancestry
            if in_round:
                next_scope = {"block": block_num, "in_round": True}
                if scoped_path is not None:
                    next_scope["parent_branch_path"] = [
                        list(pair) for pair in scoped_path
                    ]
                open_branches[scope_key] = next_scope
            else:
                open_branches.pop(scope_key, None)
            # A stamped opener owns its explicit turn number. Advance only
            # the legacy fallback clock, never assign a sibling's block state.
            # This prevents a later legacy opener from restarting at round 1.
            completed_turn = (
                round_num if scoped and etype in MEMORY_ROUND_OPENERS else 0
            )
            round_num, block_num, in_round = legacy_state
            round_num = max(round_num, completed_turn)
        if before_invalid is not None:
            round_num, block_num, in_round = before_invalid

    state.update(
        round=round_num,
        block=block_num,
        in_round=in_round,
        dedup_signature=previous,
    )
    if all_branches:
        state["open_branches"] = open_branches
    return blocks
