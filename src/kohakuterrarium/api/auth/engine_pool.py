"""Per-user :class:`Terrarium` instances with LRU eviction.

The pool is the ONE place where ``user_id`` selects which engine a
request operates against.  Below the pool, every component
(Studio, terrarium runtime, session store, …) is single-tenant — the
exact same code that runs in standalone mode.

Capacity policy:

- ``max_active`` — cap on the number of live engines.  Evicts the
  oldest when exceeded (LRU on the per-user ``_last_used`` clock).
- ``idle_timeout_s`` — engines untouched for this long are torn down
  by a periodic reaper task started when the pool is constructed.

The anonymous slot (``user_id is None``) is used when ``multi_user``
is off — every request shares one engine, preserving the standalone
boot behaviour.

Concurrency: ``get_or_create`` serialises engine construction with an
``asyncio.Lock`` so two concurrent requests for the same new user
build at most one engine.  Eviction runs under the same lock to
prevent racing a teardown against an in-flight request.
"""

import asyncio
import threading
import time
from pathlib import Path

from kohakuterrarium.terrarium import Terrarium
from kohakuterrarium.utils.config_dir import config_dir
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


_ANONYMOUS_KEY = "_anon"


def _user_session_dir(user_id: int | None) -> Path:
    """Resolve the on-disk session directory for ``user_id``.

    ``None`` → the shared ``<config_dir>/sessions/`` (standalone mode).
    Otherwise ``<config_dir>/users/<user_id>/sessions/``.
    """
    if user_id is None:
        return config_dir() / "sessions"
    return config_dir() / "users" / str(int(user_id)) / "sessions"


