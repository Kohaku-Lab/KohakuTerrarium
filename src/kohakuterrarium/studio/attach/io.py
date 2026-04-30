"""Single IO attach — engine-backed bidirectional chat.

Replaces the legacy ``ws/agents.py``, ``ws/chat.py:ws_terrarium``,
``ws/chat.py:ws_creature``, plus
``serving/agent_session.py:StreamOutput`` and the helper trio
``_attach_terrarium_outputs / _register_channel_callbacks /
_send_channel_history`` in ``ws/chat.py``.

The new attach mounts onto a creature via ``engine.get_creature(cid)``
and translates the engine's ``OutputModule`` events to the WS frame
schema the frontend already speaks.  When the creature lives in a
multi-creature graph, the same WS connection also surfaces shared-
channel messages and history (the legacy "terrarium WS" behaviour),
so the frontend chat panel works the same in both 1- and N-creature
sessions.
"""

import asyncio
import time
from typing import Any

from fastapi import WebSocket

from kohakuterrarium.llm.message import (
    content_parts_to_dicts,
    normalize_content_parts,
)
from kohakuterrarium.modules.output.event import UIReply
from kohakuterrarium.studio.attach._event_stream import StreamOutput, get_event_log
from kohakuterrarium.studio.sessions.lifecycle import find_creature
from kohakuterrarium.terrarium.engine import Terrarium
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


def _handle_ui_reply(
    data: dict[str, Any],
    agent: Any,
    ws: WebSocket,
    queue: asyncio.Queue,
    source_name: str,
) -> None:
    """Route an inbound ``ui_reply`` WS frame to the agent's bus.

    Sync helper invoked from the receive loop. Submits the reply to
    the agent's output router; the router resolves any pending Future
    and broadcasts a supersede to other secondary outputs (which the
    frontend translates back into an ``ack`` frame via StreamOutput).

    The ack frame itself is enqueued for the WS forward task so the
    submitting client gets ``{type: "ui_reply_ack", event_id, status}``
    even when the reply was rejected (unknown id / superseded).
    """
    event_id = data.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        return
    action_id = data.get("action_id", "")
    values = data.get("values") or {}
    user = data.get("user")
    ts = data.get("ts") or time.time()

    reply = UIReply(
        event_id=event_id,
        action_id=action_id,
        values=values if isinstance(values, dict) else {},
        user=user if isinstance(user, str) else None,
        timestamp=float(ts) if isinstance(ts, (int, float)) else time.time(),
    )

    try:
        _accepted, ack_status = agent.output_router.submit_reply_with_status(reply)
    except Exception as e:
        logger.debug("submit_reply failed", error=str(e), exc_info=True)
        ack_status = "unknown"

    ack = {
        "type": "ui_reply_ack",
        "event_id": event_id,
        "status": ack_status,
        "source": source_name,
        "ts": time.time(),
    }
    try:
        queue.put_nowait(ack)
    except asyncio.QueueFull:
        logger.debug("ui_reply_ack dropped — queue full")


async def _process_input(
    agent: Any,
    content: str | list[dict[str, Any]],
    queue: asyncio.Queue,
    source_name: str,
) -> None:
    """Run ``agent.inject_input`` in its own task so the WS receive
    loop can keep processing inbound frames (notably ``ui_reply``)
    while the agent is mid-turn.

    Errors and the post-turn ``idle`` notice are pushed via the same
    outbound queue that ``_forward_queue`` drains, so the caller
    doesn't need to share the websocket reference.
    """
    try:
        await agent.inject_input(content, source="web")
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
    try:
        queue.put_nowait({"type": "idle", "source": source_name, "ts": time.time()})
    except asyncio.QueueFull:
        logger.debug("idle frame dropped — queue full")


async def _forward_queue(queue: asyncio.Queue, ws: WebSocket) -> None:
    try:
        while True:
            msg = await queue.get()
            if msg is None:
                break
            await ws.send_json(msg)
    except Exception as e:
        logger.debug("WS forward queue error", error=str(e), exc_info=True)


def _register_channel_callbacks(
    env: Any, queue: asyncio.Queue
) -> list[tuple[Any, Any]]:
    """Subscribe to all shared-channel sends for a graph environment."""
    out: list[tuple[Any, Any]] = []

    def make_cb(ch_name: str):
        def cb(channel_name, message):
            ts = (
                message.timestamp.isoformat()
                if hasattr(message.timestamp, "isoformat")
                else str(message.timestamp)
            )
            queue.put_nowait(
                {
                    "type": "channel_message",
                    "source": "channel",
                    "channel": channel_name,
                    "sender": message.sender,
                    "content": message.content,
                    "message_id": message.message_id,
                    "timestamp": ts,
                    "ts": time.time(),
                }
            )

        return cb

    for ch in env.shared_channels._channels.values():
        cb = make_cb(ch.name)
        ch.on_send(cb)
        out.append((ch, cb))
    return out


