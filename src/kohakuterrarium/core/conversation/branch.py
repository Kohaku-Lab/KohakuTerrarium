"""Build a retention-limited conversation candidate from replayed branch messages."""

from copy import deepcopy

from kohakuterrarium.core.conversation.conversation import Conversation


def build_branch_conversation(
    current: Conversation, messages: list[dict]
) -> Conversation:
    """Construct branch messages with the current system prompts and retention policy."""
    candidate = Conversation(current.config)
    candidate._messages.extend(
        message for message in current.get_messages() if message.role == "system"
    )
    for message in messages:
        role = message.get("role")
        if role == "system":
            continue
        extra = {
            key: deepcopy(message[key])
            for key in ("tool_calls", "tool_call_id", "name", "metadata")
            if message.get(key)
        }
        fields = {
            key: deepcopy(value)
            for key, value in message.items()
            if key
            not in {"role", "content", "tool_calls", "tool_call_id", "name", "metadata"}
        }
        if fields:
            extra["extra_fields"] = fields
        candidate.append(role, deepcopy(message.get("content", "")), **extra)
    candidate._metadata.created_at = current._metadata.created_at
    candidate._metadata.message_count = len(candidate)
    candidate._metadata.total_chars = candidate.get_context_length()
    return candidate