class EnginePool:
    """LRU-evicting registry of per-user :class:`Terrarium` engines.

    Build one at app startup, hand it to dependencies via
    ``app.state.engine_pool``, and tear it down in lifespan shutdown.
    """

    def __init__(
        self,
        *,
        max_active: int = 10,
        idle_timeout_s: int = 1800,
    ) -> None:
        self._max_active = max(1, int(max_active))
        self._idle_timeout_s = max(0, int(idle_timeout_s))
        self._engines: dict[str, Terrarium] = {}
        self._last_used: dict[str, float] = {}
        # Threading lock — sync get_or_create() can be called from
        # both sync and async code paths.  Engine construction is
        # bounded (a few hundred ms at most) so blocking briefly is
        # fine; an asyncio.Lock would force the entire dependency
        # graph onto async code.
        self._lock = threading.Lock()
        self._reaper_task: asyncio.Task | None = None
        # Stamped so tests can assert eviction order deterministically.
        self._monotonic = time.monotonic

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_or_create(self, user_id: int | None) -> Terrarium:
        """Return the (cached or freshly-built) engine for ``user_id``.

        Updates the per-user LRU timestamp on every call.  When the
        pool is at capacity, evicts the oldest entry before installing
        the new one.

        Synchronous — :func:`api.deps.get_service` and CLI code paths
        both need this path without awaiting.
        """
        key = self._key(user_id)
        engine_to_shut_down: Terrarium | None = None
        with self._lock:
            if key in self._engines:
                self._last_used[key] = self._monotonic()
                return self._engines[key]

            if len(self._engines) >= self._max_active:
                engine_to_shut_down = self._evict_oldest_locked()

            session_dir = _user_session_dir(user_id)
            session_dir.mkdir(parents=True, exist_ok=True)
            engine = Terrarium(session_dir=str(session_dir))
            self._engines[key] = engine
            self._last_used[key] = self._monotonic()
            logger.info(
                "engine_pool: spawned engine",
                user_id=user_id,
                session_dir=str(session_dir),
                live_count=len(self._engines),
            )
        # Shut down the evicted engine OUTSIDE the lock — shutdown
        # can be slow (closes sessions, joins background tasks) and
        # holding the lock would block every other engine request.
        if engine_to_shut_down is not None:
            _try_shutdown_sync(engine_to_shut_down)
        return engine

    def evict(self, user_id: int | None) -> bool:
        """Force-evict an engine.  Returns True if one was torn down."""
        key = self._key(user_id)
        with self._lock:
            engine = self._evict_key_locked(key)
        if engine is None:
            return False
        _try_shutdown_sync(engine)
        return True

    def evict_all(self) -> int:
        """Shut down every live engine.  Returns the count torn down.

        Synchronous — best-effort fire-and-forget for async engines
        (loop.create_task without await).  Use :meth:`evict_all_async`
        from FastAPI lifespan shutdown to actually wait for async
        teardown to complete; without that the loop closes before the
        engine's shutdown task drains, surfacing as "Task exception
        was never retrieved" warnings.
        """
        with self._lock:
            keys = list(self._engines)
            engines = [self._evict_key_locked(k) for k in keys]
        for engine in engines:
            if engine is not None:
                _try_shutdown_sync(engine)
        return len(keys)

    async def evict_all_async(self) -> int:
        """Async variant — awaits each engine's ``shutdown()``
        coroutine.  Preferred from lifespan shutdown so file handles
        and background tasks finish closing before the loop tears
        down.
        """
        with self._lock:
            keys = list(self._engines)
            engines = [self._evict_key_locked(k) for k in keys]
        for engine in engines:
            if engine is not None:
                await _try_shutdown_async(engine)
        return len(keys)

    async def start_reaper(self) -> None:
        """Spawn the periodic idle-eviction task.

        Called once at app startup.  Idempotent — calling twice is a
        no-op.
        """
        if self._reaper_task is not None and not self._reaper_task.done():
            return
        if self._idle_timeout_s <= 0:
            return  # disabled
        self._reaper_task = asyncio.create_task(self._run_reaper())

    async def stop_reaper(self) -> None:
        if self._reaper_task is None:
            return
        self._reaper_task.cancel()
        try:
            await self._reaper_task
        except (asyncio.CancelledError, Exception):
            pass
        self._reaper_task = None

    def live_user_ids(self) -> list[int | None]:
        """Snapshot of currently-pooled user ids.  Diagnostic only."""
        return [None if k == _ANONYMOUS_KEY else int(k) for k in self._engines]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _key(self, user_id: int | None) -> str:
        if user_id is None:
            return _ANONYMOUS_KEY
        return str(int(user_id))

    def _evict_oldest_locked(self) -> Terrarium | None:
        """Pop the LRU entry from the dict.  Caller shuts it down
        OUTSIDE the lock."""
        if not self._engines:
            return None
        oldest_key = min(self._last_used, key=self._last_used.get)
        return self._evict_key_locked(oldest_key)

    def _evict_key_locked(self, key: str) -> Terrarium | None:
        engine = self._engines.pop(key, None)
        self._last_used.pop(key, None)
        if engine is not None:
            logger.info(
                "engine_pool: evicted engine",
                key=key,
                live_count=len(self._engines),
            )
        return engine

    async def _run_reaper(self) -> None:  # pragma: no cover - sleep-bounded loop body
        """Periodic sweep of idle engines.

        Wakes once per ``idle_timeout_s / 2`` (min 30s) so eviction
        latency is at most half the configured timeout.  Cancelled
        cleanly on shutdown.  The loop body is excluded from coverage
        because driving it deterministically requires either
        time-mocking the asyncio sleep or running for the full
        interval — neither warrants the test complexity.  The
        eviction primitives it composes (``_evict_key_locked``,
        ``_try_shutdown_sync``) are independently tested.
        """
        interval = max(30, self._idle_timeout_s // 2)
        try:
            while True:
                await asyncio.sleep(interval)
                cutoff = self._monotonic() - self._idle_timeout_s
                # Collect stale keys + the engines to teardown under
                # the lock, then run shutdowns outside.
                to_shutdown: list[Terrarium] = []
                with self._lock:
                    stale = [k for k, t in self._last_used.items() if t < cutoff]
                    for key in stale:
                        engine = self._evict_key_locked(key)
                        if engine is not None:
                            to_shutdown.append(engine)
                for engine in to_shutdown:
                    _try_shutdown_sync(engine)
        except asyncio.CancelledError:
            raise


def _try_shutdown_sync(engine: Terrarium) -> None:
    """Best-effort engine shutdown — engines may expose async or sync
    ``shutdown``.  We don't propagate exceptions because shutdown is
    a cleanup path; a crash here shouldn't cascade.
    """
    shutdown = getattr(engine, "shutdown", None)
    if shutdown is None:  # pragma: no cover - production Terrarium always has shutdown
        return
    try:
        result = shutdown()
        if asyncio.iscoroutine(
            result
        ):  # pragma: no cover - timing-dependent path; covered by integration runs
            # Engine shutdown is async — schedule on a loop.
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(result)
            except RuntimeError:
                # No running loop — drive to completion synchronously.
                # ``asyncio.run`` would conflict with a parent loop if
                # there were one; we already checked.
                asyncio.run(result)
    except Exception:  # pragma: no cover - defensive
        logger.exception("engine_pool: shutdown raised")


async def _try_shutdown_async(engine: Terrarium) -> None:
    """Async variant — actually awaits async ``shutdown`` coroutines.

    Use this from FastAPI lifespan shutdown so the engine's teardown
    completes before the event loop closes.  The sync variant uses
    ``create_task`` and returns immediately, which is fine for the
    LRU-eviction hot path (we don't want to block the next
    ``get_or_create`` on shutdown latency) but wrong at lifespan
    shutdown where we DO want to wait.
    """
    shutdown = getattr(engine, "shutdown", None)
    if shutdown is None:  # pragma: no cover - production Terrarium always has shutdown
        return
    try:
        result = shutdown()
        if asyncio.iscoroutine(result):
            await result
    except Exception:  # pragma: no cover - defensive
        logger.exception("engine_pool: async shutdown raised")


__all__ = ["EnginePool", "_user_session_dir"]
