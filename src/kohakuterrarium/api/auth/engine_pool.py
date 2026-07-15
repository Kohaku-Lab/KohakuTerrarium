"""Map user identities to isolated Terrarium engines with bounded retention.

The pool is the tenancy boundary; downstream Studio, Terrarium, and session code
remains single-tenant. Capacity uses LRU eviction, idle engines are reaped, and the
anonymous key preserves a shared-engine slot. A shared lock serializes construction
and registry mutation, while slow shutdown work runs after releasing the lock.
"""

import asyncio
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from kohakuterrarium.terrarium import Terrarium
from kohakuterrarium.utils.config_dir import config_dir
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


_ANONYMOUS_KEY = "_anon"


def _user_session_dir(user_id: int | None) -> Path:
    """Return the shared session directory or the user's isolated directory."""
    if user_id is None:
        return config_dir() / "sessions"
    return config_dir() / "users" / str(int(user_id)) / "sessions"


class EnginePool:
    """Own per-user Terrarium instances from app startup through shutdown."""

    def __init__(
        self,
        *,
        max_active: int = 10,
        idle_timeout_s: int = 1800,
        drive_resolver: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._max_active = max(1, int(max_active))
        self._idle_timeout_s = max(0, int(idle_timeout_s))
        # Resolve a fresh immutable Drive policy for each engine so users never
        # share registration or configuration instances.
        self._drive_resolver = drive_resolver
        self._engines: dict[str, Terrarium] = {}
        self._last_used: dict[str, float] = {}
        # A threading lock supports synchronous dependency and CLI callers without
        # forcing the service-resolution graph to become asynchronous.
        self._lock = threading.Lock()
        self._reaper_task: asyncio.Task | None = None
        # The clock is replaceable so recency ordering can be deterministic.
        self._monotonic = time.monotonic

    # Engine lookup and lifecycle operations.

    def get_or_create(self, user_id: int | None) -> Terrarium:
        """Return the user's engine, touching LRU state and evicting at capacity."""
        key = self._key(user_id)
        engine_to_shut_down: Terrarium | None = None
        with self._lock:
            if key in self._engines:
                # Reinsert recency state so dict order breaks equal-clock ties in LRU
                # order; monotonic timestamps can repeat on some platforms.
                del self._last_used[key]
                self._last_used[key] = self._monotonic()
                return self._engines[key]

            if len(self._engines) >= self._max_active:
                engine_to_shut_down = self._evict_oldest_locked()

            session_dir = _user_session_dir(user_id)
            session_dir.mkdir(parents=True, exist_ok=True)
            drive_kwargs = self._drive_resolver() if self._drive_resolver else {}
            engine = Terrarium(session_dir=str(session_dir), **drive_kwargs)
            self._engines[key] = engine
            self._last_used[key] = self._monotonic()
            logger.info(
                "engine_pool: spawned engine",
                user_id=user_id,
                session_dir=str(session_dir),
                live_count=len(self._engines),
            )
        # Shutdown may close sessions and join tasks, so it must not hold the pool lock.
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

    def evict_others(self, keep: Terrarium | None) -> list[int | None]:
        """Evict all engines except ``keep`` so they rebuild with current policy.

        The returned identifiers include ``None`` for the anonymous shared slot.
        """
        with self._lock:
            victims = [k for k, e in self._engines.items() if e is not keep]
            engines = [self._evict_key_locked(k) for k in victims]
        for engine in engines:
            if engine is not None:
                _try_shutdown_sync(engine)
        return [None if k == _ANONYMOUS_KEY else int(k) for k in victims]

    def evict_all(self) -> int:
        """Remove every engine and start best-effort shutdown without awaiting it."""
        with self._lock:
            keys = list(self._engines)
            engines = [self._evict_key_locked(k) for k in keys]
        for engine in engines:
            if engine is not None:
                _try_shutdown_sync(engine)
        return len(keys)

    async def evict_all_async(self) -> int:
        """Remove every engine and await shutdown before the event loop closes."""
        with self._lock:
            keys = list(self._engines)
            engines = [self._evict_key_locked(k) for k in keys]
        for engine in engines:
            if engine is not None:
                await _try_shutdown_async(engine)
        return len(keys)

    async def start_reaper(self) -> None:
        """Start the idempotent idle-engine reaper when idle expiry is enabled."""
        if self._reaper_task is not None and not self._reaper_task.done():
            return
        if self._idle_timeout_s <= 0:
            return  # A non-positive timeout disables idle eviction.
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

    # Locked registry primitives and idle reaping.

    def _key(self, user_id: int | None) -> str:
        if user_id is None:
            return _ANONYMOUS_KEY
        return str(int(user_id))

    def _evict_oldest_locked(self) -> Terrarium | None:
        """Remove the least-recently-used engine; the caller shuts it down unlocked."""
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
        """Sweep idle engines at half-timeout intervals with a 30-second floor."""
        interval = max(30, self._idle_timeout_s // 2)
        try:
            while True:
                await asyncio.sleep(interval)
                cutoff = self._monotonic() - self._idle_timeout_s
                # Registry removal is atomic under the lock; shutdown remains unlocked.
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
    """Start sync or async engine shutdown without propagating cleanup failures."""
    shutdown = getattr(engine, "shutdown", None)
    if shutdown is None:  # pragma: no cover - production Terrarium always has shutdown
        return
    try:
        result = shutdown()
        if asyncio.iscoroutine(
            result
        ):  # pragma: no cover - timing-dependent path; covered by integration runs
            # Preserve non-blocking eviction when an event loop is already running.
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(result)
            except RuntimeError:
                # Without a running loop, shutdown must complete synchronously.
                asyncio.run(result)
    except Exception:  # pragma: no cover - defensive
        logger.exception("engine_pool: shutdown raised")


async def _try_shutdown_async(engine: Terrarium) -> None:
    """Await engine shutdown so lifecycle teardown completes before loop closure."""
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