async def _send_channel_history(ws: WebSocket, env: Any) -> None:
    """Replay the shared-channel history that happened before this WS."""
    for ch in env.shared_channels._channels.values():
        for msg in ch.history:
            ts = (
                msg.timestamp.isoformat()
                if hasattr(msg.timestamp, "isoformat")
                else str(msg.timestamp)
            )
            await ws.send_json(
                {
                    "type": "channel_message",
                    "source": "channel",
                    "channel": ch.name,
                    "sender": msg.sender,
                    "content": msg.content,
                    "message_id": msg.message_id,
                    "timestamp": ts,
                    "ts": time.time(),
                    "history": True,
                }
            )


async def attach_io(
    websocket: WebSocket,
    engine: Terrarium,
    session_id: str,
    creature_id: str,
) -> None:
    """Run the IO attach loop on ``websocket`` until it disconnects.

    Resolves the creature via the engine, attaches a ``StreamOutput``
    secondary sink, and forwards every event through the WS.  When
    the creature shares a graph with peers, the shared channels are
    surfaced through the same connection (terrarium-style chat).
    """
    creature = find_creature(engine, session_id, creature_id)
    agent = creature.agent

    queue: asyncio.Queue = asyncio.Queue()
    log = get_event_log(f"{session_id}:{creature.creature_id}")
    out_module = StreamOutput(creature.name, queue, log)
    agent.output_router.add_secondary(out_module)

    # Surface graph-level channels for multi-creature sessions.
    env = engine._environments.get(creature.graph_id)
    channel_cbs: list[tuple[Any, Any]] = []
    if env is not None and env.shared_channels.list_channels():
        channel_cbs = _register_channel_callbacks(env, queue)
        await _send_channel_history(websocket, env)

    # Send a session_info frame so the frontend identifies the creature.
    await websocket.send_json(
        {
            "type": "activity",
            "activity_type": "session_info",
            "source": creature.name,
            "model": agent.config.model,
            "agent_name": creature.name,
            "ts": time.time(),
        }
    )

    fwd_task = asyncio.create_task(_forward_queue(queue, websocket))

    # Track input-processing tasks so we can cancel them on disconnect.
    # Each user input fires its own task — the receive loop must NOT
    # ``await`` ``agent.inject_input`` directly, because a tool that
    # awaits a UIReply (``ask_user``, ``confirm``, etc.) would deadlock
    # waiting for a frame the receive loop can't fetch while it's
    # stuck inside ``inject_input``.
    input_tasks: list[asyncio.Task] = []

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type == "ui_reply":
                # Phase B: inbound reply to an interactive OutputEvent.
                # Route into the agent's output_router; it dispatches to
                # the awaiting Future and broadcasts supersede to peers.
                _handle_ui_reply(data, agent, websocket, queue, creature.name)
                continue
            if msg_type == "ui_dismiss":
                # Display-only event was dismissed by the user. Nothing
                # to await; informational so audit / observers can log.
                continue
            if msg_type != "input":
                continue
            content = _normalize_input_content(data)
            if not content:
                continue
            user_evt = {
                "type": "user_input",
                "source": creature.name,
                "content": content,
                "ts": time.time(),
            }
            log.append(user_evt)
            await queue.put(user_evt)
            # Fire-and-forget: spawn a task so the receive loop returns
            # to ``await receive_json()`` immediately. Without this,
            # interactive tools like ``ask_user`` deadlock — the agent
            # awaits a UIReply while this loop sits inside
            # ``inject_input`` unable to deliver it.
            task = asyncio.create_task(
                _process_input(agent, content, queue, creature.name)
            )
            input_tasks.append(task)
            # Drop completed tasks so the list doesn't grow forever.
            input_tasks[:] = [t for t in input_tasks if not t.done()]
    finally:
        queue.put_nowait(None)
        fwd_task.cancel()
        for task in input_tasks:
            task.cancel()
        try:
            agent.output_router.remove_secondary(out_module)
        except Exception as e:
            logger.debug(
                "Failed to remove secondary output",
                error=str(e),
                exc_info=True,
            )
        for ch, cb in channel_cbs:
            try:
                ch.remove_on_send(cb)
            except Exception as e:
                logger.debug(
                    "Failed to remove channel callback",
                    error=str(e),
                    exc_info=True,
                )
