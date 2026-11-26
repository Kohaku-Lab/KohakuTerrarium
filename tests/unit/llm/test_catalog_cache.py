"""Provider catalog cache through real profile stores and in-memory HTTP."""

import asyncio
import json

import httpx
import pytest

from kohakuterrarium.llm import catalog_cache as cache_mod
from kohakuterrarium.llm.catalog_cache import (
    DISCOVERY_TTL_SECONDS,
    DiscoveryCache,
    backend_fingerprint,
    cached_discover_models,
    invalidate_discovery,
)
from kohakuterrarium.llm.discovery import ModelDiscoveryError
from kohakuterrarium.llm.grok.auth import GrokToken, GrokTokens
from kohakuterrarium.llm.profiles.api_keys import save_api_key
from kohakuterrarium.llm.profiles.backends import load_backends
from kohakuterrarium.llm.profiles.resolve import save_backend
from kohakuterrarium.llm.profiles.types import LLMBackend
from kohakuterrarium.studio.identity.api_keys import remove_key, set_key
from kohakuterrarium.studio.identity.codex import oauth
from kohakuterrarium.studio.identity.llm.backends import (
    remove_backend,
    save_backend_record,
)
from kohakuterrarium.utils.config_dir import config_dir


class _Upstream:
    """In-memory deepseek ``/models`` endpoint that counts requests."""

    def __init__(self):
        self.calls: list[str] = []
        self.model = "m1"
        self.status = 200
        self.gate: asyncio.Event | None = None

    async def __call__(self, request):
        self.calls.append(request.headers.get("authorization", ""))
        if self.gate is not None:
            await self.gate.wait()
        if self.status != 200:
            return httpx.Response(self.status, json={})
        return httpx.Response(200, json={"data": [{"id": self.model}]})

    @property
    def transport(self):
        return httpx.MockTransport(self)


class _Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def upstream():
    save_api_key("deepseek", "key-one")
    return _Upstream()


async def test_hit_then_ttl_expiry_refetches(upstream):
    clock = _Clock()
    cache = DiscoveryCache(clock=clock)
    first = await cache.get("deepseek", transport=upstream.transport)
    assert [m.id for m in first.models] == ["m1"]
    assert first.fetched_at == clock.now
    assert first.fetched_at_iso == "1970-01-12T13:46:40+00:00"

    upstream.model = "m2"
    clock.now += DISCOVERY_TTL_SECONDS - 1
    assert await cache.get("deepseek", transport=upstream.transport) is first
    assert len(upstream.calls) == 1

    clock.now += 1
    expired = await cache.get("deepseek", transport=upstream.transport)
    assert [m.id for m in expired.models] == ["m2"]
    assert expired.fetched_at == clock.now
    assert len(upstream.calls) == 2


async def test_clock_moving_backwards_is_a_miss(upstream):
    clock = _Clock()
    cache = DiscoveryCache(clock=clock)
    await cache.get("deepseek", transport=upstream.transport)
    clock.now -= 5
    await cache.get("deepseek", transport=upstream.transport)
    assert len(upstream.calls) == 2


async def test_credential_change_misses_and_secret_is_not_kept(upstream):
    cache = DiscoveryCache(clock=_Clock())
    await cache.get("deepseek", transport=upstream.transport)
    save_api_key("deepseek", "key-two")
    upstream.model = "m2"
    snapshot = await cache.get("deepseek", transport=upstream.transport)
    assert [m.id for m in snapshot.models] == ["m2"]
    assert upstream.calls == ["Bearer key-one", "Bearer key-two"]
    state = repr(cache._entries) + repr(cache._generations)
    assert "key-two" not in state and "key-one" not in state


async def test_base_url_and_key_env_change_miss(upstream, monkeypatch):
    cache = DiscoveryCache(clock=_Clock())
    await cache.get("deepseek", transport=upstream.transport)
    backend = load_backends()["deepseek"]
    monkeypatch.setenv("TEAM_KEY", "team")
    save_backend(LLMBackend("deepseek", "openai", backend.base_url + "/", "TEAM_KEY"))
    await cache.get("deepseek", transport=upstream.transport)
    assert len(upstream.calls) == 2


async def test_refresh_bypasses_and_replaces_the_entry(upstream):
    clock = _Clock()
    cache = DiscoveryCache(clock=clock)
    await cache.get("deepseek", transport=upstream.transport)
    upstream.model = "m2"
    clock.now += 30
    refreshed = await cache.get("deepseek", refresh=True, transport=upstream.transport)
    assert [m.id for m in refreshed.models] == ["m2"]
    assert refreshed.fetched_at == clock.now
    assert await cache.get("deepseek", transport=upstream.transport) is refreshed
    assert len(upstream.calls) == 2


async def test_concurrent_requests_share_one_fetch(upstream):
    cache = DiscoveryCache(clock=_Clock())
    upstream.gate = asyncio.Event()
    waiters = [
        asyncio.create_task(cache.get("deepseek", transport=upstream.transport))
        for _ in range(3)
    ]
    waiters.append(
        asyncio.create_task(
            cache.get("deepseek", refresh=True, transport=upstream.transport)
        )
    )
    while not upstream.calls:
        await asyncio.sleep(0)
    for _ in range(20):
        await asyncio.sleep(0)
    upstream.gate.set()
    results = await asyncio.gather(*waiters)
    assert len(upstream.calls) == 1
    assert all(result is results[0] for result in results)
    assert not cache._flights


