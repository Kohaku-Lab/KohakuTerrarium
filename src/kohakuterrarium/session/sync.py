"""Session event mirroring across the Laboratory layer.

The worker that owns a live :class:`SessionStore` is the sole writer
for that session.  Events get tee'd to the controller in real time via
APP messages on namespace ``terrarium.session.sync``; the controller's
:class:`SessionMirrorWriter` opens (or reuses) a mirror SessionStore
under its own session dir and appends each event.

That way Studio's persistence reads (history, viewer, fork) can be
served from the controller's local mirror without round-trips to the
worker for every list / paginate call.

The locked decision in ``wiring.md`` for session storage is mirrored —
single writer (the worker), eventual consistency on the mirror,
order-preserving (events from a single session are serialized through
one outbound queue on the worker).

Wire shape per event (APP body on ``terrarium.session.sync`` /
``event``):

::

    {
        "session_id": str,
        "key": str,          # SessionStore event key, e.g. "alice:e000003"
        "data": dict,        # event payload as written by append_event
    }
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


# ---------------------------------------------------------------------------
# Producer side — worker
# ---------------------------------------------------------------------------


class SessionEventTee:
    """Forwards :class:`SessionStore` events to the controller.

    Attach via :meth:`attach` once you have a store + a lab node.
    Detach via :meth:`detach` when the session is closing.  Tee is
    idempotent — subscribing twice is a no-op (SessionStore.subscribe
    dedupes by callable identity).

    The callback runs synchronously inside :meth:`append_event` (a
    SessionStore design choice).  We schedule the actual ``notify``
    on the event loop so the worker's append path stays non-blocking.
    The :class:`asyncio.Queue` smooths bursts; if a notify fails, the
    Tee logs at debug and continues — losing one event is preferable
    to back-pressuring the engine's append path.
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
        # Do NOT eagerly grab a loop here.  The deprecated
        # ``asyncio.get_event_loop()`` raises ``RuntimeError`` on 3.12+
        # when no loop is current — and a ``SessionEventTee`` is
        # constructed from ``WorkerSessionAttacher.attach()``, a sync
        # method.  The loop is only actually needed once the pump
        # starts, so resolve it lazily in :meth:`attach`.
        self._loop = loop
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=1024)
        self._pump_task: asyncio.Task | None = None
        self._attached = False

    def attach(self) -> None:
        """Subscribe to the store and start the outbound pump.

        Must be called from a running event loop (it spawns the pump
        task) — which it always is in production: ``attach`` is invoked
        inside the async ``terrarium.runtime`` adapter handler.

        The store's meta snapshot is enqueued *before* ``subscribe`` so
        it is the first wire message — the controller's mirror store is
        initialised with ``config_type`` / ``config_path`` / ``agents``
        ahead of any event.  Without it the mirror ``.kohakutr`` carries
        empty meta and a resume off it fails ("Session is a None").
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
        """A ``("meta", body)`` queue item snapshotting the store meta."""
        try:
            meta = dict(self._store.load_meta())
        except Exception:  # pragma: no cover - defensive
            meta = {}
        return ("meta", {"session_id": self._session_id, "meta": _json_safe(meta)})

    def detach(self) -> None:
        """Unsubscribe and stop the pump.  Idempotent."""
        if not self._attached:
            return
        self._store.unsubscribe(self._on_event)
        if self._pump_task is not None and not self._pump_task.done():
            self._pump_task.cancel()
        self._attached = False

    # SessionStore.subscribe callback signature: (key: str, data: dict) -> None
    def _on_event(self, key: str, data: dict) -> None:
        try:
            # SessionStore stores data dicts that should be msgpack-safe
            # already.  We still round-trip through JSON to catch
            # anything non-serialisable (bytes/Path/etc.) before hitting
            # the wire — the kohakuvault packer rejects bytes outright.
            payload = {
                "session_id": self._session_id,
                "key": key,
                "data": _json_safe(data),
            }
        except Exception:  # pragma: no cover - defensive
            logger.exception("session-sync: failed to serialise event %r", key)
            return
        # SessionStore.append_event is synchronous and can fire from
        # NON-loop threads (Backgroundify-wrapped tools, thread-based
        # input modules, …).  asyncio.Queue.put_nowait is loop-local;
        # bouncing through call_soon_threadsafe keeps the queue and
        # waiting consumer correct under arbitrary call sites.
        try:
            self._loop.call_soon_threadsafe(self._enqueue, ("event", payload), key)
        except RuntimeError:  # pragma: no cover - loop closed during shutdown
            pass

    def _enqueue(self, item: tuple[str, dict[str, Any]], key: str) -> None:
        """Loop-local enqueue with drop-old-on-full back-pressure.

        ``item`` is a ``(wire_type, body)`` pair — ``wire_type`` is
        ``"event"`` for store events and ``"meta"`` for the one-shot
        meta snapshot.
        """
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
        # When notify fails we keep the failed item and retry with
        # bounded backoff until the link recovers — silently dropping it
        # would leave a permanent gap on the mirror (the worker's store
        # still has it, but no later trigger replays it).  Backoff caps
        # at 1s so a slow link doesn't cause unbounded latency on every
        # subsequent event once the link returns.
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
                        # First failure logs the full traceback so operators
                        # see what's going on; subsequent failures (the
                        # link is probably down) are summarised at debug to
                        # avoid log spam under sustained churn.
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
                        # Exponential-ish backoff bounded at 1s.
                        delay = min(0.01 * (2 ** min(consecutive_failures, 7)), 1.0)
                        await asyncio.sleep(delay)
        except asyncio.CancelledError:
            raise


# ---------------------------------------------------------------------------
# Consumer side — controller mirror
# ---------------------------------------------------------------------------


DEFAULT_MIRROR_MAX_OPEN_STORES = 64


class SessionMirrorWriter:
    """Subscribes to ``terrarium.session.sync`` events and writes a mirror.

    Install one per controller.  On each inbound ``event`` the writer
    opens (or reuses) a SessionStore at ``mirror_dir / <session_id>.kohakutr``
    and appends the event with the same agent / data the worker
    recorded.

    The writer is best-effort: if a SessionStore append fails, we log
    and continue.  No retry, no back-pressure to the worker.  The
    mirror is a read-side convenience; the worker's local store is
    authoritative.

    **Open-store cap.** A long-running controller seeing many session
    ids would otherwise leak SQLite + FTS handles indefinitely.  When
    the open store count exceeds ``max_open_stores`` (default 64),
    the *oldest* store (insertion-ordered via dict semantics) is
    closed before opening the new one.  Re-opening on the next event
    is cheap — it just costs one SQLite open + the bookkeeping in
    :class:`SessionStore.__init__`.
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
        """Close every mirror store and unregister.  Idempotent."""
        self._node.unregister_app_extension(NAMESPACE)
        for store in self._stores.values():
            try:
                store.close()
            except Exception:  # pragma: no cover - defensive
                logger.exception("session-sync: failed to close mirror store")
        self._stores.clear()

    def checkpoint(self, session_id: str) -> None:
        """Checkpoint an open mirror store so a raw byte read sees it all.

        Used by the resume route before it copies a worker session's
        mirror ``.kohakutr`` to push back to a worker — a live store's
        meta + recent events sit in a write cache / the ``-wal``
        sidecar until checkpointed. A no-op when the session isn't
        currently open (an evicted store was already checkpointed when
        the LRU closed it).
        """
        store = self._stores.get(session_id)
        if store is None:
            return
        try:
            store.checkpoint()
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "session-sync mirror: checkpoint failed for %s", session_id
            )

    def store_for(self, session_id: str) -> SessionStore:
        """Return the mirror store for ``session_id``, opening it lazily.

        Refreshes insertion order on cache hit so frequently-touched
        stores stay on the warm side of the LRU eviction.
        """
        existing = self._stores.pop(session_id, None)
        if existing is not None:
            self._stores[session_id] = existing  # move to "end" = most recent
            return existing
        # Evict oldest stores until we have room.
        while len(self._stores) >= self._max_open_stores:
            oldest_id, oldest_store = next(iter(self._stores.items()))
            self._stores.pop(oldest_id, None)
            try:
                oldest_store.close()
            except Exception:  # pragma: no cover - defensive
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

        The worker is authoritative for the session config — without
        this the mirror ``.kohakutr`` has no ``config_type`` /
        ``config_path`` / ``agents`` and a resume off the mirror fails.
        Mirror-only annotations (``on_node``, stamped per-event) are
        never in the worker snapshot, so iterating its keys can't
        clobber them.
        """
        session_id = body.get("session_id")
        meta = body.get("meta")
        if not isinstance(session_id, str) or not isinstance(meta, dict):
            return None
        try:
            store = self.store_for(session_id)
        except Exception:  # pragma: no cover - defensive
            logger.exception("session-sync mirror: store_for failed for %s", session_id)
            return None
        # Per-key writes: a failure on one key (e.g. KVault size cap on a
        # large config_snapshot) MUST NOT skip the remaining keys, else
        # the mirror ends up with a partial meta — typically agents/
        # format_version but no config_path/config_snapshot — and a
        # later resume fails with "no config_path or config_snapshot".
        for key, value in meta.items():
            try:
                store.meta[key] = value
            except Exception:  # pragma: no cover - defensive
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
            # Strip duplicated key/event_type from the payload; SessionStore
            # re-stamps these.  Keep everything else.
            payload = {k: v for k, v in data.items() if k not in ("type",)}
            store.append_event(agent, event_type, payload)
            # Stamp the originating worker once AFTER the event is
            # durably persisted — otherwise a partial-failure path
            # could leave a session advertised with ``node_id``
            # despite never having committed an event.  ``setdefault``
            # makes the write idempotent and avoids a TOCTOU under
            # concurrent dispatch.
            source_node = getattr(msg, "sender_node", "") or body.get("node_id", "")
            if source_node:
                store.meta.setdefault("on_node", source_node)
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "session-sync mirror: append failed for %s/%s", session_id, key
            )
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _agent_from_key(key: str) -> str:
    """SessionStore keys have the shape ``<agent>:e<seq>``."""
    if ":" not in key:
        return "unknown"
    return key.split(":", 1)[0]


def _json_safe(value: Any) -> Any:
    """Best-effort coercion so the body survives the kohakuvault packer.

    The packer rejects raw bytes — base64 those.  Other unknown types
    fall back to ``repr``.  Nested dicts / lists recurse.
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
