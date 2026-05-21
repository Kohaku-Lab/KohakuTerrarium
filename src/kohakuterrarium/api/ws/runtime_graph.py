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

from kohakuterrarium.api.auth.ws_auth import accept_with_auth_echo
from kohakuterrarium.api.deps import get_service_legacy as get_service
from kohakuterrarium.studio._runtime import host_engine_or_none
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()


@router.websocket("/ws/runtime/graph")
async def runtime_graph_stream(websocket: WebSocket):
    """Stream runtime graph events for graph-editor data wiring."""
    await accept_with_auth_echo(websocket)
    # The snapshot and engine-event stream both route through the
    # ``TerrariumService`` Protocol, so they aggregate across workers
    # in lab-host mode.  The per-channel ``on_send`` observer hooks
    # still need a host engine's internal channel registry — in
    # lab-host mode there is none (``host_engine_or_none`` → ``None``),
    # so channel-message frames fall back to the generic engine-event
    # stream rather than the richer callback shape.
    # WebSocket runtime-graph stream is a long-lived global view; it
    # does not scope to a user.  The legacy non-request service
    # accessor (re-exported here as ``get_service``) provides this —
    # multi-user per-engine isolation is enforced on creature-scoped
    # routes, not the global graph snapshot.
    service = get_service()
    engine = host_engine_or_none(service)
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
        # Channel ``on_send`` hooks need a host engine's channel
        # registry.  Lab-host has none — skip; the generic engine-event
        # stream still carries channel_message events.
        if engine is None:
            return
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
        # Service-routed: fans out across workers in lab-host mode.
        async for event in service.subscribe():
            kind = event.kind.value if hasattr(event.kind, "value") else str(event.kind)
            # CHANNEL_MESSAGE events are normally delivered by the
            # endpoint-local ``_make_channel_callback`` hook, which
            # produces the richer flat shape the graph editor consumes
            # (sender / content / content_preview / message_id) —
            # forwarding the generic copy too would double-deliver
            # (B-api-1). BUT in lab-host mode ``engine`` is ``None``,
            # so ``sync_channel_observers`` registers NO local callbacks
            # and the only source of channel_message frames is the
            # service-routed engine-event stream itself (CF-8). In that
            # mode synthesize the rich flat shape from the engine event
            # payload and forward it; otherwise drop to avoid the dup.
            if kind == "channel_message":
                if engine is None:
                    payload = event.payload or {}
                    content = payload.get("content", "")
                    await enqueue(
                        {
                            "type": "channel_message",
                            "version": _version(),
                            "graph_id": event.graph_id,
                            "channel": event.channel,
                            "sender": str(payload.get("sender", "")),
                            "content": _jsonable(content),
                            "content_preview": _preview(content),
                            "message_id": str(payload.get("message_id", "")),
                            "timestamp": _timestamp_to_string(payload.get("timestamp")),
                        }
                    )
            else:
                await enqueue(
                    {
                        "type": kind,
                        "version": _version(),
                        "graph_id": event.graph_id,
                        "creature_id": event.creature_id,
                        "channel": event.channel,
                        "payload": event.payload or {},
                        "ts": event.ts,
                    }
                )
            await sync_channel_observers()

    # Build + send the initial snapshot BEFORE subscribing to engine
    # events. Otherwise the engine_task pump can enqueue events with a
    # ``version`` timestamp pre-dating the snapshot's ``version``; the
    # client's patch logic uses that timestamp for ordering and would
    # drop the earlier events as stale.
    engine_task: asyncio.Task | None = None
    try:
        await sync_channel_observers()
        # Service-routed snapshot — host-local in standalone, fanned
        # out across workers in lab-host mode.
        snapshot = await service.runtime_graph_snapshot()
        await websocket.send_json(
            {"type": "subscribed", "version": snapshot["version"]}
        )
        await websocket.send_json({"type": "snapshot", "snapshot": snapshot})
        engine_task = asyncio.create_task(engine_events())
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
        if engine_task is not None:
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
