"""Mirror worker-owned session events to controller-local stores.

Each worker remains the authoritative single writer. Events for a session pass
through one outbound queue, preserving order while the controller mirror stays
eventually consistent for local Studio reads.
"""

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

from kohakuterrarium.laboratory._internal.app import AppMessage
from kohakuterrarium.laboratory.protocols import LabNotifier, LabRegistrar
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

NAMESPACE = "terrarium.session.sync"


class SessionEventTee:
    """Forwards :class:`SessionStore` events to the controller.

    ``attach`` and ``detach`` are idempotent. Store callbacks enqueue work
    synchronously, while an asynchronous pump sends ordered notifications.
    Transient notification failures retain and retry the current event.
    """

    def __init__(
        self,
        session_id: str,
        store: SessionStore,
        lab_node: LabNotifier,
        *,
        target_node: str = "_host",
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self._session_id = session_id
        self._store = store
        self._node = lab_node
        self._target = target_node
        # Construction can occur outside a running loop; resolve it on attach.
        self._loop = loop
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=1024)
        self._pump_task: asyncio.Task | None = None
        self._attached = False

    def attach(self) -> None:
        """Subscribe to the store and start the outbound pump.

        The caller must have a running event loop. Metadata is queued before
        subscription so the mirror is resumable before its first event arrives.
        """
        if self._attached:
            return
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        self._queue.put_nowait(self._meta_item())
        self._store.subscribe(self._on_event)
        self._pump_task = self._loop.create_task(self._pump())
        self._attached = True

    def _meta_item(self) -> tuple[str, dict[str, Any]]:
        """Return a queue item containing a JSON-safe metadata snapshot."""
        try:
            meta = dict(self._store.load_meta())
        except Exception:  # pragma: no cover - mirroring must not affect the source
            meta = {}
        return ("meta", {"session_id": self._session_id, "meta": _json_safe(meta)})

    def detach(self) -> None:
        """Idempotently unsubscribe from the store and stop the outbound pump."""
        if not self._attached:
            return
        self._store.unsubscribe(self._on_event)
        if self._pump_task is not None and not self._pump_task.done():
            self._pump_task.cancel()
        self._attached = False

    def _on_event(self, key: str, data: dict) -> None:
        try:
            # Normalize unsupported values before the stricter wire packer sees them.
            payload = {
                "session_id": self._session_id,
                "key": key,
                "data": _json_safe(data),
            }
        except Exception:  # pragma: no cover - mirroring must not affect the source
            logger.exception("session-sync: failed to serialise event %r", key)
            return
        # Append callbacks may run off-loop; queue access must return to its loop.
        try:
            self._loop.call_soon_threadsafe(self._enqueue, ("event", payload), key)
        except RuntimeError:  # pragma: no cover - loop closed during shutdown
            pass

    def _enqueue(self, item: tuple[str, dict[str, Any]], key: str) -> None:
        """Enqueue a wire item on-loop, dropping the oldest item when full."""
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:  # pragma: no cover - depends on load
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._queue.put_nowait(item)
            except asyncio.QueueFull:
                logger.warning("session-sync: queue full, dropped event %r", key)

    async def _pump(self) -> None:
        consecutive_failures = 0
        # Retrying the current item prevents permanent mirror gaps; bounded
        # backoff avoids excessive latency after the link recovers.
        try:
            while True:
                wire_type, body = await self._queue.get()
                while True:
                    try:
                        await self._node.notify(
                            to_node=self._target,
                            namespace=NAMESPACE,
                            type=wire_type,
                            body=body,
                        )
                        consecutive_failures = 0
                        break
                    except Exception:  # pragma: no cover - depends on link
                        consecutive_failures += 1
                        # Log one traceback, then summarize repeated link failures.
                        if consecutive_failures == 1:
                            logger.warning(
                                "session-sync: notify failed; will retry until "
                                "link recovers",
                                extra={"event_key": body.get("key")},
                                exc_info=True,
                            )
                        else:
                            logger.debug(
                                "session-sync: notify still failing (%d in a row)",
                                consecutive_failures,
                            )
                        delay = min(0.01 * (2 ** min(consecutive_failures, 7)), 1.0)
                        await asyncio.sleep(delay)
        except asyncio.CancelledError:
            raise


DEFAULT_MIRROR_MAX_OPEN_STORES = 64


