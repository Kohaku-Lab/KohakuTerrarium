"""Incremental extraction of searchable session event blocks."""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from kohakuterrarium.session.history.replay import (
    _coerce_path,
    index_parent_paths,
    index_parent_paths_by_position,
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
    if count:
        state["previous"] = {
            k: v for k, v in events[count - 1].items() if k not in {"event_id", "ts"}
        }
    blocks = _extract_blocks(
        agent,
        events[count:],
        count,
        cursor=state,
        live_ids=None if all_branches else live_ids,
        all_branches=all_branches,
    )
    if all_branches:
        # Older stored turns may omit an explicit parent path. Replay infers
        # their ancestry from physical event order; use the same derivation
        # for indexed blocks so sibling branches cannot pass filtering by
        # matching the child turn/branch alone. Fully stamped modern events
        # avoid this extra history projection.
        missing = [
            block
            for block in blocks
            if block.turn_index is not None
            and block.turn_index > 1
            and block.parent_branch_path is None
        ]
        if missing:
            inferred = index_parent_paths_by_position(events)
            for block in missing:
                if block.event_position is not None:
                    block.parent_branch_path = inferred[block.event_position]
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
    turn_index: int | None = None
    branch_id: int | None = None
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
) -> list[Block]:
    """Extract searchable blocks from session events."""
    state = cursor if cursor is not None else {}
    if live_ids is None and cursor is None and not all_branches:
        live_ids = select_live_event_ids(events)
    blocks: list[Block] = []
    round_num = state.get("round", 0)
    block_num = state.get("block", 0)
    in_round = state.get("in_round", False)
    previous = state.pop("previous", None)
    # Keep only currently open modern turns; sibling branches may interleave.
    open_branches = dict(state.get("open_branches", {})) if all_branches else {}

    for i, evt in enumerate(events):
        signature = {k: v for k, v in evt.items() if k not in {"event_id", "ts"}}
        if signature == previous:
            continue
        previous = signature
        first_block = len(blocks)
        etype = evt.get("type", "")
        ts = evt.get("ts", 0)
        eid = evt.get("event_id")
        if live_ids is not None and isinstance(eid, int) and eid not in live_ids:
            continue

        turn = evt.get("turn_index")
        branch = evt.get("branch_id")
        scoped = (
            all_branches
            and type(turn) is int
            and turn > 0
            and type(branch) is int
            and branch > 0
        )
        scope_key = f"{turn}:{branch}" if scoped else None
        if (
            scope_key is None
            and all_branches
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
            legacy_state = (round_num, block_num, in_round)
            scoped_state = open_branches.get(scope_key, {})
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
            block.turn_index = turn if type(turn) is int and turn > 0 else None
            block.branch_id = branch if type(branch) is int and branch > 0 else None
            if evt.get("parent_branch_path") is not None:
                block.parent_branch_path = _coerce_path(evt["parent_branch_path"])
            elif scoped and etype not in ("user_input", "trigger_fired"):
                # A missing/null continuation inherits its opener's lineage;
                # a new opener must be inferred from physical order instead
                # of reusing an earlier sibling's (turn, branch) scope.
                block.parent_branch_path = scoped_path

        if scope_key is not None:
            if scoped and evt.get("parent_branch_path") is not None:
                scoped_path = _coerce_path(evt["parent_branch_path"])
            elif scoped and etype in ("user_input", "trigger_fired"):
                scoped_path = None
            if in_round:
                next_scope = {"block": block_num, "in_round": True}
                if scoped_path is not None:
                    next_scope["parent_branch_path"] = [
                        list(pair) for pair in scoped_path
                    ]
                open_branches[scope_key] = next_scope
            else:
                open_branches.pop(scope_key, None)
            round_num, block_num, in_round = legacy_state

    state.update(round=round_num, block=block_num, in_round=in_round)
    if all_branches:
        state["open_branches"] = open_branches
    return blocks
