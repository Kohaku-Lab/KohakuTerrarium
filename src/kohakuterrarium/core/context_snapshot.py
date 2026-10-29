"""Immutable context captures for private children and background inference."""

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from kohakuterrarium.core.conversation import Conversation


@dataclass(frozen=True)
class ContextSnapshot:
    """Detached effective messages and their source provenance."""

    messages_json: str
    provenance_json: str = "{}"

    @property
    def messages(self) -> list[dict[str, Any]]:
        return json.loads(self.messages_json)

    @property
    def provenance(self) -> dict[str, Any]:
        return json.loads(self.provenance_json)

    def to_dict(self) -> dict[str, Any]:
        return {"messages": self.messages, "provenance": self.provenance}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContextSnapshot":
        return cls(json.dumps(data["messages"]), json.dumps(data.get("provenance", {})))

    def conversation(self) -> Conversation:
        """Rehydrate a new conversation without sharing message containers."""
        conversation = Conversation()
        for data in self.messages:
            known = {
                "role",
                "content",
                "name",
                "tool_call_id",
                "tool_calls",
                "metadata",
            }
            message = conversation.append(
                data["role"],
                data.get("content", ""),
                name=data.get("name"),
                tool_call_id=data.get("tool_call_id"),
                tool_calls=data.get("tool_calls"),
                extra_fields={k: v for k, v in data.items() if k not in known},
            )
            message.metadata = data.get("metadata") or {}
        return conversation


def capture_context(
    conversation: Conversation,
    *,
    provenance: dict | None = None,
    before: int | None = None,
    provider_safe: bool = True,
) -> ContextSnapshot:
    """Capture a provider-valid prefix, excluding unfinished native tool groups."""
    messages = deepcopy(
        [
            {**message.to_dict(), "metadata": message.metadata}
            for message in conversation.get_messages()
        ]
    )
    if before is not None:
        messages = messages[:before]
    if not provider_safe:
        return ContextSnapshot(
            json.dumps(messages, ensure_ascii=False),
            json.dumps(provenance or {}, ensure_ascii=False),
        )
    for index, message in enumerate(messages):
        calls = message.get("tool_calls") or []
        if not calls:
            continue
        results = set()
        for following in messages[index + 1 :]:
            if following.get("role") != "tool":
                break
            if (following.get("metadata") or {}).get("tool_result_phase") == "pending":
                break
            results.add(following.get("tool_call_id"))
        if any(call.get("id") not in results for call in calls):
            messages = messages[:index]
            break
    messages = Conversation.sanitize_orphan_tool_pairs(messages)
    return ContextSnapshot(
        json.dumps(messages, ensure_ascii=False),
        json.dumps(provenance or {}, ensure_ascii=False),
    )


def capture_agent_context(agent: Any) -> ContextSnapshot:
    """Capture a creature's effective branch, pins included, without changing it."""
    store = getattr(agent, "session_store", None)
    snapshot = capture_context(
        agent.controller.conversation,
        provenance={
            "agent": agent.config.name,
            "branch_path": list(getattr(agent, "_parent_branch_path", None) or []),
            "branch_view": dict(getattr(agent, "_branch_view", None) or {}),
            "turn_index": getattr(agent, "_turn_index", 0),
            "branch_id": getattr(agent, "_branch_id", 0),
            "event_watermark": store.max_event_id(agent.config.name) if store else None,
            "session_path": str(store.path) if store else None,
            "conversation_id": store.meta.get("conversation_id") if store else None,
            "model": getattr(agent.llm, "model", None),
        },
    )
    pins = getattr(agent.controller, "pinned", None)
    if not pins:
        return snapshot
    messages = snapshot.messages
    start = next(
        (i for i, m in enumerate(messages) if m.get("role") != "system"),
        len(messages),
    )
    pinned = [
        {**message.to_dict(), "metadata": dict(message.metadata)}
        for _, message in pins.pinned()
    ]
    messages[start:start] = pinned
    return ContextSnapshot(
        json.dumps(messages, ensure_ascii=False), snapshot.provenance_json
    )