class SessionMirrorWriter:
    """Subscribes to ``terrarium.session.sync`` events and writes a mirror.

    Install one per controller.  On each inbound ``event`` the writer
    opens (or reuses) a SessionStore at ``mirror_dir / <session_id>.kohakutr``
    and appends the event with the same agent / data the worker
    recorded.

    Mirror writes are best-effort because the worker store remains authoritative.
    Open stores are bounded by an insertion-ordered LRU to prevent unbounded
    SQLite and FTS handles.
    """

    def __init__(
        self,
        lab_node: LabRegistrar,
        mirror_dir: str | Path,
        *,
        max_open_stores: int = DEFAULT_MIRROR_MAX_OPEN_STORES,
    ) -> None:
        self._node = lab_node
        self._mirror_dir = Path(mirror_dir)
        self._mirror_dir.mkdir(parents=True, exist_ok=True)
        self._stores: dict[str, SessionStore] = {}
        self._max_open_stores = max(1, max_open_stores)
        lab_node.register_app_extension(NAMESPACE, self._dispatch)

    def close(self) -> None:
        """Idempotently unregister and close every cached mirror store."""
        self._node.unregister_app_extension(NAMESPACE)
        for store in self._stores.values():
            try:
                store.close()
            except Exception:  # pragma: no cover - mirroring must not affect the source
                logger.exception("session-sync: failed to close mirror store")
        self._stores.clear()

    def checkpoint(self, session_id: str) -> None:
        """Checkpoint an open mirror store so a raw byte read sees it all.

        Live metadata and recent events may remain in caches or the WAL. Evicted
        stores are already checkpointed, so closed sessions are a no-op.
        """
        store = self._stores.get(session_id)
        if store is None:
            return
        try:
            store.checkpoint()
        except Exception:  # pragma: no cover - mirroring must not affect the source
            logger.exception(
                "session-sync mirror: checkpoint failed for %s", session_id
            )

    def store_for(self, session_id: str) -> SessionStore:
        """Return the mirror store for ``session_id``, opening it lazily.

        Cache hits refresh insertion order so active stores avoid LRU eviction.
        """
        existing = self._stores.pop(session_id, None)
        if existing is not None:
            self._stores[session_id] = existing  # Refresh insertion order.
            return existing
        while len(self._stores) >= self._max_open_stores:
            oldest_id, oldest_store = next(iter(self._stores.items()))
            self._stores.pop(oldest_id, None)
            try:
                oldest_store.close()
            except Exception:  # pragma: no cover - mirroring must not affect the source
                logger.exception(
                    "session-sync: failed to close evicted mirror store %r",
                    oldest_id,
                )
        path = self._mirror_dir / f"{session_id}.kohakutr"
        store = SessionStore(str(path))
        self._stores[session_id] = store
        return store

    def _apply_meta(self, body: dict[str, Any]) -> None:
        """Initialise the mirror store's meta from the worker's snapshot.

        Worker metadata makes the mirror resumable. Per-key assignment preserves
        mirror-only annotations absent from the source snapshot.
        """
        session_id = body.get("session_id")
        meta = body.get("meta")
        if not isinstance(session_id, str) or not isinstance(meta, dict):
            return None
        try:
            store = self.store_for(session_id)
        except Exception:  # pragma: no cover - mirroring must not affect the source
            logger.exception("session-sync mirror: store_for failed for %s", session_id)
            return None
        # Isolate key failures so one oversized value cannot block resumable fields.
        for key, value in meta.items():
            try:
                store.meta[key] = value
            except Exception:  # pragma: no cover - mirroring must not affect the source
                logger.exception(
                    "session-sync mirror: meta key %r write failed for %s",
                    key,
                    session_id,
                )
        return None

    async def _dispatch(self, msg: AppMessage) -> None:
        body = msg.body or {}
        if msg.type == "meta":
            return self._apply_meta(body)
        if msg.type != "event":
            return None
        session_id = body.get("session_id")
        key = body.get("key")
        data = body.get("data") or {}
        if not isinstance(session_id, str) or not isinstance(key, str):
            return None
        try:
            agent = _agent_from_key(key)
            store = self.store_for(session_id)
            event_type = data.get("type", "")
            # SessionStore re-stamps the event type; preserve all other payload data.
            payload = {k: v for k, v in data.items() if k not in ("type",)}
            store.append_event(agent, event_type, payload)
            # Stamp origin only after persistence. Plain assignment avoids the
            # metadata proxy's byte-coercing ``setdefault`` implementation.
            source_node = getattr(msg, "sender_node", "") or body.get("node_id", "")
            if source_node and "on_node" not in store.meta:
                store.meta["on_node"] = source_node
        except Exception:  # pragma: no cover - mirroring must not affect the source
            logger.exception(
                "session-sync mirror: append failed for %s/%s", session_id, key
            )
        return None


def _agent_from_key(key: str) -> str:
    """Extract the agent namespace from a ``<agent>:e<seq>`` event key."""
    if ":" not in key:
        return "unknown"
    return key.split(":", 1)[0]


def _json_safe(value: Any) -> Any:
    """Recursively coerce values into wire-safe JSON-compatible forms.

    Bytes use base64 markers, while unsupported objects fall back to ``repr``.
    """
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, bytes):
        return {"__bytes_b64__": base64.b64encode(value).decode("ascii")}
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    try:
        json.dumps(value)
        return value
    except TypeError:
        return repr(value)


__all__ = [
    "NAMESPACE",
    "SessionEventTee",
    "SessionMirrorWriter",
]
