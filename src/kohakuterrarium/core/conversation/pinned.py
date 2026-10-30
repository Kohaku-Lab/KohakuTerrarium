"""Pinned context: keyed messages placed right after the system prompt.

The controller holds the pins outside the conversation history, adds them
to every provider request, and persists them in session state under
``<agent>:pinned_context``.
"""

from typing import Any

from kohakuterrarium.llm.message import (
    ImagePart,
    Message,
    MessageContent,
    create_message,
    normalize_content_parts,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

PIN_ROLES = ("user", "system")


def state_key(agent_name: str) -> str:
    """Session-state key holding one agent's pinned messages."""
    return f"{agent_name}:pinned_context"


class PinnedContext:
    """Ordered, keyed messages placed after the leading system messages."""

    def __init__(self) -> None:
        self._pins: dict[str, Message] = {}

    def pin(self, key: str, role: str, content: MessageContent) -> Message:
        """Insert or replace the pin ``key``; a replaced pin keeps its position."""
        if not key:
            raise ValueError("pin key must be a non-empty string")
        if role not in PIN_ROLES:
            raise ValueError(f"pin role must be one of {PIN_ROLES}, got {role!r}")
        message = create_message(role, normalize_content_parts(content))  # type: ignore[arg-type]
        message.metadata["pinned"] = key
        self._pins[key] = message
        return message

    def unpin(self, key: str) -> bool:
        """Remove the pin ``key``; return whether it existed."""
        return self._pins.pop(key, None) is not None

    def pinned(self) -> list[tuple[str, Message]]:
        """Return ``(key, message)`` pairs in pin order."""
        return list(self._pins.items())

    def __len__(self) -> int:
        return len(self._pins)

    def __contains__(self, key: str) -> bool:
        return key in self._pins

    def apply(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return provider messages with the pins inserted after the system block."""
        if not self._pins:
            return messages
        start = _system_end(messages)
        pins = [message.to_dict() for message in self._pins.values()]
        return list(messages[:start]) + pins + list(messages[start:])

    def context_end(self, messages: list[dict[str, Any]]) -> int:
        """Index just past the system block and the pins in applied messages."""
        leading_system_pins = 0
        for message in self._pins.values():
            if message.role != "system":
                break
            leading_system_pins += 1
        start = _system_end(messages) - leading_system_pins
        return min(start + len(self._pins), len(messages))

    def text_length(self) -> int:
        """Characters of text across all pins."""
        return sum(len(message.get_text_content()) for message in self._pins.values())

    def image_count(self) -> int:
        """Number of image parts across all pins."""
        return sum(
            sum(1 for part in message.content if isinstance(part, ImagePart))
            for message in self._pins.values()
            if isinstance(message.content, list)
        )

    def to_state(self) -> list[dict[str, Any]]:
        """Serialize the pins for session state."""
        return [
            {"key": key, "message": message.to_dict()}
            for key, message in self._pins.items()
        ]

    def load_state(self, data: Any) -> None:
        """Replace the pins with a serialized list; malformed entries are skipped."""
        self._pins = {}
        for entry in data or []:
            try:
                key = entry["key"]
                saved = Message.from_dict(entry["message"])
                self.pin(key, saved.role, saved.content)
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("Skipped malformed pinned message", error=str(exc))


def _system_end(messages: list[dict[str, Any]]) -> int:
    for index, message in enumerate(messages):
        if message.get("role") != "system":
            return index
    return len(messages)


def _pinned_context(agent: Any) -> PinnedContext | None:
    controller = getattr(agent, "controller", None)
    return getattr(controller, "pinned", None) if controller is not None else None


def _persist(agent: Any, pins: PinnedContext) -> None:
    store = getattr(agent, "session_store", None)
    if store is None:
        return
    store.state[state_key(agent.config.name)] = pins.to_state()


def pin_message(agent: Any, key: str, role: str, content: MessageContent) -> None:
    """Pin a message on ``agent`` and persist it to the attached session."""
    pins = _pinned_context(agent)
    if pins is None:
        raise RuntimeError("agent has no controller to hold pinned context")
    pins.pin(key, role, content)
    _persist(agent, pins)


def unpin_message(agent: Any, key: str) -> bool:
    """Remove a pin from ``agent``; return whether it existed."""
    pins = _pinned_context(agent)
    if pins is None or not pins.unpin(key):
        return False
    _persist(agent, pins)
    return True


def sync_with_store(agent: Any, store: Any) -> None:
    """Merge saved pins from ``store`` with in-memory ones and persist the union.

    Saved pins load first; pins set before the store was attached replace
    saved ones with the same key and append the rest.
    """
    pins = _pinned_context(agent)
    if pins is None or store is None:
        return
    current = pins.pinned()
    try:
        saved = store.state.get(state_key(agent.config.name))
    except (KeyError, TypeError):
        saved = None
    pins.load_state(saved)
    for key, message in current:
        pins.pin(key, message.role, message.content)
    if saved is not None or current:
        store.state[state_key(agent.config.name)] = pins.to_state()


__all__ = [
    "PIN_ROLES",
    "PinnedContext",
    "pin_message",
    "state_key",
    "sync_with_store",
    "unpin_message",
]
