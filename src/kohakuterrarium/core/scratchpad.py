"""Session-scoped structured working memory managed by the runtime."""

from typing import Callable

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def is_reserved_scratchpad_key(key: str) -> bool:
    """Return True for framework-private scratchpad keys."""
    return key.startswith("__") and key.endswith("__")


class Scratchpad:
    """Store inexpensive key-value state for one runtime session."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        # Writes optionally notify observability without coupling storage to an
        # output implementation.
        self._on_write: Callable[[str, str, int], None] | None = None
        logger.debug("Scratchpad initialized")

    def set_write_observer(self, cb: Callable[[str, str, int], None] | None) -> None:
        """Attach an optional fire-and-forget write observer."""
        self._on_write = cb

    def _emit(self, key: str, action: str, size_bytes: int) -> None:
        """Notify the observer without allowing telemetry to break storage."""
        cb = self._on_write
        if cb is None:
            return
        try:
            cb(key, action, size_bytes)
        except Exception as e:  # pragma: no cover - observer must not fail writes
            logger.warning("scratchpad observer failed", error=str(e), exc_info=True)

    def set(self, key: str, value: str) -> None:
        """Set a key-value pair."""
        self._data[key] = value
        logger.debug("Scratchpad set", key=key)
        self._emit(
            key, "set", len(value.encode("utf-8")) if isinstance(value, str) else 0
        )

    def get(self, key: str) -> str | None:
        """Return a value by key, or ``None`` when absent."""
        return self._data.get(key)

    def delete(self, key: str) -> bool:
        """Delete a key and report whether it existed."""
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
        """Render visible scratchpad entries as a system-prompt section."""
        visible = self.to_dict()
        if not visible:
            return ""

        lines = ["## Working Memory\n"]
        for key, value in visible.items():
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
