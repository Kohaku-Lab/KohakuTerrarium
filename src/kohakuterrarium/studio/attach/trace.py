"""Live trace attach — engine-event stream over a session.

Body drained verbatim from ``api/ws/sessions.py`` (Phase 1, agent E).
Subscribes to a running session's ``SessionStore.append_event`` and
streams every new event to a connected WS client in real time.

Design: ``plans/session-system/viewer-design.md`` §4 (Live attach) + §5.

Mechanics:

* Resolves the session against the studio's live store registry —
  only works while the session is live (resumed in-process).
  Closed-on-disk sessions reject with code 1011 and a ``not_live``
  reason.
* Per-connection ``asyncio.Queue`` decouples the synchronous
  ``append_event`` callback from the async WS send path. The callback
  uses ``loop.call_soon_threadsafe`` so a tool worker thread that emits
  via the store cannot block on the WS.
* Optional ``agent`` parameter filters by event-key prefix
  (``<agent>:e``) so a viewer focused on one creature does not get
  flooded by sibling agents in a terrarium.
"""

import asyncio
from typing import Any, Iterable

from fastapi import WebSocket, WebSocketDisconnect

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.sessions.lifecycle import list_session_stores
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Bounded queue per connection. Events are small dicts; 1000 covers any
# realistic burst (a slow client falling behind by 1k events is already
# pathological — drop newer events rather than balloon memory).
_QUEUE_MAX = 1000


def _find_live_store(
    session_name: str, stores: Iterable[SessionStore] | None = None
) -> SessionStore | None:
    """Locate the live ``SessionStore`` for ``session_name``.

    Iterates the studio's live store registry and matches on the file
    stem so callers can pass the canonical name (no ``.kohakutr``
    extension, no version suffix). Returns ``None`` if no live
    creature / session owns this name.
    """
    candidates = stores if stores is not None else list_session_stores()
    for store in candidates:
        if store is None:
            continue
        path = getattr(store, "_path", "") or getattr(store, "path", "")
        path_str = str(path)
        if not path_str:
            continue
        # Match on the basename without ``.kohakutr`` / ``.kohakutr.vN``
        # so the WS URL can pass the canonical name the lister surfaces.
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
    stores: Iterable[SessionStore] | None = None,
) -> None:
    """Live event stream for a running session.

    Closes with 1011 ``not_live`` if the session is not in-process. The
    frontend should hide / disable the Live-attach toggle for sessions
    whose status is ``"paused"`` so this close path is the exception,
    not the common case.

    ``stores`` lets callers pin a specific iterable; when ``None`` the
    studio's live registry is consulted directly.
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
        # ``key`` is ``<agent>:e<seq>``. Filter by agent prefix if the
        # caller asked for one. Match the bare name and any attached-
        # agent namespace anchored at it (``<agent>:attached:...``).
        if agent is not None:
            ns = _agent_from_key(key)
            if ns != agent and not ns.startswith(f"{agent}:attached:"):
                return
        payload = {"type": "event", "key": key, "event": data}
        try:
            loop.call_soon_threadsafe(_enqueue_or_drop, queue, payload)
        except RuntimeError:
            # Loop closed (WS torn down between callback and dispatch).
            return

    store.subscribe(_on_event)
    # Send a small hello so clients know subscription succeeded; useful
    # for the "Live attach" badge to flip from "connecting" to "live".
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
        logger.debug("Session WS error", error=str(e), exc_info=True)
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
    """Best-effort enqueue. Drops the event if the queue is full so a
    slow client can't pin memory in the running session.
    """
    try:
        queue.put_nowait(payload)
    except asyncio.QueueFull:
        logger.debug("Session WS queue full — dropping event")
