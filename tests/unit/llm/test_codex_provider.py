"""Unit tests for ``llm/codex_provider.py`` auth-mode selection.

Behavior-first: the Codex provider is the OpenAI Responses-API transport.
With an explicit ``api_key`` it authenticates against a custom ``base_url``
using API-key auth and MUST skip the Codex OAuth login; with no key it
falls back to the ChatGPT-subscription OAuth flow (tokens). These tests
pin the client-construction, mode-selection, and token-reload paths
without external network/OAuth.
"""

import asyncio
import json
import time
from dataclasses import dataclass

import pytest
from websockets import serve

from kohakuterrarium.llm import codex_provider as cp
from kohakuterrarium.llm.codex_auth import CodexTokens
from kohakuterrarium.llm.codex_provider import CODEX_BASE_URL, CodexOAuthProvider
from kohakuterrarium.llm.responses_ws import ResponsesWSError

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
        assert clone._token_lock is p._token_lock


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


class TestTokenReload:
    async def test_expired_token_reloads_newer_disk_login(self, monkeypatch):
        stale = CodexTokens(
            access_token="stale", refresh_token="", expires_at=time.time() - 60
        )
        fresh = CodexTokens(
            access_token="fresh", refresh_token="rotated", expires_at=time.time() + 3600
        )
        p = CodexOAuthProvider(model="gpt-x")
        p._tokens = stale
        rebuilds: list[bool] = []
        p._rebuild_client = lambda: rebuilds.append(True)

        async def _boom(*args, **kwargs):
            raise AssertionError(
                "refresh_tokens must not run when disk has a fresh login"
            )

        monkeypatch.setattr(
            cp.CodexTokens, "load", classmethod(lambda cls, path=None: fresh)
        )
        monkeypatch.setattr(cp, "refresh_tokens", _boom)

        await p._ensure_valid_token()

        assert p._tokens is fresh
        assert rebuilds == [True]

    async def test_refresh_failure_recovers_from_newer_disk_login(self, monkeypatch):
        stale = CodexTokens(
            access_token="stale", refresh_token="old", expires_at=time.time() - 60
        )
        fresh = CodexTokens(
            access_token="fresh", refresh_token="new", expires_at=time.time() + 3600
        )
        p = CodexOAuthProvider(model="gpt-x")
        p._tokens = stale
        p._rebuild_client = lambda: None
        loads = iter([stale, fresh])
        refresh_calls: list[CodexTokens] = []

        def _load(cls, path=None):
            return next(loads)

        async def _refresh(tokens):
            refresh_calls.append(tokens)
            raise RuntimeError("invalid_grant")

        monkeypatch.setattr(cp.CodexTokens, "load", classmethod(_load))
        monkeypatch.setattr(cp, "refresh_tokens", _refresh)

        await p._ensure_valid_token()

        assert p._tokens is fresh
        assert refresh_calls == [stale]

    async def test_refresh_failure_without_new_login_propagates(self, monkeypatch):
        stale = CodexTokens(
            access_token="stale", refresh_token="", expires_at=time.time() - 60
        )
        p = CodexOAuthProvider(model="gpt-x")
        p._tokens = stale
        p._rebuild_client = lambda: None

        async def _refresh(tokens):
            raise RuntimeError("No refresh token available - please re-authenticate")

        monkeypatch.setattr(
            cp.CodexTokens, "load", classmethod(lambda cls, path=None: stale)
        )
        monkeypatch.setattr(cp, "refresh_tokens", _refresh)

        with pytest.raises(RuntimeError, match="No refresh token available"):
            await p._ensure_valid_token()

    async def test_refresh_success_rebuilds_client(self, monkeypatch):
        stale = CodexTokens(
            access_token="stale", refresh_token="old", expires_at=time.time() - 60
        )
        fresh = CodexTokens(
            access_token="fresh", refresh_token="new", expires_at=time.time() + 3600
        )
        p = CodexOAuthProvider(model="gpt-x")
        p._tokens = stale
        rebuilds: list[bool] = []
        p._rebuild_client = lambda: rebuilds.append(True)

        async def _refresh(tokens):
            return fresh

        monkeypatch.setattr(
            cp.CodexTokens, "load", classmethod(lambda cls, path=None: stale)
        )
        monkeypatch.setattr(cp, "refresh_tokens", _refresh)

        await p._ensure_valid_token()

        assert p._tokens is fresh
        assert rebuilds == [True]

    async def test_concurrent_refresh_runs_once(self, monkeypatch):
        stale = CodexTokens(
            access_token="stale", refresh_token="old", expires_at=time.time() - 60
        )
        fresh = CodexTokens(
            access_token="fresh", refresh_token="new", expires_at=time.time() + 3600
        )
        p = CodexOAuthProvider(model="gpt-x")
        p._tokens = stale
        p._rebuild_client = lambda: None
        refresh_calls = 0

        async def _refresh(tokens):
            nonlocal refresh_calls
            refresh_calls += 1
            await asyncio.sleep(0)
            return fresh

        monkeypatch.setattr(
            cp.CodexTokens, "load", classmethod(lambda cls, path=None: stale)
        )
        monkeypatch.setattr(cp, "refresh_tokens", _refresh)

        await asyncio.gather(p._ensure_valid_token(), p._ensure_valid_token())

        assert p._tokens is fresh
        assert refresh_calls == 1

    async def test_token_reload_resets_ws_session(self, monkeypatch):
        stale = CodexTokens(
            access_token="stale", refresh_token="", expires_at=time.time() - 60
        )
        fresh = CodexTokens(
            access_token="fresh", refresh_token="new", expires_at=time.time() + 3600
        )

        p = CodexOAuthProvider(model="gpt-x", websocket_mode=True)
        p._tokens = stale
        p._rebuild_client = lambda: None
        session = _FakeWSSession()
        p._ws_session = session

        monkeypatch.setattr(
            cp.CodexTokens, "load", classmethod(lambda cls, path=None: fresh)
        )

        await p._ensure_valid_token()

        assert session.closed is True
        assert p._ws_session is None


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


