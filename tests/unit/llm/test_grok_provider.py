"""Tests for Grok subscription chat credential selection."""

import pytest

from kohakuterrarium.errors import LLMNotConfiguredError
from kohakuterrarium.llm.grok_auth import GROK_CLI_BASE_URL, GrokToken
from kohakuterrarium.llm.grok_provider import GrokSubscriptionProvider
from kohakuterrarium.llm.openai import OpenAIProvider


class _AuthError(Exception):
    status_code = 401


class _PermissionError(Exception):
    status_code = 403


@pytest.fixture(autouse=True)
def _disable_real_cli_refresh(monkeypatch):
    async def no_refresh(*, force=False):
        return None

    monkeypatch.setattr(
        "kohakuterrarium.llm.grok_provider.GrokTokens.ensure_fresh_cli",
        no_refresh,
    )


class TestGrokSubscriptionProvider:
    def test_missing_login_does_not_fall_back_to_api_key(self, monkeypatch):
        monkeypatch.setenv("XAI_API_KEY", "must-not-be-used")
        monkeypatch.setattr(
            "kohakuterrarium.llm.grok_provider.GrokTokens.load_candidates",
            lambda: [],
        )
        monkeypatch.setattr(
            "kohakuterrarium.llm.grok_provider.GrokTokens." "load_bootstrap_candidates",
            lambda: [],
        )

        with pytest.raises(LLMNotConfiguredError, match="does not fall back"):
            GrokSubscriptionProvider()

    def test_uses_first_candidate_and_redacted_source(self, monkeypatch):
        token = GrokToken(
            access_token="secret",
            source="grok-cli",
            base_url=GROK_CLI_BASE_URL,
            extra_headers={
                "X-XAI-Token-Auth": "xai-grok-cli",
                "x-authenticateresponse": "authenticate-response",
                "x-grok-client-identifier": "grok-shell",
                "x-grok-client-mode": "interactive",
                "x-grok-client-version": "1.0.5",
            },
        )
        monkeypatch.setattr(
            "kohakuterrarium.llm.grok_provider.GrokTokens.load_candidates",
            lambda: [token],
        )

        provider = GrokSubscriptionProvider(model="grok-test")

        assert provider.credential_source == "grok-cli"
        assert provider.config.model == "grok-test"
        assert provider.provider_native_tools == frozenset(
            {"grok_image_gen", "video_gen"}
        )
        assert provider.base_url == "https://cli-chat-proxy.grok.com/v1"
        assert provider._extra_headers == {
            "X-XAI-Token-Auth": "xai-grok-cli",
            "x-authenticateresponse": "authenticate-response",
            "x-grok-client-identifier": "grok-shell",
            "x-grok-client-mode": "interactive",
            "x-grok-client-version": "1.0.5",
            "x-grok-model-override": "grok-test",
        }

    def test_uses_xai_chat_cache_header_not_responses_field(self, monkeypatch):
        token = GrokToken(access_token="secret", source="grok-cli")
        monkeypatch.setattr(
            "kohakuterrarium.llm.grok_provider.GrokTokens.load_candidates",
            lambda: [token],
        )
        provider = GrokSubscriptionProvider()
        provider.prompt_cache_key = "session-1"

        kwargs = provider._prompt_cache_request_kwargs()

        assert kwargs == {"extra_headers": {"x-grok-conv-id": "session-1"}}
        assert "prompt_cache_key" not in kwargs

    def test_reload_applies_changed_cli_request_profile(self, monkeypatch):
        original = GrokToken(
            access_token="same-token",
            source="grok-cli",
            extra_headers={"x-grok-client-version": "1.0.5"},
        )
        updated = GrokToken(
            access_token="same-token",
            source="grok-cli",
            extra_headers={"x-grok-client-version": "1.0.6"},
        )
        candidates = [original]
        monkeypatch.setattr(
            "kohakuterrarium.llm.grok_provider.GrokTokens.load_candidates",
            lambda: candidates,
        )
        provider = GrokSubscriptionProvider()
        candidates[0] = updated

        changed = provider.reload_credentials()

        assert changed is True
        assert provider._extra_headers["x-grok-client-version"] == "1.0.6"

    @pytest.mark.asyncio
    async def test_rejected_primary_falls_back_before_output(self, monkeypatch):
        primary = GrokToken(access_token="one", source="grok-cli")
        fallback = GrokToken(access_token="two", source="opencode")
        monkeypatch.setattr(
            "kohakuterrarium.llm.grok_provider.GrokTokens.load_candidates",
            lambda: [primary, fallback],
        )
        calls = []

        async def fake_stream(self, messages, *, tools=None, **kwargs):
            calls.append(self._api_key)
            if self._api_key == "one":
                raise _AuthError("rejected")
            yield "ok"

        monkeypatch.setattr(OpenAIProvider, "_stream_chat", fake_stream)
        provider = GrokSubscriptionProvider()

        chunks = [chunk async for chunk in provider._stream_chat([])]

        assert chunks == ["ok"]
        assert calls == ["one", "two"]
        assert provider.credential_source == "opencode"

    @pytest.mark.asyncio
    async def test_rejected_cli_token_refreshes_and_retries_once(self, monkeypatch):
        original = GrokToken(access_token="old", source="grok-cli")
        refreshed = GrokToken(access_token="new", source="grok-cli")
        candidates = [original]
        monkeypatch.setattr(
            "kohakuterrarium.llm.grok_provider.GrokTokens.load_candidates",
            lambda: candidates,
        )
        refresh_calls = []

        async def refresh(*, force=False):
            refresh_calls.append(force)
            if force:
                candidates[0] = refreshed
                return refreshed
            return original

        monkeypatch.setattr(
            "kohakuterrarium.llm.grok_provider.GrokTokens.ensure_fresh_cli",
            refresh,
        )
        calls = []

        async def fake_stream(self, messages, *, tools=None, **kwargs):
            calls.append(self._api_key)
            if self._api_key == "old":
                raise _AuthError("rejected")
            yield "ok"

        monkeypatch.setattr(OpenAIProvider, "_stream_chat", fake_stream)
        provider = GrokSubscriptionProvider()

        chunks = [chunk async for chunk in provider._stream_chat([])]

        assert chunks == ["ok"]
        assert calls == ["old", "new"]
        assert refresh_calls == [False, True]

    @pytest.mark.asyncio
    async def test_does_not_fallback_after_output_started(self, monkeypatch):
        tokens = [
            GrokToken(access_token="one", source="grok-cli"),
            GrokToken(access_token="two", source="opencode"),
        ]
        monkeypatch.setattr(
            "kohakuterrarium.llm.grok_provider.GrokTokens.load_candidates",
            lambda: tokens,
        )

        async def fake_stream(self, messages, *, tools=None, **kwargs):
            yield "partial"
            raise _AuthError("late")

        monkeypatch.setattr(OpenAIProvider, "_stream_chat", fake_stream)
        provider = GrokSubscriptionProvider()
        stream = provider._stream_chat([])

        assert await anext(stream) == "partial"
        with pytest.raises(_AuthError):
            await anext(stream)

    @pytest.mark.asyncio
    async def test_permission_error_does_not_poison_or_fallback_credential(
        self, monkeypatch
    ):
        tokens = [
            GrokToken(access_token="one", source="grok-cli"),
            GrokToken(access_token="two", source="opencode"),
        ]
        monkeypatch.setattr(
            "kohakuterrarium.llm.grok_provider.GrokTokens.load_candidates",
            lambda: tokens,
        )
        calls = []

        async def fake_stream(self, messages, *, tools=None, **kwargs):
            calls.append(self._api_key)
            raise _PermissionError("model unavailable")
            yield  # pragma: no cover

        monkeypatch.setattr(OpenAIProvider, "_stream_chat", fake_stream)
        provider = GrokSubscriptionProvider()

        with pytest.raises(_PermissionError):
            _ = [chunk async for chunk in provider._stream_chat([])]
        with pytest.raises(_PermissionError):
            _ = [chunk async for chunk in provider._stream_chat([])]

        assert calls == ["one", "one"]
        assert provider._rejected_fingerprints == set()
