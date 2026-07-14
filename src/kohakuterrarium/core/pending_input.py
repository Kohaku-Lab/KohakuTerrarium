"""Stable per-item ids for queued mid-turn inputs + edit/cancel ops.

A message typed while the agent is mid-turn is buffered in
``Agent._pending_mid_turn_inputs`` and drained into the running turn
later (see :mod:`core.agent_mid_turn`). UXI-08(a) lets a shell correct
or drop such a message *before it is sent*: each buffered event carries
a stable id in ``event.context[PENDING_ID_KEY]``, and the edit/cancel
ops act on the live buffer keyed by that id.

The ops are pure functions over the buffer list so they win iff they
run before the drain's ``list(buffer); buffer.clear()`` claims the
event — after the claim the event is gone from the buffer and the op is
a plain "already sent" no-op.
"""

from typing import Any
from uuid import uuid4

from kohakuterrarium.core.events import TriggerEvent

PENDING_ID_KEY = "pending_id"


def new_pending_id() -> str:
    """Mint a fresh queued-input id."""
    return f"pending_{uuid4().hex[:12]}"


def stamp_pending_id(event: TriggerEvent) -> str:
    """Ensure ``event`` carries a pending id; return it.

    Idempotent — a caller-supplied id already in ``event.context``
    survives so a shell can mint the id up front and target the message
    by it as soon as the queued ack arrives.
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
    """Replace the content of the buffered event with ``pending_id``.

    Returns ``True`` when the message was still queued (edit committed),
    ``False`` when it was already claimed by the drain / never existed.
    """
    for event in buffer:
        if pending_id_of(event) == pending_id:
            event.content = content
            return True
    return False


def cancel_pending(buffer: list[TriggerEvent], pending_id: str) -> bool:
    """Drop the buffered event with ``pending_id`` from the buffer.

    Returns ``True`` when it was still queued (cancel committed),
    ``False`` when it was already claimed / never existed.
    """
    for idx, event in enumerate(buffer):
        if pending_id_of(event) == pending_id:
            del buffer[idx]
            return True
    return False
