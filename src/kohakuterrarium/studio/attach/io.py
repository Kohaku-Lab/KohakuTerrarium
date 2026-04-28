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

    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") != "input":
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
            try:
                await agent.inject_input(content, source="web")
            except Exception as e:
                await websocket.send_json(
                    {
                        "type": "error",
                        "source": creature.name,
                        "content": str(e),
                        "ts": time.time(),
                    }
                )
                continue
            await websocket.send_json(
                {"type": "idle", "source": creature.name, "ts": time.time()}
            )
    finally:
        queue.put_nowait(None)
        fwd_task.cancel()
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
