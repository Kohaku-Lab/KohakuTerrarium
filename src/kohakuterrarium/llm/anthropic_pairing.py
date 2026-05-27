"""Anthropic Messages API tool_use ↔ tool_result pairing enforcement.

Mirrors what ``codex_format.fix_tool_call_pairing`` does for the Codex
Responses API: a final pass over the converted message list that fixes
ordering, synthesises missing tool_result blocks for unmatched
tool_use, and drops orphan tool_result blocks. Anthropic's API
rejects (400) any conversation where a ``tool_use`` block lacks a
matching ``tool_result`` in the immediately-following user message
or where a ``tool_result`` references no preceding ``tool_use``;
this pass keeps those constraints satisfied even after interrupts,
branch switches, mid-turn input injection, or compaction.
"""

from copy import deepcopy
from typing import Any

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Synthetic placeholder content for a ``tool_use`` block whose matching
# ``tool_result`` is missing from the converted conversation. Shaped
# so the model treats it as a recoverable interruption ("retry if you
# still need the result") rather than a hard failure to debug.
SYNTHETIC_TOOL_RESULT_TEXT = (
    "Tool call was interrupted or removed before producing a result. "
    "This may not mean any error — if you receive no new input from "
    "the user, you can retry the call."
)


def synthetic_tool_result_block(tool_use_id: str, tool_name: str) -> dict[str, Any]:
    """Build a placeholder ``tool_result`` block for an unmatched
    ``tool_use``. ``is_error: True`` is honoured by Claude — the model
    treats the result as a failure and decides whether to retry."""
    label = f"[{tool_name}] " if tool_name else ""
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": f"{label}{SYNTHETIC_TOOL_RESULT_TEXT}",
        "is_error": True,
    }


def fix_anthropic_tool_block_pairing(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Enforce Anthropic's strict ``tool_use`` ↔ ``tool_result`` pairing.

    Three transformations applied to the converted Anthropic native
    message list:

    1. **Splice** ``tool_result`` blocks from later user messages up
       to the user message immediately following the matching
       ``assistant``. Ordering-only fix — a real user text that
       landed between the assistant and the eventual tool_result
       (e.g. opportunistic input injection) stays in place, just
       after the synthesised tool_result group.
    2. **Synthesise** an ``is_error: True`` placeholder for any
       ``tool_use`` block whose ``tool_result`` is missing entirely
       (interrupted job, dropped by compaction, persisted-but-
       orphaned by a crash). Placeholder content tells the model
       the call was interrupted and that retrying is valid.
    3. **Drop** orphan ``tool_result`` blocks — a ``tool_result``
       whose ``tool_use_id`` never appears in any preceding
       ``assistant`` message. A user message that ends up empty
       after dropping its orphan blocks is itself dropped.

    Idempotent — running the pass on its own output yields the same
    result.
    """
    if not messages:
        return messages

    # Pre-pass — index every ``tool_result`` block by tool_use_id so
    # the main pass can splice it up to immediately follow its matching
    # assistant regardless of where it ended up. Last-occurrence wins
    # for duplicate ids (retry produced two results — Anthropic accepts
    # only one, so we use the later one).
    result_block_locations: dict[str, tuple[int, int, dict[str, Any]]] = {}
    for mi, msg in enumerate(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for bi, block in enumerate(content):
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tid = str(block.get("tool_use_id") or "")
            if tid:
                result_block_locations[tid] = (mi, bi, block)

    consumed_locations: set[tuple[int, int]] = set()
    seen_tool_use_ids: set[str] = set()
    rebuilt: list[dict[str, Any]] = []

    for mi, msg in enumerate(messages):
        role = msg.get("role")
        if role == "assistant":
            rebuilt.append(msg)
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            tool_uses = [
                block
                for block in content
                if isinstance(block, dict) and block.get("type") == "tool_use"
            ]
            if not tool_uses:
                continue
            tool_result_blocks: list[dict[str, Any]] = []
            for tu_block in tool_uses:
                tu_id = str(tu_block.get("id") or "")
                tu_name = str(tu_block.get("name") or "")
                if not tu_id:
                    continue
                seen_tool_use_ids.add(tu_id)
                located = result_block_locations.get(tu_id)
                if located is not None:
                    loc_mi, loc_bi, located_block = located
                    consumed_locations.add((loc_mi, loc_bi))
                    tool_result_blocks.append(deepcopy(located_block))
                else:
                    logger.warning(
                        "Synthesised missing tool_result for unmatched tool_use",
                        tool_use_id=tu_id,
                        tool_name=tu_name,
                    )
                    tool_result_blocks.append(
                        synthetic_tool_result_block(tu_id, tu_name)
                    )
            if tool_result_blocks:
                rebuilt.append({"role": "user", "content": tool_result_blocks})
        elif role == "user":
            content = msg.get("content")
            if not isinstance(content, list):
                rebuilt.append(msg)
                continue
            filtered: list[dict[str, Any]] = []
            dropped_orphan = 0
            for bi, block in enumerate(content):
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    filtered.append(block)
                    continue
                if (mi, bi) in consumed_locations:
                    continue
                tu_id = str(block.get("tool_use_id") or "")
                if tu_id not in seen_tool_use_ids:
                    dropped_orphan += 1
                    continue
                # Belongs to a preceding tool_use AND wasn't pulled up
                # by the splice — keep it. Rare; defensive.
                filtered.append(block)
            if dropped_orphan:
                logger.warning(
                    "Dropped orphan tool_result block(s)",
                    count=dropped_orphan,
                    message_index=mi,
                )
            if filtered:
                if filtered == content:
                    rebuilt.append(msg)
                else:
                    new_msg = dict(msg)
                    new_msg["content"] = filtered
                    rebuilt.append(new_msg)
            # else: user message ended up empty (all tool_results were
            # consumed by splices or dropped as orphans) — skip it.
        else:
            rebuilt.append(msg)

    return rebuilt
