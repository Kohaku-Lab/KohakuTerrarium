"""
Session-scoped key-value working memory.

Different from memory (file-based, cross-session, agent-managed).
Scratchpad is session-scoped, framework-managed, structured, and cheap.
"""

from typing import Callable

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def is_reserved_scratchpad_key(key: str) -> bool:
    """Return True for framework-private scratchpad keys."""
    return key.startswith("__") and key.endswith("__")


class Scratchpad:
    """
    Session-scoped key-value working memory.

    Different from memory (file-based, cross-session, agent-managed).
    Scratchpad is:
    - Session-scoped (cleared on restart)
    - Framework-managed (auto-injected into context)
    - Structured (key-value, not free-form)
    - Cheap (no LLM needed to read/write)
    """

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        # Wave B additive ``scratchpad_write`` event: fire-and-forget
        # observer. Signature: ``cb(key, action, size_bytes)``.
        # ``action`` is ``"set"`` / ``"delete"``. Staying ``None`` is
        # the zero-overhead default.
        self._on_write: Callable[[str, str, int], None] | None = None
        logger.debug("Scratchpad initialized")

    def set_write_observer(self, cb: Callable[[str, str, int], None] | None) -> None:
        """Attach a fire-and-forget observer. See ``_on_write`` above."""
        self._on_write = cb

    def _emit(self, key: str, action: str, size_bytes: int) -> None:
        """Fire the write observer if wired; swallow errors defensively."""
        cb = self._on_write
        if cb is None:
            return
        try:
            cb(key, action, size_bytes)
        except Exception as e:  # pragma: no cover — defensive
            logger.debug("scratchpad observer failed", error=str(e), exc_info=True)

    def set(self, key: str, value: str) -> None:
        """Set a key-value pair."""
        self._data[key] = value
        logger.debug("Scratchpad set", key=key)
        self._emit(
            key, "set", len(value.encode("utf-8")) if isinstance(value, str) else 0
        )

    def get(self, key: str) -> str | None:
        """Get value by key. Returns None if not found."""
        return self._data.get(key)

    def delete(self, key: str) -> bool:
        """Delete a key. Returns True if existed."""
        if key in self._data:
            del self._data[key]
            logger.debug("Scratchpad deleted", key=key)
            self._emit(key, "delete", 0)
            return True
        return False

    def list_keys(self, include_reserved: bool = False) -> list[str]:
        """List keys, hiding framework-reserved entries by default."""
        if include_reserved:
            return list(self._data.keys())
        return [k for k in self._data if not is_reserved_scratchpad_key(k)]

    def clear(self) -> None:
        """Clear all data."""
        self._data.clear()
        logger.debug("Scratchpad cleared")

    def to_dict(self) -> dict[str, str]:
        """Get all data as a dict copy."""
        return {
            k: v for k, v in self._data.items() if not is_reserved_scratchpad_key(k)
        }

    def to_prompt_section(self) -> str:
        """
        Format scratchpad as a prompt section for injection into system prompt.

        Returns empty string if scratchpad is empty.
        Returns markdown with ## Working Memory header if has data.
        """
        visible = self.to_dict()
        if not visible:
            return ""

        lines = ["## Working Memory\n"]
        for key, value in visible.items():
            # For multi-line values, indent them
            if "\n" in value:
                lines.append(f"### {key}\n{value}\n")
            else:
                lines.append(f"- **{key}**: {value}")

        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def __repr__(self) -> str:
        return f"Scratchpad(keys={self.list_keys()})"


# get_scratchpad() has moved to kohakuterrarium.core.session to avoid
# a circular import (session.py imports Scratchpad from this module).
# Callers should import from kohakuterrarium.core.session directly.