class _TokenExpiredError(Exception):
    def __init__(self):
        super().__init__("Error code: 401 - Provided authentication token is expired.")
        self.status_code = 401
        self.body = {
            "error": {
                "code": "token_expired",
                "message": "Provided authentication token is expired.",
            }
        }


async def _empty_stream():
    if False:  # pragma: no cover - make this an async generator
        yield


class _ScriptedResponses:
    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome()


class _ScriptedClient:
    def __init__(self, outcomes):
        self.responses = _ScriptedResponses(outcomes)


class _FakeWSSession:
    def __init__(self):
        self.closed = False

    def invalidate(self):
        pass

    async def close(self):
        self.closed = True


class TestUnauthorizedRecovery:
    async def _drive(self, provider):
        return [
            chunk
            async for chunk in provider._stream_chat(
                [{"role": "user", "content": "hi"}]
            )
        ]

    async def test_server_401_reloads_disk_login_and_retries(self, monkeypatch):
        stale = CodexTokens(
            access_token="stale", refresh_token="old", expires_at=time.time() + 3600
        )
        fresh = CodexTokens(
            access_token="fresh", refresh_token="new", expires_at=time.time() + 3600
        )
        p = CodexOAuthProvider(model="gpt-x")
        p._tokens = stale
        p._rebuild_client = lambda: None
        p._client = _ScriptedClient([_TokenExpiredError(), _empty_stream])
        session = _FakeWSSession()
        p._ws_session = session

        async def _boom(*args, **kwargs):
            raise AssertionError(
                "refresh_tokens must not run when disk has a fresh login"
            )

        monkeypatch.setattr(
            cp.CodexTokens, "load", classmethod(lambda cls, path=None: fresh)
        )
        monkeypatch.setattr(cp, "refresh_tokens", _boom)

        chunks = await self._drive(p)

        assert chunks == []
        assert p._tokens is fresh
        assert p._client.responses.calls == 2
        assert session.closed is True
        assert p._ws_session is None

    async def test_server_401_refreshes_cached_token_and_retries(self, monkeypatch):
        stale = CodexTokens(
            access_token="stale", refresh_token="old", expires_at=time.time() + 3600
        )
        fresh = CodexTokens(
            access_token="fresh", refresh_token="new", expires_at=time.time() + 3600
        )
        p = CodexOAuthProvider(model="gpt-x")
        p._tokens = stale
        p._rebuild_client = lambda: None
        p._client = _ScriptedClient([_TokenExpiredError(), _empty_stream])
        refresh_calls: list[CodexTokens] = []

        async def _refresh(tokens):
            refresh_calls.append(tokens)
            return fresh

        monkeypatch.setattr(
            cp.CodexTokens, "load", classmethod(lambda cls, path=None: stale)
        )
        monkeypatch.setattr(cp, "refresh_tokens", _refresh)

        chunks = await self._drive(p)

        assert chunks == []
        assert p._tokens is fresh
        assert refresh_calls == [stale]
        assert p._client.responses.calls == 2

    async def test_server_401_retries_credentials_once(self, monkeypatch):
        stale = CodexTokens(
            access_token="stale", refresh_token="old", expires_at=time.time() + 3600
        )
        fresh = CodexTokens(
            access_token="fresh", refresh_token="new", expires_at=time.time() + 3600
        )
        p = CodexOAuthProvider(model="gpt-x")
        p._tokens = stale
        p._rebuild_client = lambda: None
        p._client = _ScriptedClient([_TokenExpiredError(), _TokenExpiredError()])
        refresh_calls = 0

        async def _refresh(tokens):
            nonlocal refresh_calls
            refresh_calls += 1
            return fresh

        monkeypatch.setattr(
            cp.CodexTokens, "load", classmethod(lambda cls, path=None: stale)
        )
        monkeypatch.setattr(cp, "refresh_tokens", _refresh)

        with pytest.raises(_TokenExpiredError):
            await self._drive(p)

        assert p._client.responses.calls == 2
        assert refresh_calls == 1

    async def test_server_401_without_recoverable_credentials_reraises(
        self, monkeypatch
    ):
        stale = CodexTokens(
            access_token="stale", refresh_token="old", expires_at=time.time() + 3600
        )
        p = CodexOAuthProvider(model="gpt-x")
        p._tokens = stale
        p._rebuild_client = lambda: None
        p._client = _ScriptedClient([_TokenExpiredError()])
        refresh_calls: list[CodexTokens] = []

        async def _refresh(tokens):
            refresh_calls.append(tokens)
            raise RuntimeError("invalid_grant")

        monkeypatch.setattr(
            cp.CodexTokens, "load", classmethod(lambda cls, path=None: stale)
        )
        monkeypatch.setattr(cp, "refresh_tokens", _refresh)

        with pytest.raises(_TokenExpiredError):
            await self._drive(p)

        assert p._client.responses.calls == 1
        assert refresh_calls == [stale]

    async def test_server_401_after_output_does_not_retry(self, monkeypatch):
        stale = CodexTokens(
            access_token="stale", refresh_token="old", expires_at=time.time() + 3600
        )

        async def _chunk_then_401():
            yield _Ev(type="response.output_text.delta", delta="hello")
            raise _TokenExpiredError()

        p = CodexOAuthProvider(model="gpt-x")
        p._tokens = stale
        p._rebuild_client = lambda: None
        p._client = _ScriptedClient([_chunk_then_401])

        async def _boom(*args, **kwargs):
            raise AssertionError("recovery must not run after output was emitted")

        monkeypatch.setattr(
            cp.CodexTokens, "load", classmethod(lambda cls, path=None: stale)
        )
        monkeypatch.setattr(cp, "refresh_tokens", _boom)

        chunks: list[str] = []
        with pytest.raises(_TokenExpiredError):
            async for chunk in p._stream_chat([{"role": "user", "content": "hi"}]):
                chunks.append(chunk)

        assert chunks == ["hello"]
        assert p._client.responses.calls == 1


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


