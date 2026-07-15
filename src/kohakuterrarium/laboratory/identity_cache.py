"""Worker-side identity cache for the hybrid identity model.

Workers resolve identity data locally first, then fetch it from the
controller and cache it with type-specific TTLs. Concurrent requests for
the same record share one underlying fetch.
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

# Short key TTLs propagate revocation quickly; profiles and MCP configs change
# less frequently and can remain cached longer.
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
    """Resolve and cache worker identity data from local files or host RPCs.

    Async getters coalesce cache misses by key. Synchronous consumers must
    prefetch and then use the non-blocking lookup methods.
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
        # A host has one ChatGPT subscription, so Codex tokens use a fixed key.
        self._codex: dict[str, _Entry] = {}
        # Weak references discard per-key locks after all concurrent waiters
        # release them, preventing growth from one-time cache keys.
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
        """Return Codex OAuth tokens cached with the API-key TTL."""
        return await self._fetch(
            "codex", "_singleton", self._codex, self._key_ttl, self._fetch_codex
        )

    def sync_codex_tokens(self):
        """Return worker-local or cached Codex tokens without blocking.

        Worker-local tokens take precedence because OAuth refresh state is
        process-scoped and a host token can cause refresh mismatches.
        """
        # Reading the file directly avoids re-entering this registered resolver.
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
        """Warm the Codex token cache, ignoring missing credentials."""
        try:
            await self.get_codex_token()
        except IdentityNotFound:
            pass
        except Exception:  # pragma: no cover - defensive
            logger.warning("prefetch_for_codex_if_needed failed", exc_info=True)

    def sync_api_key(self, provider: str) -> str:
        """Return a worker-local or cached API key without blocking.

        Local credentials take precedence. An empty string indicates a miss.
        """
        # The private loader honors KT_CONFIG_DIR without re-entering this
        # registered resolver through the public accessor.
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
        """Warm one provider's cache, ignoring missing credentials."""
        try:
            await self.get_api_key(provider)
        except IdentityNotFound:
            # A creature may obtain credentials elsewhere or not require them.
            pass
        except Exception:  # pragma: no cover - defensive
            logger.warning(
                "prefetch_for_provider failed",
                extra={"provider": provider},
                exc_info=True,
            )

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
        # The local strong reference keeps this weakly stored lock alive while
        # all concurrent waiters share it.
        lock = self._locks.get(lock_key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[lock_key] = lock
        async with lock:
            # Another task may have populated the entry while this task waited.
            now = time.monotonic()
            entry = cache.get(key)
            if entry is not None and entry.expires_at > now:
                return entry.value
            value = await fetcher(key)
            cache[key] = _Entry(value, time.monotonic() + ttl)
            return value

    async def _fetch_api_key(self, provider: str) -> str:
        # Worker-local credentials intentionally override host credentials.
        local = _read_local_api_key(provider)
        if local:
            return local
        body = await self._request("get_api_key", {"provider": provider})
        return body["key"]

    async def _fetch_profile(self, name: str) -> dict[str, Any]:
        # Worker-local profiles intentionally override host profiles.
        local = _read_local_profile(name)
        if local is not None:
            return local
        body = await self._request("get_profile", {"name": name})
        return body["profile"]

    async def _fetch_mcp(self, name: str) -> dict[str, Any]:
        body = await self._request("get_mcp_server", {"name": name})
        return body["server"]

    async def _fetch_codex(self, _key: str) -> dict[str, Any]:
        # Process-bound worker tokens cannot safely be replaced by host tokens.
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


def _read_local_api_key(provider: str) -> str:
    """Read a worker-local API key without invoking the resolver."""
    keys = _load_api_keys()
    if provider in keys and keys[provider]:
        return keys[provider]
    env_var = PROVIDER_KEY_MAP.get(provider, "")
    if env_var:
        return os.environ.get(env_var, "")
    return ""


def _read_local_codex_tokens():
    """Read worker-local Codex tokens without invoking the resolver."""
    tokens = CodexTokens.load(path=_default_token_path())
    if tokens is not None and tokens.access_token:
        return tokens
    return None


def _read_local_profile(name: str) -> dict[str, Any] | None:
    """Return a worker-local profile as a wire dictionary, if present."""
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
