"""Unit tests for :mod:`kohakuterrarium.laboratory.identity_cache`."""

import asyncio

import pytest

from kohakuterrarium.laboratory.identity_cache import (
    DEFAULT_KEY_TTL_SECONDS,
    IdentityCache,
    IdentityNotFound,
)


class _FakeSender:
    def __init__(self, responses=None, error=None, count_request=False):
        self.responses = responses or {}
        self.error = error
        self.calls = []
        self.count_request = count_request

    async def request(self, *, to_node, namespace, type, body, timeout):
        self.calls.append((to_node, namespace, type, body))
        if self.error:
            return {"error": self.error}
        return self.responses.get(type)


# ── get_api_key ──────────────────────────────────────────────────


class TestGetApiKey:
    async def test_fetches_and_caches(self):
        sender = _FakeSender(responses={"get_api_key": {"key": "sk-abc"}})
        c = IdentityCache(sender)
        out = await c.get_api_key("openai")
        assert out == "sk-abc"
        # Second call uses cache.
        out2 = await c.get_api_key("openai")
        assert out2 == "sk-abc"
        # Only one underlying request.
        assert len(sender.calls) == 1

    async def test_concurrent_fetch_coalesces(self):
        sender = _FakeSender(responses={"get_api_key": {"key": "sk"}})
        c = IdentityCache(sender)
        # Run several concurrent fetches.
        results = await asyncio.gather(
            c.get_api_key("p"),
            c.get_api_key("p"),
            c.get_api_key("p"),
        )
        assert results == ["sk", "sk", "sk"]
        # Only one wire request.
        assert len(sender.calls) == 1

    async def test_lock_re_check_serves_waiters_from_cache(self):
        # A genuinely slow fetch: while the first task holds the lock and
        # awaits the wire, the other two block on the lock. When they
        # acquire it they must hit the post-lock re-check and return the
        # now-cached value WITHOUT a second wire request.
        gate = asyncio.Event()

        class _SlowSender:
            def __init__(self):
                self.calls = 0

            async def request(self, *, to_node, namespace, type, body, timeout):
                self.calls += 1
                await gate.wait()
                return {"key": "sk-slow"}

        sender = _SlowSender()
        c = IdentityCache(sender)
        first = asyncio.create_task(c.get_api_key("p"))
        # Let ``first`` reach ``await fetcher`` and take the lock.
        await asyncio.sleep(0.02)
        second = asyncio.create_task(c.get_api_key("p"))
        third = asyncio.create_task(c.get_api_key("p"))
        await asyncio.sleep(0.02)
        # Release the wire — first completes, then the queued waiters
        # acquire the lock and serve from the freshly-populated cache.
        gate.set()
        results = await asyncio.gather(first, second, third)
        assert results == ["sk-slow", "sk-slow", "sk-slow"]
        # Exactly one wire request despite three concurrent callers.
        assert sender.calls == 1

    async def test_not_found_raises(self):
        sender = _FakeSender(error={"kind": "not_found", "message": "no key"})
        c = IdentityCache(sender)
        with pytest.raises(IdentityNotFound):
            await c.get_api_key("openai")

    async def test_invalid_error_raises_value_error(self):
        sender = _FakeSender(error={"kind": "invalid", "message": "bad"})
        c = IdentityCache(sender)
        with pytest.raises(ValueError):
            await c.get_api_key("openai")

    async def test_unknown_error_raises_runtime(self):
        sender = _FakeSender(error={"kind": "boom", "message": "x"})
        c = IdentityCache(sender)
        with pytest.raises(RuntimeError):
            await c.get_api_key("openai")


# ── get_profile / get_mcp_server ────────────────────────────────


class TestGetProfileAndMCP:
    async def test_get_profile(self):
        sender = _FakeSender(
            responses={"get_profile": {"profile": {"name": "p", "model": "x"}}}
        )
        c = IdentityCache(sender)
        out = await c.get_profile("p")
        assert out == {"name": "p", "model": "x"}

    async def test_get_mcp_server(self):
        sender = _FakeSender(
            responses={"get_mcp_server": {"server": {"name": "s", "url": "u"}}}
        )
        c = IdentityCache(sender)
        out = await c.get_mcp_server("s")
        assert out == {"name": "s", "url": "u"}


# ── sync_api_key ─────────────────────────────────────────────────


class TestSyncApiKey:
    async def test_returns_empty_when_not_cached(self):
        sender = _FakeSender()
        c = IdentityCache(sender)
        assert c.sync_api_key("openai") == ""

    async def test_returns_cached_value(self):
        sender = _FakeSender(responses={"get_api_key": {"key": "sk-abc"}})
        c = IdentityCache(sender)
        await c.get_api_key("openai")
        assert c.sync_api_key("openai") == "sk-abc"

    async def test_returns_empty_after_expiry(self):
        sender = _FakeSender(responses={"get_api_key": {"key": "sk-abc"}})
        # Zero TTL → entry immediately expires.
        c = IdentityCache(sender, key_ttl=0.0)
        await c.get_api_key("openai")
        # Wait until past the clock granularity.
        await asyncio.sleep(0.01)
        assert c.sync_api_key("openai") == ""


