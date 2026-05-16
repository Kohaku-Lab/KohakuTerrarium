"""Worker-side identity cache for the hybrid identity model.

A worker that runs creatures on behalf of a controller needs to make
LLM calls.  Those calls need API keys, profile bodies, and MCP server
configs.  Rather than each worker maintaining its own identity files,
the worker fetches identity from the controller (the source of truth)
and caches with a short TTL.

Per ``management-wiring.md``:

- API keys → pulled on demand (sensitive; want quick invalidation)
- LLM profiles + MCP server configs → cached with longer TTL
- All caches honour broadcast invalidations (future unit)

The cache is async-safe via a single lock; concurrent requests for
the same record coalesce into one underlying fetch.
"""

import asyncio
import os
import time
import weakref
from dataclasses import asdict
from typing import Any

from kohakuterrarium.laboratory.protocols import LabSender
from kohakuterrarium.llm.api_keys import (
    PROVIDER_KEY_MAP,
    _load_api_keys,
)
from kohakuterrarium.llm.codex_auth import CodexTokens, _default_token_path
from kohakuterrarium.llm.profiles import get_profile as _local_get_profile
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


HOST_NODE = "_host"
NAMESPACE = "studio.identity"

# TTLs are deliberately tight for keys (revocation must propagate
# quickly) and looser for profiles / MCP configs (rarely change at
# runtime).  Configurable per-instance.
DEFAULT_KEY_TTL_SECONDS = 30.0
DEFAULT_PROFILE_TTL_SECONDS = 300.0
DEFAULT_MCP_TTL_SECONDS = 300.0


class IdentityNotFound(KeyError):
    """Raised when the controller has no matching identity record."""


class _Entry:
    __slots__ = ("value", "expires_at")

    def __init__(self, value: Any, expires_at: float) -> None:
        self.value = value
        self.expires_at = expires_at


