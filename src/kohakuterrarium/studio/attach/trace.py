"""Stream newly appended events from a live session store over a websocket.

Only in-process stores can be attached. A bounded per-connection queue bridges
synchronous, potentially cross-thread store callbacks to asynchronous websocket
sends, and optional agent filtering limits the stream to one creature namespace.
"""

import asyncio
from collections.abc import Iterable, Mapping
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# A lagging trace viewer must drop new events rather than grow session memory without
# bound; one thousand small event dictionaries accommodates normal bursts.
_QUEUE_MAX = 1000


def _find_live_store(
    session_name: str,
    stores: "Mapping[str, SessionStore] | Iterable[SessionStore] | None" = None,
) -> SessionStore | None:
    """Locate the runtime-owned live store identified by graph ID or file stem.

    Mapping keys are checked first because live-session URLs may contain a graph ID
    unrelated to the backing filename. File matching accepts canonical session names
    without ``.kohakutr``, ``.kt``, or version suffixes. The caller supplies the
    instance-scoped registry for the runtime it serves.
    """
    if stores is None:
        return None
    if isinstance(stores, Mapping):
        direct = stores.get(session_name)
        if direct is not None:
            return direct
        candidates: Iterable[SessionStore] = stores.values()
    else:
        candidates = stores
    for store in candidates:
        if store is None:
            continue
        path = getattr(store, "_path", "") or getattr(store, "path", "")
        path_str = str(path)
        if not path_str:
            continue
        # Session listings expose a canonical stem rather than storage suffixes.
        base = path_str.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        stem = base
        for suffix in (".kohakutr", ".kt"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        if ".kohakutr.v" in base:
            stem = base.split(".kohakutr.v", 1)[0]
        if stem == session_name:
            return store
    return None


def _agent_from_key(key: str) -> str:
    """Extract the agent prefix from an event key (``<agent>:e<seq>``)."""
    head, _sep, _tail = key.rpartition(":e")
    return head


async def run_trace_attach(
    websocket: WebSocket,
    session_name: str,
    agent: str | None,
    stores: "Mapping[str, SessionStore] | Iterable[SessionStore] | None" = None,
) -> None:
    """Stream events for an in-process session until the websocket disconnects.

    Missing live stores produce a ``not_live`` error and close code 1011. ``stores``
    is the serving runtime's registry; ``None`` intentionally makes every lookup
    unavailable rather than consulting global state.
    """
    await websocket.accept()
    store = _find_live_store(session_name, stores)
    if store is None:
        await websocket.send_json(
            {
                "type": "error",
                "reason": "not_live",
                "session_name": session_name,
                "message": (
                    "Session is not currently live in-process. Resume it "
                    "before subscribing to live events."
                ),
            }
        )
        await websocket.close(code=1011)
        return

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_QUEUE_MAX)

    def _on_event(key: str, data: dict) -> None:
        # Filtering includes the bare agent namespace and attached-agent descendants.
        if agent is not None:
            ns = _agent_from_key(key)
            if ns != agent and not ns.startswith(f"{agent}:attached:"):
                return
        payload = {"type": "event", "key": key, "event": data}
        try:
            loop.call_soon_threadsafe(_enqueue_or_drop, queue, payload)
        except RuntimeError:
            # Teardown may close the loop after the store invokes this callback.
            return

    store.subscribe(_on_event)
    # The acknowledgement distinguishes a quiet live stream from a stalled connection.
    try:
        await websocket.send_json(
            {
                "type": "subscribed",
                "session_name": session_name,
                "agent": agent,
            }
        )
        while True:
            payload = await queue.get()
            await websocket.send_json(payload)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning("Session WS error", error=str(e), exc_info=True)
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass
    finally:
        store.unsubscribe(_on_event)


def _enqueue_or_drop(queue: asyncio.Queue, payload: dict) -> None:
    """Enqueue an event unless the bounded queue is full."""
    try:
        queue.put_nowait(payload)
    except asyncio.QueueFull:
        logger.debug("Session WS queue full — dropping event")
