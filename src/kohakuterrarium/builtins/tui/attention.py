"""Optional attention capability adapters for TUI hosts."""

from __future__ import annotations

from typing import Any


def _call(tui: Any, name: str, *args: Any) -> None:
    handler = getattr(tui, name, None)
    if callable(handler):
        handler(*args)


def processing_start(tui: Any, target: str) -> None:
    _call(tui, "attention_processing_start", target)


def processing_end(tui: Any, target: str) -> None:
    _call(tui, "attention_processing_end", target)


def processing_cancel(tui: Any, target: str) -> None:
    _call(tui, "attention_processing_cancel", target)


def is_pending(tui: Any, event_id: str | None, target: str) -> bool:
    handler = getattr(tui, "attention_has_pending", None)
    return bool(callable(handler) and handler(event_id, target))


def add_pending(tui: Any, event_id: str | None, target: str) -> None:
    _call(tui, "attention_pending", event_id, target)


def clear(tui: Any, event_id: str | None, target: str) -> None:
    _call(tui, "attention_clear", event_id, target)
