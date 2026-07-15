"""Anthropic Messages API tool_use ↔ tool_result pairing enforcement.

Repair Anthropic tool-use/result ordering after history mutation.

Anthropic requires each tool use to pair with a result in the immediately
following user message, so missing results are synthesized and orphans dropped.
"""

from copy import deepcopy
from typing import Any

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Missing results are represented as recoverable interruptions that may be retried.
SYNTHETIC_TOOL_RESULT_TEXT = (
    "Tool call was interrupted or removed before producing a result. "
    "This may not mean any error — if you receive no new input from "
    "the user, you can retry the call."
)


def synthetic_tool_result_block(tool_use_id: str, tool_name: str) -> dict[str, Any]:
    """Build a retryable error result for an unmatched tool use."""
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
    """Splice matching results, synthesize missing results, and drop orphans."""
    if not messages:
        return messages

    # Index results first; duplicate ids use the latest result because only one is valid.
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
                # Keep valid results not already moved beside their tool use.
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
            # Empty user messages are invalid after all result blocks are removed.
        else:
            rebuilt.append(msg)

    return rebuilt
