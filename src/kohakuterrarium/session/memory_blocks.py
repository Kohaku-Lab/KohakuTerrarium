"""Incremental extraction of searchable session event blocks."""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from kohakuterrarium.session.history import (
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


def extract_batch(
    agent: str, events: list[dict], checkpoint: dict | None = None, start_from: int = 0
):
    """Extract the append-only suffix, retaining round and branch context."""
    state = dict(checkpoint or {})
    count = state.get("count", 0)
    if count > len(events) or (
        count and state.get("anchor") != _anchor(events[count - 1])
    ):
        raise RebuildRequired
    branches = dict(state.get("branches", {}))
    legacy_conflicts = state.get("legacy_conflicts", [])
    max_turn = max((int(t) for t in branches), default=0)
    if checkpoint:
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
    else:
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
        agent, events[count:], count, cursor=state, live_ids=live_ids
    )
    if start_from > count:
        blocks = [
            b
            for b in blocks
            if int(b.block_id.rsplit(":e", 1)[1].split(":")[0]) >= start_from
        ]
    state.update(
        count=len(events),
        branches=branches,
        legacy_conflicts=legacy_conflicts,
        anchor=_anchor(events[-1]) if events else "",
    )
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
) -> list[Block]:
    """Extract searchable blocks from session events."""
    state = cursor if cursor is not None else {}
    if live_ids is None and cursor is None:
        live_ids = select_live_event_ids(events)
    blocks: list[Block] = []
    round_num = state.get("round", 0)
    block_num = state.get("block", 0)
    in_round = state.get("in_round", False)
    previous = state.pop("previous", None)

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

        if etype == "user_input":
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

        for part, block in enumerate(blocks[first_block:]):
            block.block_id = f"{agent}:e{event_offset + i}:p{part}"

    state.update(round=round_num, block=block_num, in_round=in_round)
    return blocks
