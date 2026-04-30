"""
OutputEvent - the universal output-side event.

OutputEvent is the output-side counterpart of TriggerEvent
(``core/events.py``). Where TriggerEvent represents anything that flows
*into* the controller (user input, timers, tool completion, channel
messages), OutputEvent represents anything that flows *out* of the
controller toward renderers, observers, persistence, and remote
streams.

Phase A introduced the typed envelope and the canonical event types
that mirrored the existing ``OutputModule`` hooks. Phase B extends the
envelope with fields needed for interactive events
(``surface``, ``interactive``, ``timeout_s``) and live updates
(``update_target``), plus a sibling :class:`UIReply` type for the
reply path.

Phase A scope (transformation only):

The set of valid ``OutputEvent.type`` values is exactly the set of
hooks the framework already exposes via ``OutputModule``:

- ``"text"`` — streamed text chunk (mirrors ``write_stream``)
- ``"processing_start"`` / ``"processing_end"`` — controller lifecycle
- ``"user_input"`` — echo of inbound user input
- ``"assistant_image"`` — structured image part
- ``"resume_batch"`` — wraps the historical events list passed to
  ``on_resume`` during session resume
- Any of the existing 30+ ``activity_type`` strings used by today's
  ``on_activity`` dispatch (``tool_start``, ``tool_done``,
  ``subagent_start``, ``compact_start``, ``trigger_fired`` …)

Phase B adds:

- ``"ask_text"``, ``"confirm"``, ``"selection"`` — interactive kinds.
- ``"progress"``, ``"notification"``, ``"card"`` — display kinds (with
  ``card`` becoming interactive when its payload carries ``actions``).

For activity events, ``content`` carries the existing detail string
and ``payload`` carries the existing metadata dict. For ``text``
events, ``content`` carries the chunk. For ``assistant_image``,
``payload`` carries the image fields (url, detail, source_type,
source_name, revised_prompt). For ``resume_batch``, ``payload``
carries ``{"events": [...]}``. For Phase B kinds, see
``plans/ui-event/design-phase-b.md`` §4 for the per-type schemas.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from kohakuterrarium.llm.message import ContentPart

# Surface declarations — where a renderer should host the event.
# ``chat`` is mandatory; renderers without a given surface fall back
# to ``chat`` rather than dropping the event.
Surface = Literal["chat", "modal", "toast", "side-panel", "status"]

# Action ids that the bus reserves for protocol-level signals.
ACTION_TIMEOUT = "__timeout__"
ACTION_SUPERSEDED = "__superseded__"


@dataclass
class OutputEvent:
    """Universal output-side event. Counterpart to TriggerEvent."""

    type: str
    content: str | list[ContentPart] = ""
    payload: dict[str, Any] = field(default_factory=dict)
    id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)

    # ── Phase B additions ─────────────────────────────────────────
    surface: Surface = "chat"
    interactive: bool = False
    update_target: str | None = None
    timeout_s: float | None = None
    correlation_id: str | None = None


@dataclass
class UIReply:
    """Reply produced by a renderer in response to an interactive
    :class:`OutputEvent`.

    Both sides of the bus see the same shape:

    - The producer (``await router.emit_and_wait(...)``) receives a
      ``UIReply`` whose ``event_id`` matches the awaited event. On
      timeout, ``action_id`` is the literal ``"__timeout__"`` so
      callers always handle the same shape.
    - The renderer (CLI/TUI/Web) constructs a ``UIReply`` when the
      user activates a widget and submits it via
      ``router.submit_reply(...)``. If a different renderer already
      replied, the late submission gets a ``"__superseded__"``
      acknowledgement so the renderer can dim its widget.
    """

    event_id: str
    action_id: str
    values: dict[str, Any] = field(default_factory=dict)
    user: str | None = None
    timestamp: float = 0.0

    @property
    def is_timeout(self) -> bool:
        return self.action_id == ACTION_TIMEOUT

    @property
    def is_superseded(self) -> bool:
        return self.action_id == ACTION_SUPERSEDED
