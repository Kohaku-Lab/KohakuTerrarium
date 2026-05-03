"""Runtime graph websocket for graph-editor data refresh.

Streams an initial graph snapshot, engine topology/lifecycle events, and
shared-channel messages across every live graph. Clients can refetch the
HTTP snapshot after broad topology events and patch small channel-message
updates directly.
"""

import asyncio
import json
import time
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from kohakuterrarium.api.deps import get_engine
from kohakuterrarium.api.routes.runtime_graph import build_runtime_graph_snapshot
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()


@router.websocket("/ws/runtime/graph")
async def runtime_graph_stream(websocket: WebSocket):
    """Stream runtime graph events for graph-editor data wiring."""
    await websocket.accept()
    engine = get_engine()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
    loop = asyncio.get_running_loop()
    channel_callbacks: dict[tuple[str, str], Any] = {}
    known_channels: set[tuple[str, str]] = set()

    async def enqueue(payload: dict[str, Any]) -> None:
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            logger.debug("Runtime graph WS queue full - dropping event")

    def enqueue_threadsafe(payload: dict[str, Any]) -> None:
        def put() -> None:
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                logger.debug("Runtime graph WS queue full - dropping event")

        try:
            loop.call_soon_threadsafe(put)
        except RuntimeError:
            logger.debug("Runtime graph WS loop closed - dropping event")

    async def sync_channel_observers() -> None:
        for graph in engine.list_graphs():
            env = engine._environments.get(graph.graph_id)
            registry = (
                getattr(env, "shared_channels", None) if env is not None else None
            )
            if registry is None:
                continue
            for channel_name in registry.list_channels():
                key = (graph.graph_id, channel_name)
                if key in known_channels:
                    continue
                channel = registry.get(channel_name)
                if channel is None:
                    continue
                callback = _make_channel_callback(graph.graph_id, enqueue_threadsafe)
                channel.on_send(callback)
                channel_callbacks[key] = (channel, callback)
                known_channels.add(key)

    async def engine_events() -> None:
        async for event in engine.subscribe():
            payload = {
                "type": (
                    event.kind.value
                    if hasattr(event.kind, "value")
                    else str(event.kind)
                ),
                "version": _version(),
                "graph_id": event.graph_id,
                "creature_id": event.creature_id,
                "channel": event.channel,
                "payload": event.payload or {},
                "ts": event.ts,
            }
            await enqueue(payload)
            await sync_channel_observers()

    engine_task = asyncio.create_task(engine_events())

    try:
        await sync_channel_observers()
        snapshot = build_runtime_graph_snapshot(engine)
        await websocket.send_json(
            {"type": "subscribed", "version": snapshot["version"]}
        )
        await websocket.send_json({"type": "snapshot", "snapshot": snapshot})
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("Runtime graph WS error", error=str(exc), exc_info=True)
        with suppress(Exception):
            await websocket.send_json({"type": "error", "message": str(exc)})
        with suppress(Exception):
            await websocket.close()
    finally:
        engine_task.cancel()
        with suppress(asyncio.CancelledError):
            await engine_task
        for channel, callback in channel_callbacks.values():
            with suppress(Exception):
                channel.remove_on_send(callback)


def _make_channel_callback(graph_id: str, enqueue):
    def on_channel_send(channel_name: str, message: Any) -> None:
        enqueue(
            {
                "type": "channel_message",
                "version": _version(),
                "graph_id": graph_id,
                "channel": channel_name,
                "sender": getattr(message, "sender", ""),
                "content": _jsonable(getattr(message, "content", "")),
                "content_preview": _preview(getattr(message, "content", "")),
                "message_id": getattr(message, "message_id", ""),
                "timestamp": _timestamp_to_string(getattr(message, "timestamp", None)),
            }
        )

    return on_channel_send


def _version() -> int:
    return int(time.time() * 1000)


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def _preview(value: Any, limit: int = 160) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False)
        except TypeError:
            text = str(value)
    text = text.replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _timestamp_to_string(value: Any) -> str:
    if value is None:
        return ""
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return str(value)
