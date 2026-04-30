"""
OutputEvent - the universal output-side event.

OutputEvent is the output-side counterpart of TriggerEvent
(``core/events.py``). Where TriggerEvent represents anything that flows
*into* the controller (user input, timers, tool completion, channel
messages), OutputEvent represents anything that flows *out* of the
controller toward renderers, observers, persistence, and remote
streams.

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

For activity events, ``content`` carries the existing detail string
and ``payload`` carries the existing metadata dict. For ``text``
events, ``content`` carries the chunk. For ``assistant_image``,
``payload`` carries the image fields (url, detail, source_type,
source_name, revised_prompt). For ``resume_batch``, ``payload``
carries ``{"events": [...]}``.

Future Phase B fields (``surface``, ``interactive``,
``correlation_id``, ``timeout_s``, ``update_target``) are deliberately
omitted here. They will land alongside the interactive bus work.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from kohakuterrarium.llm.message import ContentPart


@dataclass
class OutputEvent:
    """Universal output-side event. Counterpart to TriggerEvent."""

    type: str
    content: str | list[ContentPart] = ""
    payload: dict[str, Any] = field(default_factory=dict)
    id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
