"""
Conversation management for KohakuTerrarium.

Handles message history, context length tracking, and serialization.
Supports multimodal messages (text + images).
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from kohakuterrarium.llm.message import (
    ContentPart,
    ImagePart,
    Message,
    MessageContent,
    MessageList,
    Role,
    TextPart,
    create_message,
    messages_to_dicts,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _get_content_text_length(content: MessageContent) -> int:
    """Get text length of message content (text, multimodal, or None)."""
    if content is None:
        return 0
    if isinstance(content, str):
        return len(content)
    return sum(len(part.text) for part in content if isinstance(part, TextPart))


def _is_empty_content(content: Any) -> bool:
    """Return True if a message's ``content`` carries no user-visible text.

    Used by the orphan tool-call sanitiser to decide whether an assistant
    message whose ``tool_calls`` were all dropped can be removed wholesale.
    Treats ``None``, the empty string (after strip), and an empty list as
    empty. A list with any non-trivial part (text with content, image,
    file) counts as non-empty — the assistant still has something to say.
    """
    if content is None:
        return True
    if isinstance(content, str):
        return not content.strip()
    if isinstance(content, list):
        for part in content:
            if isinstance(part, TextPart):
                if part.text and part.text.strip():
                    return False
            elif isinstance(part, dict):
                # Post-serialisation dicts — treat anything non-text or
                # non-empty text as meaningful payload.
                if part.get("type") == "text":
                    text = part.get("text", "")
                    if text and text.strip():
                        return False
                else:
                    return False
            else:
                # Any non-TextPart object (ImagePart, FilePart, …) is
                # meaningful — keep the message.
                return False
        return True
    return False


@dataclass
class ConversationConfig:
    """
    Configuration for conversation management.

    Attributes:
        max_messages: Maximum number of messages to keep (0 = unlimited)
        keep_system: Always keep system message(s) even when truncating
        sanitize_orphan_tool_calls: Strip mismatched tool_call / tool-result
            pairs from the wire payload. Most OpenAI-compatible providers
            return HTTP 400 when either side of a pair is missing; compaction
            occasionally produces this. Pure, opt-out, on by default.
    """

    max_messages: int = 0
    keep_system: bool = True
    sanitize_orphan_tool_calls: bool = True


@dataclass
class ConversationMetadata:
    """Metadata about a conversation."""

    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    message_count: int = 0
    total_chars: int = 0


class Conversation:
    """
    Manages a conversation with message history and context tracking.

    Supports:
    - Adding messages (system, user, assistant, tool)
    - Context length tracking
    - Serialization to/from JSON
    - Message truncation when context grows too large

    Usage:
        conv = Conversation()
        conv.append("system", "You are a helpful assistant.")
        conv.append("user", "Hello!")
        conv.append("assistant", "Hi! How can I help?")

        # Get messages for API call
        messages = conv.to_messages()

        # Check context length
        print(f"Context: {conv.get_context_length()} chars")
    """

    def __init__(self, config: ConversationConfig | None = None):
        """
        Initialize a conversation.

        Args:
            config: Optional configuration for context management
        """
        self.config = config or ConversationConfig()
        self._messages: MessageList = []
        self._metadata = ConversationMetadata()

    def append(
        self,
        role: Role | str,
        content: MessageContent,
        **kwargs: Any,
    ) -> Message:
        """
        Append a message to the conversation.

        Args:
            role: Message role (system, user, assistant, tool)
            content: Message content (str or list[ContentPart] for multimodal)
            **kwargs: Additional message parameters (name, tool_call_id, etc.)

        Returns:
            The created Message object
        """
        msg = create_message(role, content, **kwargs)  # type: ignore
        self._messages.append(msg)

        # Update metadata
        content_length = _get_content_text_length(content)
        self._metadata.message_count += 1
        self._metadata.total_chars += content_length
        self._metadata.updated_at = datetime.now()

        # Check for multimodal content
        is_multimodal = isinstance(content, list)
        image_count = 0
        if is_multimodal:
            image_count = sum(1 for p in content if isinstance(p, ImagePart))

        logger.debug(
            "Message appended",
            role=role,
            content_length=content_length,
            total_messages=len(self._messages),
            multimodal=is_multimodal,
            images=image_count if image_count else None,
        )

        # Check if truncation needed
        self._maybe_truncate()

        return msg

    def append_message(self, message: Message) -> None:
        """Append an existing Message object."""
        self._messages.append(message)
        self._metadata.message_count += 1
        self._metadata.total_chars += _get_content_text_length(message.content)
        self._metadata.updated_at = datetime.now()
        self._maybe_truncate()

    def _maybe_truncate(self) -> None:
        """Truncate messages if message count limit exceeded."""
        if self.config.max_messages <= 0:
            return

        # Keep system messages if configured
        system_messages: list[Message] = []
        other_messages: list[Message] = []

        if self.config.keep_system:
            for msg in self._messages:
                if msg.role == "system":
                    system_messages.append(msg)
                else:
                    other_messages.append(msg)
        else:
            other_messages = list(self._messages)

        # Truncate by message count
        max_other = self.config.max_messages - len(system_messages)
        if len(other_messages) > max_other:
            other_messages = other_messages[-max_other:]
            logger.debug("Truncated by message count", kept=len(other_messages))

        # Rebuild messages list
        self._messages = system_messages + other_messages
        self._metadata.total_chars = sum(
            _get_content_text_length(m.content) for m in self._messages
        )

    def to_messages(self) -> list[dict[str, Any]]:
        """
        Convert conversation to OpenAI API message format.

        Applies the orphan tool-call sanitiser when
        ``config.sanitize_orphan_tool_calls`` is True so the payload
        sent to the provider never violates the OpenAI contract of
        ``assistant.tool_calls`` pairing with matching ``role=tool``
        messages. See :meth:`sanitize_orphan_tool_pairs`.

        Returns:
            List of message dicts suitable for API calls
        """
        messages = messages_to_dicts(self._messages)
        if self.config.sanitize_orphan_tool_calls:
            messages = self.sanitize_orphan_tool_pairs(messages)
        return messages

    @staticmethod
    def sanitize_orphan_tool_pairs(
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Strip unmatched tool_call / tool-result pairs.

        Pure function: takes the provider payload, returns a new list
        with orphan fragments removed. Idempotent — running twice
        yields identical output.

        Rules (matches the OpenAI Chat Completions contract):

        1. Every id in an ``assistant.tool_calls`` list MUST have a
           matching ``role=tool`` message with the same ``tool_call_id``
           somewhere between that assistant message and the next
           ``assistant`` / ``user`` message. Unmatched ids are dropped
           from ``tool_calls``. If an assistant message ends up with
           empty ``tool_calls`` AND empty ``content``, the whole
           message is dropped.
        2. Every ``role=tool`` message MUST reference a ``tool_call_id``
           announced by some *preceding* assistant message (after the
           same sanitisation pass). Orphan tool messages are dropped.

        Produces WARNING-level log entries for every drop so operators
        can see when compaction left the conversation inconsistent.
        """
        if not messages:
            return messages

        # --- Pass 1 + 2: scan for orphan assistant tool_calls. ---
        # For each assistant with tool_calls, walk forward until we hit
        # the next assistant/user and collect the tool_call_ids that
        # actually showed up. Drop the missing ones.
        cleaned: list[dict[str, Any]] = []
        n = len(messages)
        for idx, msg in enumerate(messages):
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                expected_ids = [
                    tc.get("id") for tc in msg["tool_calls"] if tc.get("id") is not None
                ]
                # Collect responder ids up to the next assistant/user.
                observed_ids: set[str] = set()
                for j in range(idx + 1, n):
                    nxt = messages[j]
                    if nxt.get("role") in ("assistant", "user"):
                        break
                    if nxt.get("role") == "tool":
                        tc_id = nxt.get("tool_call_id")
                        if tc_id:
                            observed_ids.add(tc_id)

                kept_calls = [
                    tc for tc in msg["tool_calls"] if tc.get("id") in observed_ids
                ]
                dropped = len(msg["tool_calls"]) - len(kept_calls)
                if dropped:
                    missing = [
                        tc.get("id")
                        for tc in msg["tool_calls"]
                        if tc.get("id") not in observed_ids
                    ]
                    logger.warning(
                        f"dropped {dropped} orphan tool_call(s) on assistant message #{idx}",
                        dropped=dropped,
                        message_index=idx,
                        missing_ids=missing,
                        expected_ids=expected_ids,
                    )
                new_msg = dict(msg)
                if kept_calls:
                    new_msg["tool_calls"] = kept_calls
                else:
                    # All tool_calls orphaned — remove the key so the
                    # provider doesn't see an empty list.
                    new_msg.pop("tool_calls", None)

                # If the assistant now has NO meaningful payload, drop
                # the whole message. Content considered "empty" if it's
                # None, empty string, or empty list.
                if not kept_calls and _is_empty_content(new_msg.get("content")):
                    logger.warning(
                        f"dropped assistant message #{idx} — no content + all tool_calls orphaned",
                        message_index=idx,
                    )
                    continue
                cleaned.append(new_msg)
            else:
                cleaned.append(msg)

        # --- Pass 3: drop orphan tool-result messages. ---
        # A tool message is valid only if some preceding assistant in
        # the (already sanitised) list advertises its tool_call_id.
        announced_ids: set[str] = set()
        final: list[dict[str, Any]] = []
        for idx, msg in enumerate(cleaned):
            role = msg.get("role")
            if role == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    tc_id = tc.get("id")
                    if tc_id:
                        announced_ids.add(tc_id)
                final.append(msg)
            elif role == "tool":
                tc_id = msg.get("tool_call_id")
                if tc_id and tc_id in announced_ids:
                    final.append(msg)
                else:
                    logger.warning(
                        f"dropped orphan tool-result message #{idx} with id={tc_id}",
                        message_index=idx,
                        tool_call_id=tc_id,
                    )
            else:
                final.append(msg)

        return final

    def get_messages(self) -> MessageList:
        """Get the raw Message objects."""
        return list(self._messages)

    def get_context_length(self) -> int:
        """
        Get current context length in characters.

        Note: This is text characters only (excludes image data).
        For token estimation, divide by ~4 for English text.
        Images consume additional tokens (~85 for low detail, ~765+ for high).
        """
        return sum(_get_content_text_length(msg.content) for msg in self._messages)

    def get_image_count(self) -> int:
        """Get total number of images in conversation."""
        count = 0
        for msg in self._messages:
            if isinstance(msg.content, list):
                count += sum(1 for p in msg.content if isinstance(p, ImagePart))
        return count

    def get_system_message(self) -> Message | None:
        """Get the first system message in the conversation, if any."""
        for msg in self._messages:
            if msg.role == "system":
                return msg
        return None

    def get_last_message(self) -> Message | None:
        """Get the last message in the conversation."""
        return self._messages[-1] if self._messages else None

    def get_last_assistant_message(self) -> Message | None:
        """Get the last assistant message."""
        for msg in reversed(self._messages):
            if msg.role == "assistant":
                return msg
        return None

    def truncate_from(self, index: int) -> list[Message]:
        """Remove messages from ``index`` onward.

        Returns the removed messages. If ``index`` is 0 or 1 (system only),
        nothing is removed. Used by edit/regenerate/rewind features.
        """
        if index < 0 or index >= len(self._messages):
            return []
        removed = self._messages[index:]
        self._messages = self._messages[:index]
        self._metadata.message_count = len(self._messages)
        self._metadata.total_chars = sum(
            _get_content_text_length(m.content) for m in self._messages
        )
        return removed

    def find_last_user_index(self) -> int:
        """Return the index of the last user message, or -1 if none."""
        for i in range(len(self._messages) - 1, -1, -1):
            if self._messages[i].role == "user":
                return i
        return -1

    def clear(self, keep_system: bool = True) -> None:
        """
        Clear the conversation history.

        Args:
            keep_system: If True, keep system messages
        """
        if keep_system:
            self._messages = [m for m in self._messages if m.role == "system"]
        else:
            self._messages = []

        self._metadata.message_count = len(self._messages)
        self._metadata.total_chars = sum(
            _get_content_text_length(m.content) for m in self._messages
        )
        logger.debug("Conversation cleared", kept_messages=len(self._messages))

    def __len__(self) -> int:
        """Return number of messages."""
        return len(self._messages)

    def __bool__(self) -> bool:
        """Return True if conversation has messages."""
        return len(self._messages) > 0

    # Serialization

    def _serialize_content(self, content: MessageContent) -> Any:
        """Serialize message content to JSON-compatible format.

        Emits the **nested** OpenAI-style ``image_url`` shape to match
        ``ImagePart.to_dict()`` and the Chat Completions wire format:

        ``{"type":"image_url","image_url":{"url":..,"detail":..},"meta":{..}}``

        The legacy flat shape (``{url, detail, source_type, source_name}``
        at the top level) remains readable via ``_deserialize_content``.
        """
        if isinstance(content, str):
            return content

        parts = []
        for part in content:
            if isinstance(part, TextPart):
                parts.append({"type": "text", "text": part.text})
            elif isinstance(part, ImagePart):
                parts.append(part.to_dict())
        return parts

    def _deserialize_content(self, content: Any) -> MessageContent:
        """Deserialize message content from JSON.

        Accepts both the current nested shape and the legacy flat shape
        so sessions written before the normalization continue to load.
        """
        if isinstance(content, str):
            return content

        parts: list[ContentPart] = []
        for item in content:
            kind = item.get("type")
            if kind == "text":
                parts.append(TextPart(text=item.get("text", "")))
            elif kind == "image_url":
                # Nested (current) vs flat (legacy) shape.
                if "image_url" in item and isinstance(item["image_url"], dict):
                    img = item["image_url"]
                    url = img.get("url", "")
                    detail = img.get("detail", "low")
                    meta = item.get("meta") or {}
                    source_type = meta.get("source_type")
                    source_name = meta.get("source_name")
                else:
                    url = item.get("url", "")
                    detail = item.get("detail", "low")
                    source_type = item.get("source_type")
                    source_name = item.get("source_name")
                parts.append(
                    ImagePart(
                        url=url,
                        detail=detail,
                        source_type=source_type,
                        source_name=source_name,
                    )
                )
        return parts

    def to_json(self) -> str:
        """Serialize conversation to JSON string."""
        data = {
            "messages": [
                {
                    "role": msg.role,
                    "content": self._serialize_content(msg.content),
                    "name": msg.name,
                    "tool_call_id": msg.tool_call_id,
                    "metadata": msg.metadata,
                }
                for msg in self._messages
            ],
            "metadata": {
                "created_at": self._metadata.created_at.isoformat(),
                "updated_at": self._metadata.updated_at.isoformat(),
                "message_count": self._metadata.message_count,
                "total_chars": self._metadata.total_chars,
            },
        }
        return json.dumps(data, ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> "Conversation":
        """Deserialize conversation from JSON string."""
        data = json.loads(json_str)
        conv = cls()

        for msg_data in data.get("messages", []):
            content = conv._deserialize_content(msg_data["content"])
            msg = create_message(
                role=msg_data["role"],
                content=content,
                name=msg_data.get("name"),
                tool_call_id=msg_data.get("tool_call_id"),
            )
            msg.metadata = msg_data.get("metadata", {})
            conv._messages.append(msg)

        if "metadata" in data:
            meta = data["metadata"]
            conv._metadata = ConversationMetadata(
                created_at=datetime.fromisoformat(meta["created_at"]),
                updated_at=datetime.fromisoformat(meta["updated_at"]),
                message_count=meta["message_count"],
                total_chars=meta["total_chars"],
            )

        return conv

    def __repr__(self) -> str:
        return (
            f"Conversation(messages={len(self._messages)}, "
            f"context_chars={self.get_context_length()})"
        )
