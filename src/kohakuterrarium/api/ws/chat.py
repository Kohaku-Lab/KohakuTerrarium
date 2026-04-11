"""Unified WebSocket endpoints.

  /ws/terrariums/{terrarium_id}  - ALL events for a terrarium
  /ws/creatures/{agent_id}       - ALL events for a standalone agent

Every event tagged with source. Channel messages captured via on_send
callbacks (works for both queue and broadcast channels).
"""

import asyncio
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from kohakuterrarium.api.deps import get_manager
from kohakuterrarium.api.events import StreamOutput, get_event_log

router = APIRouter()


async def _forward_queue(queue: asyncio.Queue, ws: WebSocket) -> None:
    """Forward messages from an asyncio queue to a WebSocket."""
    try:
        while True:
            msg = await queue.get()
            if msg is None:
                break
            await ws.send_json(msg)
    except Exception:
        pass


# -- Terrarium WebSocket helpers ------------------------------------------


def _attach_terrarium_outputs(
    runtime, terrarium_id: str, queue: asyncio.Queue, manager
) -> list[tuple[str, StreamOutput, object]]:
    """Attach StreamOutput to root agent and all creatures.

    Returns a list of (name, output, agent) tuples for cleanup.
    """
    attached: list[tuple[str, StreamOutput, object]] = []

    if runtime.root_agent is not None:
        log = get_event_log(f"{terrarium_id}:root")
        out = StreamOutput("root", queue, log)
        runtime.root_agent.output_router.add_secondary(out)
        attached.append(("root", out, runtime.root_agent))
        manager.terrarium_mount(terrarium_id, "root")

    for cname in runtime.get_status()["creatures"]:
        agent = runtime.get_creature_agent(cname)
        if agent is None:
            continue
        log = get_event_log(f"{terrarium_id}:{cname}")
        out = StreamOutput(cname, queue, log)
        agent.output_router.add_secondary(out)
        attached.append((cname, out, agent))
        manager.terrarium_mount(terrarium_id, cname)

    return attached


def _register_channel_callbacks(
    runtime, queue: asyncio.Queue
) -> list[tuple[object, object]]:
    """Subscribe to all shared channel messages via on_send callbacks.

    Returns a list of (channel, callback) for cleanup.
    """
    channel_cbs: list[tuple[object, object]] = []

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
                    "content": (
                        message.content
                        if isinstance(message.content, str)
                        else str(message.content)
                    ),
                    "message_id": message.message_id,
                    "timestamp": ts,
                    "ts": time.time(),
                }
            )

        return cb

    for ch in runtime.environment.shared_channels._channels.values():
        cb = make_cb(ch.name)
        ch.on_send(cb)
        channel_cbs.append((ch, cb))

    return channel_cbs


async def _send_channel_history(ws: WebSocket, runtime) -> None:
    """Send historical channel messages that happened before the WS connected."""
    for ch in runtime.environment.shared_channels._channels.values():
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
                    "content": (
                        msg.content
                        if isinstance(msg.content, str)
                        else str(msg.content)
                    ),
                    "message_id": msg.message_id,
                    "timestamp": ts,
                    "ts": time.time(),
                    "history": True,
                }
            )


async def _handle_terrarium_input(ws: WebSocket, manager, terrarium_id: str) -> None:
    """Handle incoming WebSocket messages for a terrarium."""
    while True:
        data = await ws.receive_json()
        if data.get("type") != "input":
            continue
        target = data.get("target", "root")
        message = data.get("message", "")
        if not message:
            continue
        try:
            session = manager.terrarium_mount(terrarium_id, target)
            log = get_event_log(f"{terrarium_id}:{target}")
            log.append(
                {
                    "type": "user_input",
                    "source": target,
                    "content": message,
                    "ts": time.time(),
                }
            )
            await session.agent.inject_input(message, source="web")
            await ws.send_json({"type": "idle", "source": target, "ts": time.time()})
        except ValueError as e:
            await ws.send_json(
                {
                    "type": "error",
                    "source": target,
                    "content": str(e),
                    "ts": time.time(),
                }
            )


def _cleanup_terrarium_ws(
    attached: list[tuple[str, StreamOutput, object]],
    channel_cbs: list[tuple[object, object]],
) -> None:
    """Detach secondary outputs and remove channel callbacks."""
    for _, out, agent in attached:
        try:
            agent.output_router.remove_secondary(out)
        except Exception:
            pass
    for ch, cb in channel_cbs:
        try:
            ch.remove_on_send(cb)
        except Exception:
            pass


# -- /ws/terrariums/{terrarium_id} ----------------------------------------


@router.websocket("/ws/terrariums/{terrarium_id}")
async def ws_terrarium(websocket: WebSocket, terrarium_id: str):
    """Stream all events from a terrarium (root + creatures + channels)."""
    await websocket.accept()
    manager = get_manager()

    try:
        runtime = manager._get_runtime(terrarium_id)
    except ValueError as e:
        await websocket.send_json({"type": "error", "content": str(e)})
        await websocket.close()
        return

    queue: asyncio.Queue = asyncio.Queue()
    attached = _attach_terrarium_outputs(runtime, terrarium_id, queue, manager)
    channel_cbs = _register_channel_callbacks(runtime, queue)
    await _send_channel_history(websocket, runtime)

    fwd_task = asyncio.create_task(_forward_queue(queue, websocket))

    try:
        await _handle_terrarium_input(websocket, manager, terrarium_id)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        queue.put_nowait(None)
        fwd_task.cancel()
        _cleanup_terrarium_ws(attached, channel_cbs)
        try:
            await websocket.close()
        except RuntimeError:
            pass


# -- /ws/creatures/{agent_id} ---------------------------------------------


@router.websocket("/ws/creatures/{agent_id}")
async def ws_creature(websocket: WebSocket, agent_id: str):
    """Stream all events from a standalone agent."""
    await websocket.accept()
    manager = get_manager()

    session = manager._agents.get(agent_id)
    if not session:
        await websocket.send_json(
            {"type": "error", "content": f"Agent not found: {agent_id}"}
        )
        await websocket.close()
        return

    queue: asyncio.Queue = asyncio.Queue()
    log = get_event_log(f"agent:{agent_id}")
    out = StreamOutput(session.agent.config.name, queue, log)
    session.agent.output_router.add_secondary(out)

    # Send session_info so the frontend knows which agent this is
    await websocket.send_json(
        {
            "type": "activity",
            "activity_type": "session_info",
            "source": session.agent.config.name,
            "model": session.agent.config.model,
            "agent_name": session.agent.config.name,
            "ts": time.time(),
        }
    )

    fwd_task = asyncio.create_task(_forward_queue(queue, websocket))

    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") != "input":
                continue
            message = data.get("message", "")
            if not message:
                continue
            log.append(
                {
                    "type": "user_input",
                    "source": session.agent.config.name,
                    "content": message,
                    "ts": time.time(),
                }
            )
            await session.agent.inject_input(message, source="web")
            await websocket.send_json(
                {
                    "type": "idle",
                    "source": session.agent.config.name,
                    "ts": time.time(),
                }
            )
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        queue.put_nowait(None)
        fwd_task.cancel()
        try:
            session.agent.output_router.remove_secondary(out)
        except Exception:
            pass
        try:
            await websocket.close()
        except RuntimeError:
            pass