# ── prefetch_for_provider ────────────────────────────────────────


class TestPrefetch:
    async def test_silent_on_not_found(self):
        sender = _FakeSender(error={"kind": "not_found", "message": "no"})
        c = IdentityCache(sender)
        # Doesn't raise even though there's no key.
        await c.prefetch_for_provider("openai")

    async def test_populates_cache(self):
        sender = _FakeSender(responses={"get_api_key": {"key": "k"}})
        c = IdentityCache(sender)
        await c.prefetch_for_provider("openai")
        assert c.sync_api_key("openai") == "k"


# ── invalidate ───────────────────────────────────────────────────


class TestInvalidate:
    async def test_invalidate_all(self):
        sender = _FakeSender(responses={"get_api_key": {"key": "k"}})
        c = IdentityCache(sender)
        await c.get_api_key("openai")
        c.invalidate(kind="all")
        assert c.sync_api_key("openai") == ""

    async def test_invalidate_specific(self):
        sender = _FakeSender(responses={"get_api_key": {"key": "k"}})
        c = IdentityCache(sender)
        await c.get_api_key("openai")
        c.invalidate(kind="key", name="openai")
        assert c.sync_api_key("openai") == ""

    async def test_invalidate_kind_all_entries(self):
        sender = _FakeSender(responses={"get_api_key": {"key": "k"}})
        c = IdentityCache(sender)
        await c.get_api_key("a")
        await c.get_api_key("b")
        c.invalidate(kind="key")
        assert c.sync_api_key("a") == ""
        assert c.sync_api_key("b") == ""

    def test_invalidate_unknown_kind_raises(self):
        sender = _FakeSender()
        c = IdentityCache(sender)
        with pytest.raises(ValueError, match="unknown kind"):
            c.invalidate(kind="bogus")


# ── codex token sharing ──────────────────────────────────────────


class TestCodexTokens:
    async def test_get_codex_token_caches_and_returns_dict(self):
        sender = _FakeSender(
            responses={
                "get_codex_token": {
                    "tokens": {
                        "access_token": "at-1",
                        "refresh_token": "rt-1",
                        "expires_at": 9999999999,
                        "id_token": "id-1",
                        "account_id": "acc-1",
                    }
                }
            }
        )
        c = IdentityCache(sender)
        out = await c.get_codex_token()
        assert out["access_token"] == "at-1"
        # Cached.
        await c.get_codex_token()
        assert len(sender.calls) == 1

    def test_sync_codex_tokens_misses_when_unwarmed(self):
        # Cold cache → None (the codex resolver registered in
        # ``llm.codex_auth`` then treats this as "no tokens locally"
        # and the load returns None — host-canonical-by-design).
        sender = _FakeSender()
        c = IdentityCache(sender)
        assert c.sync_codex_tokens() is None

    async def test_sync_codex_tokens_returns_CodexTokens_after_warm(self):
        from kohakuterrarium.llm.codex_auth import CodexTokens

        sender = _FakeSender(
            responses={
                "get_codex_token": {
                    "tokens": {
                        "access_token": "at-warm",
                        "refresh_token": "rt-warm",
                        "expires_at": 9999999999,
                        "id_token": "id-warm",
                        "account_id": "acc-warm",
                    }
                }
            }
        )
        c = IdentityCache(sender)
        await c.prefetch_for_codex_if_needed()
        tokens = c.sync_codex_tokens()
        assert isinstance(tokens, CodexTokens)
        assert tokens.access_token == "at-warm"
        assert tokens.account_id == "acc-warm"

    async def test_invalidate_codex_clears(self):
        sender = _FakeSender(
            responses={
                "get_codex_token": {
                    "tokens": {
                        "access_token": "at",
                        "refresh_token": "rt",
                        "expires_at": 9999999999,
                        "id_token": "",
                        "account_id": "",
                    }
                }
            }
        )
        c = IdentityCache(sender)
        await c.get_codex_token()
        assert c.sync_codex_tokens() is not None
        c.invalidate(kind="codex")
        assert c.sync_codex_tokens() is None

    async def test_invalidate_all_clears_codex_too(self):
        sender = _FakeSender(
            responses={
                "get_codex_token": {
                    "tokens": {
                        "access_token": "at",
                        "refresh_token": "rt",
                        "expires_at": 9999999999,
                        "id_token": "",
                        "account_id": "",
                    }
                }
            }
        )
        c = IdentityCache(sender)
        await c.get_codex_token()
        c.invalidate(kind="all")
        assert c.sync_codex_tokens() is None


# ── module constants ────────────────────────────────────────────


class TestConstants:
    def test_default_ttls_present(self):
        assert DEFAULT_KEY_TTL_SECONDS > 0
