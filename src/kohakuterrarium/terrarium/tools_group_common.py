"""Shared helpers for the group_* tool modules.

Shared helpers provide caller resolution, JSON-shaped results, and channel-history
serialization for the group tools.
"""

import json
from typing import Any

from kohakuterrarium.modules.tool.base import ToolContext, ToolResult
from kohakuterrarium.terrarium.group_tool_context import (
    GroupContext,
    GroupToolError,
    resolve_group_context,
    resolve_group_target,
)


def err(message: str) -> ToolResult:
    return ToolResult(error=message)


def ok(payload: dict[str, Any]) -> ToolResult:
    return ToolResult(output=json.dumps(payload, default=str), exit_code=0)


def resolve_or_error(
    ctx: ToolContext | None,
    *,
    require_privileged: bool = True,
) -> tuple[GroupContext | None, ToolResult | None]:
    """Resolve the caller's :class:`GroupContext` or return an error
    :class:`ToolResult`. ``require_privileged=False`` lets ``send_channel``
    / ``group_send`` accept non-privileged engine creatures."""
    try:
        return (
            resolve_group_context(ctx, require_privileged=require_privileged),
            None,
        )
    except GroupToolError as exc:
        return None, err(str(exc))


def resolve_target_or_error(
    gctx: GroupContext, identifier: str
) -> tuple[Any | None, ToolResult | None]:
    """Resolve one caller-graph target while preserving safe resolver errors."""
    try:
        return resolve_group_target(gctx, identifier), None
    except GroupToolError as exc:
        return None, err(str(exc))


def serialize_channel_history(channel: Any, limit: int) -> list[dict[str, Any]]:
    history = list(getattr(channel, "history", []) or [])[-limit:]
    out: list[dict[str, Any]] = []
    for msg in history:
        out.append(
            {
                "message_id": getattr(msg, "message_id", ""),
                "sender": getattr(msg, "sender", ""),
                "content": getattr(msg, "content", ""),
                "reply_to": getattr(msg, "reply_to", None),
            }
        )
    return out
