"""Agent helpers for budget construction and provider recovery sync."""

import json
from typing import Any

from kohakuterrarium.core.budget import BudgetAxis, BudgetSet
from kohakuterrarium.core.conversation import Conversation
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def build_budget_set_from_config(config: Any) -> BudgetSet | None:
    """Build a multi-axis BudgetSet from AgentConfig-like fields."""
    turn = _axis_from_tuple("turn", getattr(config, "turn_budget", None))
    walltime = _axis_from_tuple("walltime", getattr(config, "walltime_budget", None))
    tool_call = _axis_from_tuple("tool_call", getattr(config, "tool_call_budget", None))
    if turn is None and walltime is None and tool_call is None:
        return None
    return BudgetSet(turn=turn, walltime=walltime, tool_call=tool_call)


def sync_emergency_drop_conversation(
    agent: Any, messages: list[dict[str, Any]]
) -> None:
    """Synchronize an agent controller after provider-side emergency drop."""
    if not hasattr(agent, "controller"):
        return
    try:
        data = {
            "messages": [_message_to_conversation_json(msg) for msg in messages],
            "metadata": _metadata_for_messages(agent, messages),
        }
        agent.controller.conversation = Conversation.from_json(json.dumps(data))
    except Exception as exc:
        logger.debug(
            "Failed to sync emergency-drop conversation",
            error=str(exc),
            exc_info=True,
        )


def _axis_from_tuple(name: str, value: tuple[Any, Any] | None) -> BudgetAxis | None:
    if value is None:
        return None
    soft, hard = value
    return BudgetAxis(name=name, soft=float(soft), hard=float(hard))


def _message_to_conversation_json(msg: dict[str, Any]) -> dict[str, Any]:
    known = {"role", "content", "name", "tool_call_id", "tool_calls"}
    return {
        "role": msg.get("role"),
        "content": msg.get("content", ""),
        "name": msg.get("name"),
        "tool_call_id": msg.get("tool_call_id"),
        "tool_calls": msg.get("tool_calls"),
        "extra_fields": {k: v for k, v in msg.items() if k not in known},
        "metadata": {},
    }


def _metadata_for_messages(
    agent: Any, messages: list[dict[str, Any]]
) -> dict[str, Any]:
    current = agent.controller.conversation._metadata
    return {
        "created_at": current.created_at.isoformat(),
        "updated_at": current.updated_at.isoformat(),
        "message_count": len(messages),
        "total_chars": sum(len(str(m.get("content", ""))) for m in messages),
    }