async def test_cancelled_waiter_does_not_cancel_the_shared_fetch(upstream):
    cache = DiscoveryCache(clock=_Clock())
    upstream.gate = asyncio.Event()
    first = asyncio.create_task(cache.get("deepseek", transport=upstream.transport))
    second = asyncio.create_task(cache.get("deepseek", transport=upstream.transport))
    while not upstream.calls:
        await asyncio.sleep(0)
    first.cancel()
    upstream.gate.set()
    assert [m.id for m in (await second).models] == ["m1"]
    with pytest.raises(asyncio.CancelledError):
        await first
    assert len(upstream.calls) == 1


async def test_errors_are_not_cached(upstream):
    cache = DiscoveryCache(clock=_Clock())
    upstream.status = 500
    with pytest.raises(ModelDiscoveryError, match="HTTP 500"):
        await cache.get("deepseek", transport=upstream.transport)
    assert not cache._entries and not cache._flights
    upstream.status = 200
    assert [
        m.id for m in (await cache.get("deepseek", transport=upstream.transport)).models
    ] == ["m1"]
    assert len(upstream.calls) == 2


async def test_failed_refresh_keeps_the_previous_entry(upstream):
    cache = DiscoveryCache(clock=_Clock())
    first = await cache.get("deepseek", transport=upstream.transport)
    upstream.status = 503
    with pytest.raises(ModelDiscoveryError):
        await cache.get("deepseek", refresh=True, transport=upstream.transport)
    assert await cache.get("deepseek", transport=upstream.transport) is first


async def test_unknown_backend_is_not_cached():
    cache = DiscoveryCache(clock=_Clock())
    for _ in range(2):
        with pytest.raises(ModelDiscoveryError, match="Unknown provider") as error:
            await cache.get("nope")
        assert error.value.status == 404
    assert not cache._entries


async def test_invalidation_during_fetch_drops_the_late_result(upstream):
    cache = DiscoveryCache(clock=_Clock())
    upstream.gate = asyncio.Event()
    stale = asyncio.create_task(cache.get("deepseek", transport=upstream.transport))
    while not upstream.calls:
        await asyncio.sleep(0)
    cache.invalidate("deepseek")
    assert not cache._flights
    upstream.gate.set()
    await stale
    assert not cache._entries
    upstream.gate = None
    await cache.get("deepseek", transport=upstream.transport)
    assert len(upstream.calls) == 2


async def test_invalidate_all_and_one_name(upstream):
    cache = DiscoveryCache(clock=_Clock())
    save_api_key("mimo", "mimo-key")
    await cache.get("deepseek", transport=upstream.transport)
    await cache.get("mimo", transport=upstream.transport)
    cache.invalidate("mimo")
    assert set(cache._entries) == {"deepseek"}
    await cache.get("mimo", transport=upstream.transport)
    cache.invalidate()
    assert not cache._entries
    await cache.get("deepseek", transport=upstream.transport)
    assert len(upstream.calls) == 4


async def test_settings_writes_invalidate_the_process_cache(upstream, monkeypatch):
    fetched = []

    async def catalog(name, *, transport=None):
        fetched.append(name)
        return []

    monkeypatch.setattr(cache_mod, "_cache", DiscoveryCache(fetch=catalog))

    async def read():
        await cached_discover_models("deepseek")
        return len(fetched)

    assert await read() == 1
    assert await read() == 1
    set_key("deepseek", "key-one")
    assert await read() == 2
    save_backend_record("deepseek", "openai", load_backends()["deepseek"].base_url)
    assert await read() == 3
    remove_key("deepseek")
    assert await read() == 4
    assert await read() == 4
    invalidate_discovery("deepseek")
    assert await read() == 5

    save_backend_record("team", "openai", "https://team.example/v1")
    await cached_discover_models("team")
    assert "team" in cache_mod._cache._entries
    assert remove_backend("team")
    assert "team" not in cache_mod._cache._entries
    await cached_discover_models("team")
    assert "team" not in cache_mod._cache._entries
    assert fetched.count("team") == 2
    fetched.clear()

    async def fake_login(*, on_device_code=None, open_browser=True):
        class Tokens:
            expires_at = 1

        return Tokens()

    monkeypatch.setattr(oauth, "oauth_login", fake_login)
    assert await read() == 0
    await oauth.login_async()
    assert await read() == 1


def _write_codex_tokens(**fields):
    (config_dir() / "codex-auth.json").write_text(
        json.dumps({"access_token": "access", "refresh_token": "refresh", **fields})
    )


def test_codex_fingerprint_follows_the_login_account():
    codex = load_backends()["codex"]
    signed_out = backend_fingerprint(codex)
    _write_codex_tokens(refresh_token="r1")
    by_refresh = backend_fingerprint(codex)
    _write_codex_tokens(account_id="acct-1", access_token="a1")
    first = backend_fingerprint(codex)
    _write_codex_tokens(account_id="acct-1", access_token="a2-rotated")
    assert backend_fingerprint(codex) == first
    _write_codex_tokens(account_id="acct-2")
    assert len({signed_out, by_refresh, first, backend_fingerprint(codex)}) == 4
    assert "acct-2" not in backend_fingerprint(codex)


def test_grok_fingerprint_follows_the_borrowed_login(monkeypatch):
    grok = load_backends()["grok-subscription"]
    tokens = []
    monkeypatch.setattr(
        GrokTokens, "load_bootstrap_candidates", classmethod(lambda cls: tokens)
    )
    signed_out = backend_fingerprint(grok)
    tokens.append(GrokToken("secret-token", "grok-cli"))
    signed_in = backend_fingerprint(grok)
    assert signed_in != signed_out
    assert "secret-token" not in signed_in


def test_fingerprint_is_scoped_to_the_config_root(tmp_path, monkeypatch):
    backend = load_backends()["deepseek"]
    here = backend_fingerprint(backend)
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path / "other-root"))
    assert backend_fingerprint(backend) != here
