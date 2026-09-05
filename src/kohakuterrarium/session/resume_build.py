"""Rebuild persisted conversation messages into a runtime Conversation.

Kept separate from :mod:`kohakuterrarium.session.resume` so the resume
facade stays under the source-file size guard.
"""

from kohakuterrarium.core.conversation import Conversation
from kohakuterrarium.core.conversation_sanitize import normalize_tool_names


def build_conversation(messages: list[dict]) -> Conversation:
    """Build a conversation from persisted message dictionaries.

    Tool-call identifiers, names, metadata, and provider-owned extra fields
    (reasoning_content / reasoning_details / _kt_anthropic_content) are
    retained when present.
    """
    conv = Conversation()
    # Skip malformed persisted entries before repairing legacy tool labels.
    for msg in normalize_tool_names([m for m in messages if isinstance(m, dict)]):
        role = msg.get("role", "user")
        content = msg.get("content", "")
        kwargs = {}
        if msg.get("tool_calls"):
            kwargs["tool_calls"] = msg["tool_calls"]
        if msg.get("tool_call_id"):
            kwargs["tool_call_id"] = msg["tool_call_id"]
        if msg.get("name"):
            kwargs["name"] = msg["name"]
        if msg.get("metadata"):
            kwargs["metadata"] = msg["metadata"]
        extra_fields = {
            key: value
            for key, value in msg.items()
            if key
            not in {
                "role",
                "content",
                "tool_calls",
                "tool_call_id",
                "name",
                "metadata",
            }
        }
        if extra_fields:
            kwargs["extra_fields"] = extra_fields
        conv.append(role, content, **kwargs)
    # Preserve a trailing in-flight call while removing stale orphaned fragments.
    conv.prune_orphan_tool_pairs(preserve_pending_tail=True)
    return conv
