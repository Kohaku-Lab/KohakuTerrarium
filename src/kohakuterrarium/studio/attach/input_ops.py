"""Normalize, route, and process inbound Studio attach websocket messages."""

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
    """Return a canonical text or content-part payload from a websocket frame."""
    content = data.get("content")
    if isinstance(content, list):
        parts = normalize_content_parts(content) or []
        return content_parts_to_dicts(parts)
    if isinstance(content, str):
        return content
    message = data.get("message", "")
    return message if isinstance(message, str) else ""


def _resolve_target(engine: Any, creature: Any, session_id: str, data: dict) -> Any:
    """Resolve the creature addressed by a targeted websocket frame.

    A missing target addresses the creature bound to the connection. An explicit
    display name allows one connection to drive multiple creature tabs. ``KeyError``
    propagates when the named creature does not belong to the session.
    """
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
    """Apply ``input_edit`` or ``input_cancel`` to a queued message.

    The operation succeeds only while ``event_id`` remains in the pending buffer;
    otherwise the acknowledgement reports ``status="already_sent"``. Echoing the
    event ID lets the client reconcile the corresponding queued-message indicator.
    """
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
    """Inject input without blocking the websocket receive loop.

    Errors and terminal notices use the outbound queue so this task does not need
    direct access to the websocket and inbound frames such as ``ui_reply`` remain
    responsive during a turn.

    A false return means another turn buffered the input for a mid-turn drain. That
    active turn owns the next ``idle`` or ``processing_end`` frame; emitting one here
    would clear the client's processing state too early. The ``input_queued``
    acknowledgement exposes ``pending_id`` for later edit or cancellation.
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
        # The active turn owns terminal frames; an early ``idle`` would clear the
        # client's processing state before that turn ends.
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
    # A queued turn can start before this older input coroutine resumes.
    if agent.is_processing:
        return
    try:
        queue.put_nowait({"type": "idle", "source": source_name, "ts": time.time()})
    except asyncio.QueueFull:
        logger.debug("idle frame dropped — queue full")
