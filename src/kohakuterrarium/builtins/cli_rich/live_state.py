"""Per-creature live-region state.

The visible :class:`LiveRegion` widget renders from one
:class:`LiveRegionState` at a time — the one for the focused
creature. Swapping focus is just swapping which state the render
path reads from.

The state holds:

- ``text_buffer`` — accumulated streamed text since the last
  scrollback commit (the rolling preview the user sees).
- ``active_blocks`` — currently-in-flight tool / sub-agent blocks
  keyed by block id; the live region renders these as an accordion.
- ``footer`` — model/token-usage info shown in the bottom strip.
- ``recent_events`` — bounded ring buffer of ``(timestamp, event)``
  pairs the peek panel reads from (Phase G).
- ``unread_since_focus`` — count of new events for THIS creature
  since the user last focused it; cleared on focus (Phase H).
"""

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

_PEEK_BUFFER_MAX = 128
_TEXT_BUFFER_MAX_CHARS = 8000  # cap so the live region never bloats


@dataclass
class FooterState:
    """Footer metadata cached per creature (model / token usage)."""

    model_identifier: str = ""
    max_context: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class LiveRegionState:
    """Per-creature mutable state the live region renders from.

    All fields are owned by ``RichCLIApp`` and mutated only on the
    asyncio loop thread (event handlers and key bindings). Cross-
    thread access is not supported and not needed (every output
    sink is awaited from the same loop).
    """

    creature_id: str
    text_buffer: str = ""
    active_blocks: dict[str, Any] = field(default_factory=dict)
    footer: FooterState = field(default_factory=FooterState)
    recent_events: deque = field(default_factory=lambda: deque(maxlen=_PEEK_BUFFER_MAX))
    unread_since_focus: int = 0
    last_event_at: float = 0.0

    def append_text(self, text: str) -> None:
        if not text:
            return
        self.text_buffer = (self.text_buffer + text)[-_TEXT_BUFFER_MAX_CHARS:]

    def clear_text(self) -> None:
        self.text_buffer = ""
        self.active_blocks.clear()

    def record_event(self, event: Any, now: float | None = None) -> None:
        """Stamp the event in the peek ring buffer + bump activity counters."""
        ts = now if now is not None else time.time()
        self.recent_events.append((ts, event))
        self.last_event_at = ts
        self.unread_since_focus += 1

    def reset_unread(self) -> None:
        self.unread_since_focus = 0

    def recent_event_payloads(self, *, seconds: float = 30.0) -> list[Any]:
        """Events from the last ``seconds`` seconds, oldest first."""
        if not self.recent_events:
            return []
        cutoff = time.time() - seconds
        return [evt for ts, evt in self.recent_events if ts >= cutoff]


__all__ = ["FooterState", "LiveRegionState"]
