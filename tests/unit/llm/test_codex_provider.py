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


class _Ev:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class TestReasoningCapture:
    """Codex must retain Responses reasoning text for snapshot persistence."""

    def _provider(self) -> CodexOAuthProvider:
        return CodexOAuthProvider(model="m", api_key="sk", base_url="https://h/v1")

    def test_reasoning_delta_events_are_packed(self):
        p = self._provider()
        p._process_stream_event(
            _Ev(type="response.reasoning_text.delta", delta="think "), []
        )
        p._process_stream_event(
            _Ev(type="response.reasoning_text.delta", delta="hard"), []
        )
        p._process_stream_event(
            _Ev(type="response.reasoning_summary_text.delta", delta="summary"), []
        )
        assert p._reasoning.fields() == {
            "reasoning_content": "think hard",
            "reasoning_summary": "summary",
            "_kt_assistant_segments": [
                {"type": "reasoning", "source": "responses_text", "text": "think hard"},
                {"type": "reasoning", "source": "responses_summary", "text": "summary"},
            ],
        }

    def test_reasoning_done_event_replaces_accumulator(self):
        p = self._provider()
        p._process_stream_event(
            _Ev(type="response.reasoning_text.delta", delta="partial"), []
        )
        p._process_stream_event(
            _Ev(type="response.reasoning_text.done", text="complete"), []
        )
        assert p._reasoning.fields()["reasoning_content"] == "complete"

    def test_segments_preserve_reasoning_text_tool_order(self):
        p = self._provider()
        collected = []
        p._process_stream_event(
            _Ev(type="response.reasoning_text.delta", delta="think 1"), collected
        )
        p._process_stream_event(
            _Ev(type="response.output_text.delta", delta="answer 1"), collected
        )
        p._process_stream_event(
            _Ev(
                type="response.output_item.done",
                item=_Ev(
                    type="function_call", call_id="call_1", name="t", arguments="{}"
                ),
            ),
            collected,
        )
        p._process_stream_event(
            _Ev(type="response.reasoning_text.delta", delta="think 2"), collected
        )

        assert p._reasoning.fields()["_kt_assistant_segments"] == [
            {"type": "reasoning", "source": "responses_text", "text": "think 1"},
            {"type": "text", "text": "answer 1"},
            {"type": "tool_call_ref", "call_id": "call_1"},
            {"type": "reasoning", "source": "responses_text", "text": "think 2"},
        ]

    def test_reasoning_output_item_is_captured(self):
        p = self._provider()
        item = _Ev(
            type="reasoning",
            summary=[{"type": "summary_text", "text": "brief"}],
            content=[{"type": "text", "text": "private"}],
        )
        p._process_stream_event(_Ev(type="response.output_item.done", item=item), [])
        assert p._reasoning.fields() == {
            "reasoning_content": "private",
            "reasoning_summary": "brief",
            "_kt_assistant_segments": [
                {"type": "reasoning", "source": "responses_summary", "text": "brief"},
                {"type": "reasoning", "source": "responses_text", "text": "private"},
            ],
        }


class _FakeWSConnection:
    def __init__(self):
        self.sent = []
        self.scripts = []

    async def send(self, event):
        self.sent.append(event)

    def __aiter__(self):
        events = self.scripts.pop(0) if self.scripts else []

        async def gen():
            for e in events:
                yield e

        return gen()

    async def close(self):
        pass


class _FakeWSManager:
    def __init__(self, connection):
        self.connection = connection

    async def enter(self):
        return self.connection


class _FakeWSResponses:
    """responses namespace exposing both connect() and create()."""

    def __init__(self):
        self.connection = _FakeWSConnection()
        self.connect_kwargs = None
        self.connect_exc: Exception | None = None
        self.kwargs = None

    def connect(self, **kwargs):
        if self.connect_exc is not None:
            raise self.connect_exc
        self.connect_kwargs = kwargs
        return _FakeWSManager(self.connection)

    async def create(self, **kwargs):
        self.kwargs = kwargs

        async def _empty():
            if False:  # pragma: no cover - async generator shape
                yield

        return _empty()


class _FakeWSClient:
    def __init__(self):
        self.responses = _FakeWSResponses()


def _ws_completed(resp_id="r1"):
    usage = _Ev(
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        input_tokens_details=_Ev(cached_tokens=3),
    )
    return _Ev(
        type="response.completed",
        response=_Ev(id=resp_id, output=[], usage=usage),
    )


class TestWebsocketMode:
    """websocket_mode drives turns over responses.connect with HTTP fallback."""

    def _provider(self, extra_body=None) -> CodexOAuthProvider:
        p = CodexOAuthProvider(
            model="m",
            api_key="sk",
            base_url="https://h/v1",
            reasoning_effort="low",
            extra_body=extra_body or {"websocket_mode": True},
        )
        p._client = _FakeWSClient()
        return p

    async def _drive(self, provider):
        chunks = []
        async for chunk in provider._raw_stream_chat(
            [
                {"role": "system", "content": "SYS"},
                {"role": "user", "content": "hi"},
            ]
        ):
            chunks.append(chunk)
        return chunks

    async def test_ws_turn_streams_and_collects_state(self):
        p = self._provider()
        p._client.responses.connection.scripts = [
            [
                _Ev(type="response.output_text.delta", delta="hello"),
                _Ev(
                    type="response.output_item.done",
                    item=_Ev(
                        type="function_call", call_id="c9", name="t", arguments="{}"
                    ),
                ),
                _ws_completed(),
            ]
        ]

        chunks = await self._drive(p)

        assert chunks == ["hello"]
        assert [tc.name for tc in p.last_tool_calls] == ["t"]
        assert p._last_usage["prompt_tokens"] == 10
        assert p._last_usage["cached_tokens"] == 3
        # The HTTP path must not have run.
        assert p._client.responses.kwargs is None
        sent = p._client.responses.connection.sent[0]
        assert sent["model"] == "m"
        assert sent["instructions"] == "SYS"
        assert sent["store"] is False
        assert sent["reasoning"] == {"effort": "low"}
        assert "websocket_mode" not in sent

    async def test_extra_body_reasoning_merges_into_ws_event(self):
        p = self._provider(
            extra_body={"websocket_mode": True, "reasoning": {"mode": "pro"}}
        )
        p._client.responses.connection.scripts = [[_ws_completed()]]

        await self._drive(p)

        sent = p._client.responses.connection.sent[0]
        assert sent["reasoning"] == {"effort": "low", "mode": "pro"}

    async def test_connect_failure_falls_back_to_http(self):
        p = self._provider()
        p._client.responses.connect_exc = ConnectionError("no ws upgrade")

        await self._drive(p)

        kw = p._client.responses.kwargs
        assert kw is not None
        assert kw["model"] == "m"

    async def test_extra_body_reasoning_merges_on_http_path(self):
        p = CodexOAuthProvider(
            model="m",
            api_key="sk",
            base_url="https://h/v1",
            reasoning_effort="low",
            extra_body={"reasoning": {"mode": "pro"}},
        )
        p._client = _FakeClient()
        async for _ in p._raw_stream_chat([{"role": "user", "content": "hi"}]):
            pass

        kw = p._client.responses.kwargs
        assert kw["reasoning"] == {"effort": "low", "mode": "pro"}

    def test_with_model_propagates_ws_mode_with_fresh_session(self):
        p = self._provider()
        p._ws_session = object()
        clone = p.with_model("m2")
        assert clone._websocket_mode is True
        assert clone.extra_body == p.extra_body
        assert clone._ws_session is None
