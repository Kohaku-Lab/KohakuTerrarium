"""Replay provider-side context recovery (emergency drop, content repair) on a host."""

import json
from typing import Any

from kohakuterrarium.core.conversation import Conversation
from kohakuterrarium.llm.context_repair import ContextRepair
from kohakuterrarium.llm.recovery import drop_last_tool_round
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def sync_emergency_drop_conversation(
    agent: Any, messages: list[dict[str, Any]]
) -> None:
    """Synchronize an agent controller after provider-side emergency drop."""
    if not hasattr(agent, "controller"):
        return
    try:
        serialized = [_message_to_conversation_json(msg) for msg in messages]
        _, retained = drop_last_tool_round(
            agent.controller.conversation.snapshot_messages()
        )
        identities = [msg.pop("metadata", {}) for msg in retained]
        if retained == messages:
            for msg, metadata in zip(serialized, identities):
                msg["metadata"] = metadata
        data = {
            "messages": serialized,
            "metadata": _metadata_for_messages(agent, messages),
        }
        agent.controller.conversation.adopt_contents(
            Conversation.from_json(json.dumps(data))
        )
    except Exception as exc:
        logger.debug(
            "Failed to sync emergency-drop conversation",
            error=str(exc),
            exc_info=True,
        )


def attach_recovery_hooks(agent: Any, llm: Any) -> None:
    """Register an agent's emergency-drop and content-repair sync on a provider."""
    drop_sync = getattr(agent, "_on_provider_emergency_drop", None)
    if drop_sync is not None and hasattr(llm, "on_emergency_drop"):
        llm.on_emergency_drop(drop_sync)
    if hasattr(llm, "on_context_repair"):
        llm.on_context_repair(lambda repair: repair_controller(agent, repair))


def repair_controller(agent: Any, repair: ContextRepair) -> int:
    """Replay a content repair on the agent controller's conversation."""
    controller = getattr(agent, "controller", None)
    if controller is None:
        return 0
    return apply_context_repair(controller.conversation, repair)


def apply_context_repair(conversation: Conversation, repair: ContextRepair) -> int:
    """Replay a provider content repair on a conversation, keeping message identity.

    Returns how many items the repair changed; ``0`` leaves the conversation as is.
    """
    try:
        snapshot = conversation.snapshot_messages(preserve_pending_tail=True)
        changed, repaired = repair.apply(snapshot)
        if not changed:
            return 0
        messages = []
        for msg in repaired:
            msg = dict(msg)
            metadata = msg.pop("metadata", {}) or {}
            serialized = _message_to_conversation_json(msg)
            serialized["metadata"] = metadata
            messages.append(serialized)
        current = conversation._metadata
        data = {
            "messages": messages,
            "metadata": {
                "created_at": current.created_at.isoformat(),
                "updated_at": current.updated_at.isoformat(),
                "message_count": len(messages),
                "total_chars": sum(len(str(m.get("content", ""))) for m in repaired),
            },
        }
        conversation.adopt_contents(Conversation.from_json(json.dumps(data)))
        return changed
    except Exception as exc:
        logger.warning(
            "Failed to replay context repair on the conversation",
            kind=repair.kind,
            error=str(exc),
            exc_info=True,
        )
        return 0


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
