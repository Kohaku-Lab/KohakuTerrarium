"""Unit tests for ``llm/codex_provider.py`` auth-mode selection.

Behavior-first: the Codex provider is the OpenAI Responses-API transport.
With an explicit ``api_key`` it authenticates against a custom ``base_url``
using API-key auth and MUST skip the Codex OAuth login; with no key it
falls back to the ChatGPT-subscription OAuth flow (tokens). These tests
pin the client-construction + mode-selection without any network/OAuth.
"""

from dataclasses import dataclass

import pytest

from kohakuterrarium.llm import codex_provider as cp
from kohakuterrarium.llm.codex_provider import CODEX_BASE_URL, CodexOAuthProvider

pytestmark = pytest.mark.skipif(not cp.HAS_OPENAI, reason="openai SDK not installed")


@dataclass
class _FakeTokens:
    access_token: str = "oauth-token-xyz"

    def is_expired(self) -> bool:
        return False


class TestApiKeyMode:
    async def test_api_key_mode_skips_oauth_and_uses_base_url(self, monkeypatch):
        # Any attempt to OAuth-login should fail the test loudly.
        async def _boom(*a, **k):
            raise AssertionError("oauth_login must NOT run in api-key mode")

        monkeypatch.setattr(cp, "oauth_login", _boom)

        p = CodexOAuthProvider(
            model="gpt-x", api_key="sk-custom", base_url="https://my.host/v1"
        )
        await p.ensure_authenticated()

        assert p._tokens is None  # no OAuth token loaded
        assert p._client is not None
        assert "my.host/v1" in str(p._client.base_url)

    async def test_ensure_valid_token_builds_client_without_tokens(self, monkeypatch):
        async def _boom(*a, **k):
            raise AssertionError("oauth_login must NOT run in api-key mode")

        monkeypatch.setattr(cp, "oauth_login", _boom)
        p = CodexOAuthProvider(model="gpt-x", api_key="sk-custom")
        await p._ensure_valid_token()
        assert p._client is not None

    def test_with_model_preserves_api_key_and_base_url(self):
        p = CodexOAuthProvider(
            model="a", api_key="sk-custom", base_url="https://my.host/v1"
        )
        clone = p.with_model("b")
        assert clone._api_key == "sk-custom"
        assert clone._base_url == "https://my.host/v1"


class TestOAuthMode:
    async def test_oauth_mode_uses_codex_base_url_and_token(self, monkeypatch):
        # No api_key -> OAuth path. Stub token load so no browser login runs.
        monkeypatch.setattr(
            cp.CodexTokens, "load", classmethod(lambda cls, path=None: _FakeTokens())
        )

        async def _boom(*a, **k):
            raise AssertionError("oauth_login must NOT run when tokens exist")

        monkeypatch.setattr(cp, "oauth_login", _boom)

        p = CodexOAuthProvider(model="gpt-x")  # no api_key
        await p.ensure_authenticated()

        assert p._api_key is None
        assert isinstance(p._tokens, _FakeTokens)
        assert str(p._client.base_url).rstrip("/") == CODEX_BASE_URL.rstrip("/")


class _FakeResponses:
    def __init__(self):
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs

        async def _empty():
            if False:  # pragma: no cover - make this an async generator
                yield

        return _empty()


class _FakeClient:
    def __init__(self):
        self.responses = _FakeResponses()


class TestSessionIdHeaderGating:
    """``session_id`` is a ChatGPT/Codex-internal routing header: it must
    be sent in OAuth mode and OMITTED in API-key mode (so a third-party
    OpenAI-compatible Responses endpoint doesn't reject the request)."""

    async def _drive(self, provider):
        async for _ in provider._raw_stream_chat([{"role": "user", "content": "hi"}]):
            pass

    async def test_api_key_mode_omits_session_id(self):
        p = CodexOAuthProvider(model="m", api_key="sk", base_url="https://h/v1")
        p._client = _FakeClient()
        await self._drive(p)
        assert "extra_headers" not in p._client.responses.kwargs

    async def test_oauth_mode_sends_session_id(self):
        p = CodexOAuthProvider(model="m")  # OAuth mode (no api_key)
        p._tokens = _FakeTokens()
        p._client = _FakeClient()
        await self._drive(p)
        kw = p._client.responses.kwargs
        assert "extra_headers" in kw
        assert "session_id" in kw["extra_headers"]
