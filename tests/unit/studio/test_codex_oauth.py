"""Unit tests for :mod:`kohakuterrarium.studio.identity.codex_oauth`."""

import pytest

from kohakuterrarium.studio.identity import codex_oauth as mod
from kohakuterrarium.studio.identity.codex_oauth import (
    get_status,
    get_usage_async,
    login_async,
)


class _FakeTokens:
    def __init__(self, expired=False, expires_at=1000):
        self._expired = expired
        self.expires_at = expires_at

    def is_expired(self):
        return self._expired


class _FakeUsageCached:
    def __init__(self, *, empty=False, snapshots=None):
        self._empty = empty
        self.captured_at = 100
        self.snapshots = snapshots or []
        self.promo_message = "promo"

    def is_empty(self):
        return self._empty


class _FakeSnap:
    def to_dict(self):
        return {"x": 1}


# ── get_status ──────────────────────────────────────────────────


class TestGetStatus:
    def test_not_logged_in(self, monkeypatch):
        monkeypatch.setattr(mod.CodexTokens, "load", staticmethod(lambda: None))
        assert get_status() == {"authenticated": False}

    def test_authenticated_valid(self, monkeypatch):
        monkeypatch.setattr(
            mod.CodexTokens, "load", staticmethod(lambda: _FakeTokens(expired=False))
        )
        out = get_status()
        assert out["authenticated"] is True
        assert out["expired"] is False

    def test_authenticated_expired(self, monkeypatch):
        monkeypatch.setattr(
            mod.CodexTokens, "load", staticmethod(lambda: _FakeTokens(expired=True))
        )
        assert get_status()["expired"] is True


# ── login_async ─────────────────────────────────────────────────


class TestLoginAsync:
    async def test_returns_status(self, monkeypatch):
        async def fake_oauth(*, on_device_code=None, open_browser=True):
            # Stub accepts + ignores the kwargs so the legacy
            # contract test keeps passing as ``oauth_login`` grows
            # new parameters.
            return _FakeTokens(expires_at=12345)

        monkeypatch.setattr(mod, "oauth_login", fake_oauth)
        out = await login_async()
        assert out == {"status": "ok", "expires_at": 12345}

    async def test_forwards_on_device_code_callback(self, monkeypatch):
        # The SSE route relies on login_async piping the callback
        # through to oauth_login.  Verify the kwarg actually arrives.
        seen = {}

        async def fake_oauth(*, on_device_code=None, open_browser=True):
            seen["callback"] = on_device_code
            seen["open_browser"] = open_browser
            return _FakeTokens(expires_at=999)

        monkeypatch.setattr(mod, "oauth_login", fake_oauth)

        async def cb(url, code, expires_in):  # pragma: no cover
            pass

        await login_async(on_device_code=cb)
        assert seen["callback"] is cb
        # Default ``open_browser`` is True for back-compat with the
        # CLI ``run_login_blocking`` entry point.
        assert seen["open_browser"] is True

    async def test_forwards_open_browser_false(self, monkeypatch):
        # The SSE route MUST pass ``open_browser=False`` so the
        # backend doesn't auto-pop a system browser on the host
        # machine while the modal is already driving the user's
        # interaction.  Pin the forwarding contract; a future
        # refactor that drops this kwarg would let the Android
        # event-loop-blocking ``webbrowser.open`` bug come back.
        seen = {}

        async def fake_oauth(*, on_device_code=None, open_browser=True):
            seen["open_browser"] = open_browser
            return _FakeTokens(expires_at=42)

        monkeypatch.setattr(mod, "oauth_login", fake_oauth)
        await login_async(on_device_code=None, open_browser=False)
        assert seen["open_browser"] is False


# ── get_usage_async (live-first, cache fallback) ─────────────────


def _patch_live(monkeypatch, *, usage=None, credits=None, raises=None):
    """Install fake ``fetch_usage`` / ``list_reset_credits`` on the module."""

    async def fake_fetch(tokens):
        if raises is not None:
            raise raises
        return usage or {"snapshots": [], "available_count": None}

    async def fake_list(tokens, *, fallback_count=None):
        return credits or {"available_count": fallback_count, "credits": []}

    monkeypatch.setattr(mod, "fetch_usage", fake_fetch)
    monkeypatch.setattr(mod, "list_reset_credits", fake_list)


