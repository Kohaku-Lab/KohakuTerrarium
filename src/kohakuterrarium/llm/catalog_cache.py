"""Process-wide TTL cache in front of :func:`discover_models`.

Each backend keeps one entry keyed by a digest of what changes its catalog:
config root, backend type, base URL, key variable, and a hash of the
credential or login identity. Secrets are hashed, never stored. Concurrent
misses share one fetch; failures are not cached.
"""

import asyncio
import hashlib
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from kohakuterrarium.llm import discovery
from kohakuterrarium.llm.codex.auth import CodexTokens
from kohakuterrarium.llm.codex.claims import account_id_from_id_token
from kohakuterrarium.llm.grok.auth import GrokTokens
from kohakuterrarium.llm.profiles.api_keys import backend_api_key
from kohakuterrarium.llm.profiles.backends import load_backends
from kohakuterrarium.llm.profiles.types import LLMBackend
from kohakuterrarium.utils.config_dir import config_dir
from kohakuterrarium.utils.env_interp import interpolate_env_vars

DISCOVERY_TTL_SECONDS = 600.0


@dataclass(frozen=True)
class CatalogSnapshot:
    """One provider catalog and the epoch second it was read upstream."""

    models: tuple[discovery.DiscoveredModel, ...]
    fetched_at: float

    @property
    def fetched_at_iso(self) -> str:
        return datetime.fromtimestamp(self.fetched_at, timezone.utc).isoformat()


def _digest(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


def _codex_identity() -> str:
    tokens = CodexTokens.load()
    if tokens is None:
        return ""
    account = tokens.account_id or account_id_from_id_token(tokens.id_token)
    return _digest("account", account) if account else _digest(tokens.refresh_token)


def _grok_identity() -> str:
    return _digest(*(t.fingerprint for t in GrokTokens.load_bootstrap_candidates()))


def backend_fingerprint(backend: LLMBackend) -> str:
    """Digest of every input that changes ``backend``'s catalog (blocking)."""
    key = backend_api_key(backend.name, backend.api_key_env)
    match backend.backend_type:
        case "codex":
            login = _codex_identity()
        case "grok-subscription":
            login = _grok_identity()
        case _:
            login = ""
    return _digest(
        str(config_dir()),
        backend.backend_type,
        interpolate_env_vars(backend.base_url) or "",
        backend.api_key_env or "",
        _digest(key) if key else "",
        login,
    )


def _fingerprint_of(name: str) -> str | None:
    backend = load_backends().get(name)
    return None if backend is None else backend_fingerprint(backend)


async def _fetch_upstream(name, *, transport=None):
    return await discovery.discover_models(name, transport=transport)


class DiscoveryCache:
    """TTL + single-flight cache of provider catalogs, one entry per backend."""

    def __init__(
        self,
        *,
        ttl: float = DISCOVERY_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
        fetch: Callable[..., Awaitable[list]] = _fetch_upstream,
        fingerprint: Callable[[str], str | None] = _fingerprint_of,
    ) -> None:
        self._ttl = ttl
        self._clock = clock
        self._fetch = fetch
        self._fingerprint = fingerprint
        self._entries: dict[str, tuple[str, CatalogSnapshot]] = {}
        self._flights: dict[tuple[str, str], asyncio.Task] = {}
        self._generations: dict[str, int] = {}
        self._lock = threading.Lock()

    async def get(
        self,
        name: str,
        *,
        refresh: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> CatalogSnapshot:
        """Return ``name``'s catalog, fetching on miss, expiry or ``refresh``."""
        fingerprint = await asyncio.to_thread(self._fingerprint, name)
        if fingerprint is None:
            models = await self._fetch(name, transport=transport)
            return CatalogSnapshot(tuple(models), self._clock())
        key = (name, fingerprint)
        with self._lock:
            cached = self._entries.get(name)
            if (
                not refresh
                and cached is not None
                and cached[0] == fingerprint
                and 0 <= self._clock() - cached[1].fetched_at < self._ttl
            ):
                return cached[1]
            flight = self._flights.get(key)
            if flight is None or flight.get_loop() is not asyncio.get_running_loop():
                generation = self._generations.get(name, 0)
                flight = asyncio.ensure_future(
                    self._load(name, fingerprint, generation, transport)
                )
                flight.add_done_callback(lambda done: self._land(key, done))
                self._flights[key] = flight
        return await asyncio.shield(flight)

    async def _load(self, name, fingerprint, generation, transport):
        models = await self._fetch(name, transport=transport)
        snapshot = CatalogSnapshot(tuple(models), self._clock())
        with self._lock:
            if self._generations.get(name, 0) == generation:
                self._entries[name] = (fingerprint, snapshot)
        return snapshot

    def _land(self, key, done: asyncio.Task) -> None:
        with self._lock:
            if self._flights.get(key) is done:
                del self._flights[key]
        if not done.cancelled():
            done.exception()

    def invalidate(self, name: str | None = None) -> None:
        """Drop ``name``'s entry (every entry when ``None``) and detach its fetches.

        Thread-safe: settings writers call it from ``asyncio.to_thread``.
        """
        with self._lock:
            if name is None:
                names = {*self._entries, *self._generations}
                names.update(key[0] for key in self._flights)
            else:
                names = {name}
            for each in names:
                self._entries.pop(each, None)
                self._generations[each] = self._generations.get(each, 0) + 1
            for key in [key for key in self._flights if key[0] in names]:
                del self._flights[key]


_cache = DiscoveryCache()


async def cached_discover_models(
    name: str,
    *,
    refresh: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
) -> CatalogSnapshot:
    """Read ``name``'s catalog through the process-wide cache."""
    return await _cache.get(name, refresh=refresh, transport=transport)


def invalidate_discovery(name: str | None = None) -> None:
    """Forget the cached catalog of ``name``, or of every backend."""
    _cache.invalidate(name)
