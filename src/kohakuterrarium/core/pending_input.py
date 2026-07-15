"""Stable identities and edit or cancellation helpers for queued input.

Edits apply only while an event remains in the live queue; once the consumer
claims it, the operation safely reports that the message is no longer pending.
"""

from typing import Any
from uuid import uuid4

from kohakuterrarium.core.events import TriggerEvent

PENDING_ID_KEY = "pending_id"


def new_pending_id() -> str:
    """Return a fresh identifier for queued input."""
    return f"pending_{uuid4().hex[:12]}"


def stamp_pending_id(event: TriggerEvent) -> str:
    """Return the event's stable pending id, creating one when absent.

    Preserving caller-supplied ids lets frontends target a message immediately
    after acknowledging that it was queued.
    """
    if event.context is None:
        event.context = {}
    pending_id = event.context.get(PENDING_ID_KEY)
    if not pending_id:
        pending_id = new_pending_id()
        event.context[PENDING_ID_KEY] = pending_id
    return pending_id


def pending_id_of(event: TriggerEvent) -> str | None:
    """Read a buffered event's pending id, or ``None`` if unstamped."""
    ctx = getattr(event, "context", None)
    if not ctx:
        return None
    return ctx.get(PENDING_ID_KEY)


def edit_pending(buffer: list[TriggerEvent], pending_id: str, content: Any) -> bool:
    """Replace queued content and report whether the event was still pending."""
    for event in buffer:
        if pending_id_of(event) == pending_id:
            event.content = content
            return True
    return False


def cancel_pending(buffer: list[TriggerEvent], pending_id: str) -> bool:
    """Remove a queued event and report whether it was still pending."""
    for idx, event in enumerate(buffer):
        if pending_id_of(event) == pending_id:
            del buffer[idx]
            return True
    return False