class TestGetUsageAsync:
    async def test_not_logged_in(self, monkeypatch):
        monkeypatch.setattr(mod.CodexTokens, "load", staticmethod(lambda: None))
        out = await get_usage_async()
        assert out["status"] == "not_logged_in"
        assert out["snapshots"] == []
        assert out["reset_credits"] == {"available_count": None, "credits": []}

    async def test_live_ok_is_authoritative(self, monkeypatch):
        monkeypatch.setattr(
            mod.CodexTokens, "load", staticmethod(lambda: _FakeTokens())
        )
        _patch_live(
            monkeypatch,
            usage={"snapshots": [{"x": 1}], "available_count": 2},
            credits={"available_count": 2, "credits": [{"id": "c1"}]},
        )
        # Passive cache holds stale/other data — the live fetch wins.
        monkeypatch.setattr(mod, "_get_cached_usage", lambda: None)
        out = await get_usage_async()
        assert out["status"] == "ok"
        assert out["source"] == "live"
        assert out["snapshots"] == [{"x": 1}]
        assert out["reset_credits"] == {
            "available_count": 2,
            "credits": [{"id": "c1"}],
        }

    async def test_live_promo_comes_from_passive_cache(self, monkeypatch):
        monkeypatch.setattr(
            mod.CodexTokens, "load", staticmethod(lambda: _FakeTokens())
        )
        _patch_live(monkeypatch, usage={"snapshots": [], "available_count": 0})
        monkeypatch.setattr(
            mod, "_get_cached_usage", lambda: _FakeUsageCached(snapshots=[_FakeSnap()])
        )
        out = await get_usage_async()
        # Live snapshots, but promo is a rolling hint from the cache.
        assert out["source"] == "live"
        assert out["promo_message"] == "promo"

    async def test_live_failure_falls_back_to_full_cache(self, monkeypatch):
        monkeypatch.setattr(
            mod.CodexTokens, "load", staticmethod(lambda: _FakeTokens())
        )
        _patch_live(monkeypatch, raises=RuntimeError("live down"))
        monkeypatch.setattr(
            mod, "_get_cached_usage", lambda: _FakeUsageCached(snapshots=[_FakeSnap()])
        )
        out = await get_usage_async()
        assert out["status"] == "ok"
        assert out["source"] == "cache"
        assert out["snapshots"] == [{"x": 1}]

    async def test_live_failure_empty_cache_is_no_data(self, monkeypatch):
        monkeypatch.setattr(
            mod.CodexTokens, "load", staticmethod(lambda: _FakeTokens())
        )
        _patch_live(monkeypatch, raises=RuntimeError("live down"))
        monkeypatch.setattr(mod, "_get_cached_usage", lambda: None)
        out = await get_usage_async()
        assert out["status"] == "no_data_yet"
        assert out["source"] == "cache"

    async def test_expired_refresh_result_is_used_for_live_fetch(self, monkeypatch):
        expired = _FakeTokens(expired=True)
        fresh = _FakeTokens(expired=False)
        seen = {}

        async def fake_refresh(t):
            seen["refreshed_from"] = t
            return fresh

        async def fake_fetch(tokens):
            seen["fetched_with"] = tokens
            return {"snapshots": [], "available_count": 0}

        async def fake_list(tokens, *, fallback_count=None):
            return {"available_count": fallback_count, "credits": []}

        monkeypatch.setattr(mod.CodexTokens, "load", staticmethod(lambda: expired))
        monkeypatch.setattr(mod, "refresh_tokens", fake_refresh)
        monkeypatch.setattr(mod, "fetch_usage", fake_fetch)
        monkeypatch.setattr(mod, "list_reset_credits", fake_list)
        monkeypatch.setattr(mod, "_get_cached_usage", lambda: None)

        out = await get_usage_async()
        # Refresh return value (not the stale token) drives the live fetch.
        assert seen["refreshed_from"] is expired
        assert seen["fetched_with"] is fresh
        assert out["status"] == "ok"


# ── consume_reset_credit_async ──────────────────────────────────


class TestConsumeResetCreditAsync:
    async def test_not_logged_in_raises_permission_error(self, monkeypatch):
        monkeypatch.setattr(mod.CodexTokens, "load", staticmethod(lambda: None))
        with pytest.raises(PermissionError):
            await mod.consume_reset_credit_async()

    async def test_generates_idempotency_key_when_absent(self, monkeypatch):
        seen = {}

        async def fake_consume(tokens, key, credit_id=None):
            seen["key"] = key
            seen["credit_id"] = credit_id
            return "reset"

        monkeypatch.setattr(
            mod.CodexTokens, "load", staticmethod(lambda: _FakeTokens())
        )
        monkeypatch.setattr(mod, "consume_reset_credit", fake_consume)
        out = await mod.consume_reset_credit_async()
        assert out["outcome"] == "reset"
        # A non-empty key was generated and echoed back for retries.
        assert out["idempotency_key"]
        assert out["idempotency_key"] == seen["key"]

    async def test_passes_through_supplied_key_and_credit_id(self, monkeypatch):
        seen = {}

        async def fake_consume(tokens, key, credit_id=None):
            seen["key"] = key
            seen["credit_id"] = credit_id
            return "alreadyRedeemed"

        monkeypatch.setattr(
            mod.CodexTokens, "load", staticmethod(lambda: _FakeTokens())
        )
        monkeypatch.setattr(mod, "consume_reset_credit", fake_consume)
        out = await mod.consume_reset_credit_async(
            idempotency_key="my-key", credit_id="credit-9"
        )
        assert seen == {"key": "my-key", "credit_id": "credit-9"}
        assert out == {"outcome": "alreadyRedeemed", "idempotency_key": "my-key"}
