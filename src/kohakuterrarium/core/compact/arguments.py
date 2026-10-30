"""Shorten the arguments of old native tool calls after a compact splice.

Each tool decides through :meth:`BaseTool.compact_arguments`; a tool that
returns ``None`` (the default) keeps its arguments. Calls in the latest
tool-feedback round are left alone, like stale tool-result elision.
"""

import json
from typing import Any

from kohakuterrarium.core.conversation.elide import (
    _is_tool_feedback,
    elide_after_compact,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _parse_arguments(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _latest_round_call_ids(messages: list[Any]) -> set[str]:
    """Tool-call ids answered by the trailing run of tool-feedback messages."""
    ids: set[str] = set()
    tail = len(messages)
    while tail > 0 and _is_tool_feedback(messages[tail - 1]):
        call_id = getattr(messages[tail - 1], "tool_call_id", None)
        if call_id:
            ids.add(call_id)
        tail -= 1
    return ids


def compact_tool_arguments(conversation: Any, registry: Any) -> int:
    """Replace old tool-call arguments with what each tool keeps; return the count."""
    if conversation is None or registry is None:
        return 0
    messages = conversation.get_messages()
    keep = _latest_round_call_ids(messages)
    changed = 0
    for msg in messages:
        for call in getattr(msg, "tool_calls", None) or []:
            if call.get("id") in keep:
                continue
            fn = call.get("function") or {}
            tool = registry.get_tool(fn.get("name") or "")
            args = _parse_arguments(fn.get("arguments"))
            if tool is None or args is None:
                continue
            try:
                shorter = tool.compact_arguments(args)
            except Exception as e:
                logger.warning(
                    "compact_arguments failed", tool_name=fn.get("name"), error=str(e)
                )
                continue
            if shorter is None or shorter == args:
                continue
            raw = fn.get("arguments")
            fn["arguments"] = (
                shorter
                if isinstance(raw, dict)
                else json.dumps(shorter, ensure_ascii=False)
            )
            changed += 1
    if changed:
        conversation._metadata.total_chars = conversation.get_context_length()
        logger.debug("Old tool-call arguments compacted", count=changed)
    return changed


def after_compact(controller: Any, agent: Any) -> None:
    """Shrink the live zone a compact splice kept: stale results, then old arguments.

    Compact summarizes the compact zone and leaves recent turns untouched, so
    this is what makes the post-compact prompt drop below the threshold.
    """
    elide_after_compact(controller)
    conversation = getattr(controller, "conversation", None)
    compact_tool_arguments(conversation, getattr(agent, "registry", None))