class IdentityCache:
    """TTL-bounded cache over ``studio.identity`` RPCs.

    All getters are async.  Cache hits return immediately; misses or
    expired entries trigger a fetch from the host.  Concurrent callers
    requesting the same record share one inflight fetch via a per-key
    asyncio.Lock.

    For sync consumers (``llm.api_keys.get_api_key`` is sync), see
    :meth:`sync_api_key` — it does a non-blocking dict lookup against
    the cache's own internal store, never blocks on a fetch.  The
    expected pattern: pre-fetch keys async at spawn time (via
    :meth:`prefetch_for_provider`), then the sync agent code finds
    them in the cache.
    """

    def __init__(
        self,
        sender: LabSender,
        *,
        host_node: str = HOST_NODE,
        request_timeout: float = 5.0,
        key_ttl: float = DEFAULT_KEY_TTL_SECONDS,
        profile_ttl: float = DEFAULT_PROFILE_TTL_SECONDS,
        mcp_ttl: float = DEFAULT_MCP_TTL_SECONDS,
    ) -> None:
        self._sender = sender
        self._host = host_node
        self._timeout = request_timeout
        self._key_ttl = key_ttl
        self._profile_ttl = profile_ttl
        self._mcp_ttl = mcp_ttl
        self._keys: dict[str, _Entry] = {}
        self._profiles: dict[str, _Entry] = {}
        self._mcp: dict[str, _Entry] = {}
        # Codex tokens are a single-record cache (one host == one
        # ChatGPT subscription).  Stored under a fixed sentinel key.
        self._codex: dict[str, _Entry] = {}
        # Per-fetch coalescing locks.  WeakValueDictionary lets a lock
        # be GC'd once every concurrent waiter has released — prevents
        # an unbounded build-up of dead locks for unique keys over the
        # cache's lifetime.
        self._locks: "weakref.WeakValueDictionary[str, asyncio.Lock]" = (
            weakref.WeakValueDictionary()
        )

    async def get_api_key(self, provider: str) -> str:
        return await self._fetch(
            "key", provider, self._keys, self._key_ttl, self._fetch_api_key
        )

    async def get_profile(self, name: str) -> dict[str, Any]:
        return await self._fetch(
            "profile",
            name,
            self._profiles,
            self._profile_ttl,
            self._fetch_profile,
        )

    async def get_mcp_server(self, name: str) -> dict[str, Any]:
        return await self._fetch("mcp", name, self._mcp, self._mcp_ttl, self._fetch_mcp)

    async def get_codex_token(self) -> dict[str, Any]:
        """Fetch the host's Codex OAuth tokens (cached briefly).

        Same TTL as API keys — codex tokens revoke / refresh on a similar
        cadence and a stale local copy must not outlive the host's view.
        """
        return await self._fetch(
            "codex", "_singleton", self._codex, self._key_ttl, self._fetch_codex
        )

    def sync_codex_tokens(self):
        """Non-blocking lookup for Codex tokens.

        **Local-first**: try the worker's own
        ``<config_dir>/codex-auth.json`` (or the Codex CLI cache)
        before falling back to whatever the host most recently shared.
        Codex tokens are process-scoped — the host's token belongs to
        the host's OAuth session and routing it through a worker
        invariably fails with a refresh-mismatch.  A worker with its
        OWN ``kt login codex`` (or the system Codex CLI) MUST use that
        token, not the host's.

        Returns a :class:`kohakuterrarium.llm.codex_auth.CodexTokens`
        instance (or ``None`` on miss).
        """
        # Local-first: read the worker's file directly, bypassing the
        # registered resolver (which would loop back into this method).
        local = _read_local_codex_tokens()
        if local is not None:
            return local
        entry = self._codex.get("_singleton")
        if entry is None:
            return None
        if entry.expires_at <= time.monotonic():
            return None
        data = entry.value
        if not isinstance(data, dict) or not data.get("access_token"):
            return None
        return CodexTokens(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", ""),
            expires_at=data.get("expires_at", 0),
            id_token=data.get("id_token", ""),
            account_id=data.get("account_id", ""),
        )

    async def prefetch_for_codex_if_needed(self) -> None:
        """Async-warm the Codex token cache.  Silent on miss."""
        try:
            await self.get_codex_token()
        except IdentityNotFound:
            pass
        except Exception:  # pragma: no cover - defensive
            logger.debug("prefetch_for_codex_if_needed failed")

    def sync_api_key(self, provider: str) -> str:
        """Non-blocking lookup for an API key.

        **Local-first**: try the worker's own ``api_keys.yaml`` (and
        its env-var fallback) before falling back to the host-fetched
        cache.  Workers that have their own provider credentials
        (typical when ``--home-dir`` points at a real config dir)
        should use them rather than receiving the host's keys.

        Returns ``""`` when neither local nor host caches have an
        entry.  Used by :func:`llm.api_keys.get_api_key` via the
        registered resolver — must not await.
        """
        # Local-first: read the worker's own api_keys.yaml directly.
        # ``_load_api_keys`` is a sync file-read that honours
        # ``KT_CONFIG_DIR``; bypassing the public ``get_api_key`` here
        # avoids re-entering the resolver chain (which would loop
        # back into this method).
        local = _read_local_api_key(provider)
        if local:
            return local
        entry = self._keys.get(provider)
        if entry is None:
            return ""
        if entry.expires_at <= time.monotonic():
            return ""
        return entry.value if isinstance(entry.value, str) else ""

    async def prefetch_for_provider(self, provider: str) -> None:
        """Async-warm the cache for ``provider``.  Silent on miss."""
        try:
            await self.get_api_key(provider)
        except IdentityNotFound:
            # Worker may not need this key (creature picks up a key
            # from elsewhere, or the LLM call is non-authed).
            pass
        except Exception:  # pragma: no cover - defensive
            logger.debug("prefetch_for_provider failed", extra={"provider": provider})

    def invalidate(self, *, kind: str, name: str | None = None) -> None:
        """Drop cache entries.

        ``kind`` is ``"key" | "profile" | "mcp" | "all"``.  ``name``
        when set restricts the drop to one entry; otherwise the whole
        kind is cleared.
        """
        if kind == "all":
            self._keys.clear()
            self._profiles.clear()
            self._mcp.clear()
            self._codex.clear()
            return
        target = {
            "key": self._keys,
            "profile": self._profiles,
            "mcp": self._mcp,
            "codex": self._codex,
        }.get(kind)
        if target is None:
            raise ValueError(f"unknown kind {kind!r}")
        if name is None:
            target.clear()
        else:
            target.pop(name, None)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _fetch(
        self,
        kind: str,
        key: str,
        cache: dict[str, _Entry],
        ttl: float,
        fetcher,
    ) -> Any:
        now = time.monotonic()
        entry = cache.get(key)
        if entry is not None and entry.expires_at > now:
            return entry.value
        lock_key = f"{kind}:{key}"
        # WeakValueDictionary doesn't have setdefault; do an
        # explicit get-or-insert so concurrent waiters see the same
        # lock for the duration of the fetch.  The local ``lock``
        # variable keeps it alive while we await; once every awaiter
        # releases, GC drops it from the WeakValueDictionary.
        lock = self._locks.get(lock_key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[lock_key] = lock
        async with lock:
            # Re-check after acquiring the lock — another task may have
            # populated the entry while we were waiting.
            now = time.monotonic()
            entry = cache.get(key)
            if entry is not None and entry.expires_at > now:
                return entry.value
            value = await fetcher(key)
            cache[key] = _Entry(value, time.monotonic() + ttl)
            return value

    async def _fetch_api_key(self, provider: str) -> str:
        # Local-first: worker's own api_keys.yaml + env wins over host.
        local = _read_local_api_key(provider)
        if local:
            return local
        body = await self._request("get_api_key", {"provider": provider})
        return body["key"]

    async def _fetch_profile(self, name: str) -> dict[str, Any]:
        # Local-first: worker's own llm_profiles.json wins over host.
        local = _read_local_profile(name)
        if local is not None:
            return local
        body = await self._request("get_profile", {"name": name})
        return body["profile"]

    async def _fetch_mcp(self, name: str) -> dict[str, Any]:
        body = await self._request("get_mcp_server", {"name": name})
        return body["server"]

    async def _fetch_codex(self, _key: str) -> dict[str, Any]:
        # Local-first: worker's own codex-auth.json (or Codex CLI
        # cache) wins over the host's token.  Codex tokens are
        # process-bound and the host's are NOT interchangeable.
        local = _read_local_codex_tokens()
        if local is not None:
            return {
                "access_token": local.access_token,
                "refresh_token": local.refresh_token,
                "expires_at": local.expires_at,
                "id_token": local.id_token,
                "account_id": local.account_id,
            }
        body = await self._request("get_codex_token", {})
        return body["tokens"]

    async def _request(self, type_: str, body: dict[str, Any]) -> dict[str, Any]:
        resp = await self._sender.request(
            to_node=self._host,
            namespace=NAMESPACE,
            type=type_,
            body=body,
            timeout=self._timeout,
        )
        if isinstance(resp, dict) and "error" in resp:
            err = resp["error"]
            kind = err.get("kind", "unknown")
            msg = err.get("message", "")
            if kind == "not_found":
                raise IdentityNotFound(msg)
            if kind == "invalid":
                raise ValueError(msg)
            raise RuntimeError(f"{kind}: {msg}")
        return resp


# ---------------------------------------------------------------------------
# Local-file readers — used by every "local-first" branch above.  Each
# one reads the SAME file the standalone Studio reads (honouring
# ``KT_CONFIG_DIR`` / ``--home-dir``) but bypasses the public sync
# accessor so we don't loop back into our own resolver.
# ---------------------------------------------------------------------------


def _read_local_api_key(provider: str) -> str:
    """Worker-local api_keys.yaml + env fallback, no resolver."""
    keys = _load_api_keys()
    if provider in keys and keys[provider]:
        return keys[provider]
    env_var = PROVIDER_KEY_MAP.get(provider, "")
    if env_var:
        return os.environ.get(env_var, "")
    return ""


def _read_local_codex_tokens():
    """Worker-local codex-auth.json (or Codex CLI cache), no resolver.

    Bypasses :func:`CodexTokens.load`'s resolver branch by passing the
    default path explicitly.
    """
    tokens = CodexTokens.load(path=_default_token_path())
    if tokens is not None and tokens.access_token:
        return tokens
    return None


def _read_local_profile(name: str) -> dict[str, Any] | None:
    """Worker-local llm_profiles.json, as a wire dict.  None on miss."""
    profile = _local_get_profile(name)
    if profile is None:
        return None
    try:
        return asdict(profile)
    except TypeError:  # pragma: no cover - defensive
        return None


__all__ = [
    "DEFAULT_KEY_TTL_SECONDS",
    "DEFAULT_MCP_TTL_SECONDS",
    "DEFAULT_PROFILE_TTL_SECONDS",
    "IdentityCache",
    "IdentityNotFound",
]
