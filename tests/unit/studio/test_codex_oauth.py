"""Unit tests for :mod:`kohakuterrarium.studio.identity.codex_oauth`."""

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


# ── get_usage_async ─────────────────────────────────────────────


class TestGetUsageAsync:
    async def test_not_logged_in(self, monkeypatch):
        monkeypatch.setattr(mod.CodexTokens, "load", staticmethod(lambda: None))
        out = await get_usage_async()
        assert out["status"] == "not_logged_in"
        assert out["snapshots"] == []

    async def test_no_data(self, monkeypatch):
        monkeypatch.setattr(
            mod.CodexTokens, "load", staticmethod(lambda: _FakeTokens())
        )
        monkeypatch.setattr(mod, "_get_cached_usage", lambda: None)
        out = await get_usage_async()
        assert out["status"] == "no_data_yet"

    async def test_empty_cache(self, monkeypatch):
        monkeypatch.setattr(
            mod.CodexTokens, "load", staticmethod(lambda: _FakeTokens())
        )
        monkeypatch.setattr(
            mod, "_get_cached_usage", lambda: _FakeUsageCached(empty=True)
        )
        out = await get_usage_async()
        assert out["status"] == "no_data_yet"

    async def test_full_cache(self, monkeypatch):
        monkeypatch.setattr(
            mod.CodexTokens, "load", staticmethod(lambda: _FakeTokens())
        )
        monkeypatch.setattr(
            mod,
            "_get_cached_usage",
            lambda: _FakeUsageCached(snapshots=[_FakeSnap()]),
        )
        out = await get_usage_async()
        assert out["status"] == "ok"
        assert out["snapshots"] == [{"x": 1}]
        assert out["promo_message"] == "promo"

    async def test_expired_triggers_refresh(self, monkeypatch):
        refreshed = []
        expired_tokens = _FakeTokens(expired=True)

        async def fake_refresh(t):
            refreshed.append(t)

        monkeypatch.setattr(
            mod.CodexTokens,
            "load",
            staticmethod(lambda: expired_tokens),
        )
        monkeypatch.setattr(mod, "refresh_tokens", fake_refresh)
        monkeypatch.setattr(mod, "_get_cached_usage", lambda: None)
        out = await get_usage_async()
        # The loaded (expired) token object is the one passed to refresh.
        assert refreshed == [expired_tokens]
        # No cached usage after refresh → no_data_yet.
        assert out["status"] == "no_data_yet"