class TestReasoningReplay:
    @pytest.mark.parametrize(
        "model, replay, expected",
        [
            ("slurm/ds", True, True),
            ("kimi-k2", True, True),
            ("glm-5", True, True),
            ("deepseek-flash", False, False),
        ],
    )
    @pytest.mark.parametrize("websocket", [False, True])
    async def test_explicit_replay_capability_is_applied_and_not_sent(
        self, model, replay, expected, websocket
    ):
        p = CodexOAuthProvider(
            model=model,
            api_key="sk",
            extra_body={"responses_reasoning_replay": replay},
            websocket_mode=websocket,
        )
        p._client = _FakeWSClient() if websocket else _FakeClient()
        if websocket:
            p._client.responses.connection.scripts = [[_ws_completed()]]
        messages = [{"role": "assistant", "content": "", "reasoning_content": "Think"}]
        _ = [chunk async for chunk in p._raw_stream_chat(messages)]
        body = (
            p._client.responses.connection.sent[-1]
            if websocket
            else p._client.responses.kwargs
        )
        assert bool(body["input"]) is expected
        if expected:
            assert body["input"][0]["content"] == [
                {"type": "reasoning_text", "text": "Think"}
            ]
        assert "responses_reasoning_replay" not in body
        assert "responses_reasoning_replay" not in body.get("extra_body", {})

    @pytest.mark.parametrize(
        "model", ["gpt-6-astra", "deepseek-flash", "deepseek/deepseek-flash"]
    )
    async def test_reasoning_replay_follows_target_model(self, model):
        p = CodexOAuthProvider(model=model, api_key="sk", base_url="https://h/v1")
        p._client = _FakeClient()
        messages = [
            {
                "role": "assistant",
                "content": "Done",
                "reasoning_content": "Inspect files",
            }
        ]
        async for _ in p._raw_stream_chat(messages):
            pass
        items = p._client.responses.kwargs["input"]
        expected = [
            {"role": "assistant", "content": [{"type": "output_text", "text": "Done"}]}
        ]
        if model.startswith("deepseek"):
            expected.insert(
                0,
                {
                    "type": "reasoning",
                    "summary": [],
                    "content": [{"type": "reasoning_text", "text": "Inspect files"}],
                },
            )
        assert items == expected
        assert messages[0]["reasoning_content"] == "Inspect files"


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
        self.closed = False

    async def send(self, event):
        self.sent.append(event)

    def __aiter__(self):
        events = self.scripts.pop(0) if self.scripts else []

        async def gen():
            for e in events:
                if isinstance(e, BaseException):
                    raise e
                yield e

        return gen()

    async def close(self):
        self.closed = True


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

    async def test_receives_event_larger_than_one_mib_with_real_sdk(self):
        text = "x" * (1024 * 1024 + 1)
        submissions = []

        async def respond(socket):
            submissions.append(json.loads(await socket.recv()))
            await socket.send(
                json.dumps({"type": "response.output_text.delta", "delta": text})
            )
            await socket.send(
                json.dumps(
                    {
                        "type": "response.completed",
                        "response": {"id": "large-response", "output": []},
                    }
                )
            )
            await socket.wait_closed()

        async with serve(respond, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = CodexOAuthProvider(
                api_key="test",
                model="test",
                base_url=f"http://127.0.0.1:{port}/v1",
                websocket_mode=True,
            )
            try:
                await provider.ensure_authenticated()
                if not hasattr(provider._client.responses, "connect"):
                    pytest.skip(
                        "Installed OpenAI SDK has no Responses WebSocket support"
                    )
                chunks = [
                    chunk
                    async for chunk in provider.chat(
                        [{"role": "user", "content": "hi"}]
                    )
                ]
                assert chunks == [text]
                assert len(submissions) == 1
                assert submissions[0]["type"] == "response.create"
            finally:
                await provider.close()

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

    @pytest.mark.parametrize("started", [False, True])
    async def test_retry_exhaustion_or_first_event_prevents_further_retry(
        self, started
    ):
        p = self._provider()
        connection = p._client.responses.connection
        events = [_Ev(type="response.created")] if started else []
        connection.scripts = [[*events, TimeoutError("upstream timeout")]]
        with pytest.raises(ResponsesWSError):
            async for _ in p.chat([{"role": "user", "content": "hi"}]):
                pass
        assert len(connection.sent) == (1 if started else 2)
        assert p._client.responses.kwargs is None
        assert connection.closed

    async def test_cancelled_provider_turn_closes_without_http_fallback(self):
        p = self._provider()
        connection = p._client.responses.connection
        connection.scripts = [[asyncio.CancelledError()]]
        with pytest.raises(asyncio.CancelledError):
            async for _ in p.chat([{"role": "user", "content": "hi"}]):
                pass
        assert connection.closed
        assert len(connection.sent) == 1
        assert p._client.responses.kwargs is None

    async def test_closing_public_stream_closes_inflight_socket(self):
        p = self._provider()
        connection = p._client.responses.connection
        connection.scripts = [[_Ev(type="response.output_text.delta", delta="partial")]]
        stream = p.chat([{"role": "user", "content": "hi"}])
        assert await anext(stream) == "partial"
        session = p._ws_session
        await stream.aclose()
        assert connection.closed
        assert not session.busy
        assert len(connection.sent) == 1
        assert p._client.responses.kwargs is None

    @pytest.mark.parametrize(
        "edit", [None, "text", "reasoning", "tool", "missing", "model"]
    )
    async def test_continuation_requires_exact_assistant_echo(self, edit):
        p = self._provider()
        p.model = "deepseek-v4.1"
        connection = p._client.responses.connection
        connection.scripts = [
            [
                _Ev(type="response.reasoning_text.delta", delta="plan"),
                _Ev(type="response.output_text.delta", delta="answer"),
                _Ev(
                    type="response.output_item.done",
                    item=_Ev(
                        type="function_call",
                        call_id="c1",
                        name="lookup",
                        arguments="{}",
                    ),
                ),
                _ws_completed("r1"),
            ],
            [_ws_completed("r2")],
        ]
        messages = [{"role": "user", "content": "hi"}]
        async for _ in p._raw_stream_chat(messages):
            if edit == "model":
                p.model = "gpt-x"
        assistant = {
            "role": "assistant",
            "content": "answer",
            "reasoning_content": "plan",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": "{}"},
                }
            ],
        }
        if edit == "text":
            assistant["content"] = "edited"
        elif edit == "reasoning":
            assistant["reasoning_content"] = "edited"
        elif edit == "tool":
            assistant["tool_calls"][0]["function"]["arguments"] = '{"changed":true}'
        suffix = [
            {"role": "tool", "tool_call_id": "c1", "content": "result"},
            {"role": "user", "content": "next"},
        ]
        history = messages + ([] if edit == "missing" else [assistant]) + suffix
        async for _ in p._raw_stream_chat(history):
            pass
        sent = connection.sent[1]
        if edit is None:
            assert sent["previous_response_id"] == "r1"
            assert sent["input"] == cp.to_responses_input(suffix, model=p.model)
        else:
            assert "previous_response_id" not in sent
            assert sent["input"] == cp.fix_tool_call_pairing(
                cp.to_responses_input(history, model=p.model)
            )

    async def test_extra_body_reasoning_merges_on_http_path(self):
        p = CodexOAuthProvider(
            model="m",
            api_key="sk",
            base_url="https://h/v1",
            reasoning_effort="low",
            extra_body={
                "reasoning": {"mode": "pro"},
                "websocket_connection_options": {"ping_timeout": None},
            },
        )
        p._client = _FakeClient()
        async for _ in p._raw_stream_chat([{"role": "user", "content": "hi"}]):
            pass

        kw = p._client.responses.kwargs
        assert kw["reasoning"] == {"effort": "low", "mode": "pro"}
        assert "websocket_connection_options" not in kw.get("extra_body", {})

    def test_with_model_propagates_ws_mode_with_fresh_session(self):
        p = self._provider()
        p._ws_session = object()
        clone = p.with_model("m2")
        assert clone._websocket_mode is True
        assert clone.extra_body == p.extra_body
        assert clone._ws_session is None
