"""WS-input handling free functions for :mod:`studio.attach.io`.

Split out of ``io.py`` to keep it under the 600-line file cap: the
input-side helpers (payload normalization, targeted-frame routing, the
UXI-08a queued-message edit/cancel ops, and the fire-and-forget
``inject_input`` runner) form one cohesive cluster with no dependency
on the ``attach_io`` main loop.
"""

import asyncio
import time
from typing import Any

from kohakuterrarium.llm.message import (
    content_parts_to_dicts,
    normalize_content_parts,
)
from kohakuterrarium.studio.sessions.lifecycle import find_creature
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _normalize_input_content(data: dict[str, Any]) -> str | list[dict[str, Any]]:
    """Normalize incoming WS input payload."""
    content = data.get("content")
    if isinstance(content, list):
        parts = normalize_content_parts(content) or []
        return content_parts_to_dicts(parts)
    if isinstance(content, str):
        return content
    message = data.get("message", "")
    return message if isinstance(message, str) else ""


def _resolve_target(engine: Any, creature: Any, session_id: str, data: dict) -> Any:
    """Resolve the creature a targeted WS frame addresses.

    Defaults to the bound creature; honours an explicit ``target``
    display name so one WS can drive every sub-tab. Raises ``KeyError``
    when the named creature is not in this session (via
    ``find_creature``)."""
    target_name = (data.get("target") or "").strip()
    if not target_name or target_name == creature.name:
        return creature
    return find_creature(engine, creature.graph_id or session_id, target_name)


def _handle_pending_op(
    data: dict[str, Any],
    msg_type: str,
    engine: Any,
    creature: Any,
    session_id: str,
    queue: asyncio.Queue,
) -> None:
    """Apply an ``input_edit`` / ``input_cancel`` to a queued message.

    Targets the pending buffer by ``event_id`` (UXI-08a); the op wins
    only if the message is still queued (``status="already_sent"``
    otherwise). Acks back with the same event_id so the shell can clear
    its queued banner."""
    event_id = data.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        return
    try:
        target = _resolve_target(engine, creature, session_id, data)
    except KeyError:
        queue.put_nowait(
            {
                "type": "error",
                "source": (data.get("target") or creature.name),
                "content": f"Cannot route {msg_type}: target creature not found.",
                "ts": time.time(),
            }
        )
        return
    if msg_type == "input_edit":
        content = _normalize_input_content(data)
        committed = target.agent.edit_pending(event_id, content)
        ack_type = "input_edit_ack"
        status = "edited" if committed else "already_sent"
    else:
        committed = target.agent.cancel_pending(event_id)
        ack_type = "input_cancel_ack"
        status = "cancelled" if committed else "already_sent"
    queue.put_nowait(
        {
            "type": ack_type,
            "event_id": event_id,
            "status": status,
            "source": target.name,
            "ts": time.time(),
        }
    )


async def _process_input(
    agent: Any,
    content: str | list[dict[str, Any]],
    queue: asyncio.Queue,
    source_name: str,
    pending_id: str,
) -> None:
    """Run ``agent.inject_input`` in its own task so the WS receive
    loop can keep processing inbound frames (notably ``ui_reply``)
    while the agent is mid-turn.

    Errors and the post-turn ``idle`` notice are pushed via the same
    outbound queue that ``_forward_queue`` drains, so the caller
    doesn't need to share the websocket reference.

    ``idle`` is suppressed when ``inject_input`` returned ``False`` —
    the event was buffered for opportunistic mid-turn drain
    (``Agent._pending_mid_turn_inputs``) and the OTHER turn that's
    still running owns the next ``idle`` / ``processing_end`` frame.
    Emitting our own here would race the FE's ``processingByTab``
    flag off and the KohakUwUing indicator would blink off until
    the next streaming chunk arrived. Instead a buffered input
    emits an ``input_queued`` ack carrying ``pending_id`` so a shell can
    later ``input_edit`` / ``input_cancel`` it by id (UXI-08a).
    """
    try:
        processed = await agent.inject_input(
            content, source="web", pending_id=pending_id
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        try:
            queue.put_nowait(
                {
                    "type": "error",
                    "source": source_name,
                    "content": str(e),
                    "ts": time.time(),
                }
            )
        except asyncio.QueueFull:
            logger.debug("input error frame dropped — queue full")
        return
    if not processed:
        # Buffered for mid-turn drain — do NOT emit ``idle``; the
        # turn that's actually running will fire its own terminal
        # frames when it ends. Ack the queue slot by id so the client
        # can edit / cancel it before the drain claims it.
        try:
            queue.put_nowait(
                {
                    "type": "input_queued",
                    "source": source_name,
                    "event_id": pending_id,
                    "ts": time.time(),
                }
            )
        except asyncio.QueueFull:
            logger.debug("input_queued frame dropped — queue full")
        return
    try:
        queue.put_nowait({"type": "idle", "source": source_name, "ts": time.time()})
    except asyncio.QueueFull:
        logger.debug("idle frame dropped — queue full")
