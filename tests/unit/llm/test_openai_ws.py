"""Unit tests for ``llm/openai_ws.py`` and OpenAIProvider websocket mode."""

import asyncio
import json

import pytest
from websockets import serve

from kohakuterrarium.llm.base import ToolSchema
from kohakuterrarium.llm.openai import OpenAIProvider
from kohakuterrarium.llm.openai_ws import build_ws_request
from kohakuterrarium.llm.responses_ws import ResponsesWSError


class Ev:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class FakeWSConnection:
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


class FakeWSManager:
    def __init__(self, connection):
        self.connection = connection

    async def enter(self):
        return self.connection


class FakeResponses:
    def __init__(self):
        self.connection = FakeWSConnection()
        self.connect_exc: Exception | None = None

    def connect(self, **kwargs):
        if self.connect_exc is not None:
            raise self.connect_exc
        return FakeWSManager(self.connection)


class FakeCompletions:
    def __init__(self):
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs

        async def _empty():
            if False:  # pragma: no cover - async generator shape
                yield

        return _empty()


class FakeClient:
    def __init__(self):
        self.responses = FakeResponses()
        self.chat = Ev(completions=FakeCompletions())


def make_provider(**kwargs) -> OpenAIProvider:
    provider = OpenAIProvider(api_key="sk-test", model="gpt-x", **kwargs)
    provider._client = FakeClient()
    return provider


def completed(resp_id="r1"):
    usage = Ev(
        input_tokens=7,
        output_tokens=2,
        total_tokens=9,
        input_tokens_details=Ev(cached_tokens=1),
    )
    return Ev(
        type="response.completed", response=Ev(id=resp_id, output=[], usage=usage)
    )


MESSAGES = [
    {"role": "system", "content": "SYS"},
    {"role": "user", "content": "hi"},
]


class TestBuildWsRequest:
    @pytest.mark.parametrize(
        "model, configured, override, expected",
        [
            ("alias", True, {}, True),
            ("alias", False, {"responses_reasoning_replay": True}, True),
            ("alias", True, {"responses_reasoning_replay": False}, False),
            ("gpt-x", True, {"responses_reasoning_replay": None}, False),
            ("deepseek-flash", False, {"responses_reasoning_replay": None}, True),
        ],
    )
    def test_replay_capability_uses_effective_options_and_stays_off_wire(
        self, model, configured, override, expected
    ):
        provider = OpenAIProvider(
            api_key="sk-test",
            model=model,
            extra_body={"responses_reasoning_replay": configured},
        )
        messages = [{"role": "assistant", "content": "", "reasoning_content": "Think"}]
        kwargs = {"extra_body": override}
        event, items = build_ws_request(provider, messages, None, kwargs)
        assert bool(items) is expected
        if expected:
            assert items[0]["content"] == [{"type": "reasoning_text", "text": "Think"}]
        assert "responses_reasoning_replay" not in event
        assert provider.extra_body["responses_reasoning_replay"] is configured

    @pytest.mark.parametrize(
        "configured, override, expected_reasoning",
        [
            ("deepseek-flash", "gpt-6-astra", False),
            ("gpt-6-astra", "deepseek-flash", True),
            ("gpt-6-astra", None, False),
            ("deepseek-flash", None, True),
        ],
    )
    def test_reasoning_uses_effective_model(
        self, configured, override, expected_reasoning
    ):
        provider = OpenAIProvider(api_key="sk-test", model=configured)
        messages = [
            {
                "role": "assistant",
                "content": "Done",
                "reasoning_content": "Inspect files",
            }
        ]
        event, items = build_ws_request(
            provider, messages, None, {"model": override} if override else {}
        )
        assert event["model"] == (override or configured)
        expected = [
            {"role": "assistant", "content": [{"type": "output_text", "text": "Done"}]}
        ]
        if expected_reasoning:
            expected.insert(
                0,
                {
                    "type": "reasoning",
                    "summary": [],
                    "content": [{"type": "reasoning_text", "text": "Inspect files"}],
                },
            )
        assert items == expected

    def test_splits_instructions_and_maps_fields(self):
        provider = make_provider(
            temperature=0.3,
            max_tokens=512,
            extra_body={
                "websocket_mode": True,
                "reasoning": {"enabled": True, "effort": "high"},
                "service_tier": "priority",
            },
        )
        tools = [ToolSchema(name="t", description="d", parameters={"type": "object"})]

        event, items = build_ws_request(provider, MESSAGES, tools, {})

        assert event["model"] == "gpt-x"
        assert event["instructions"] == "SYS"
        assert event["store"] is False
        assert event["temperature"] == 0.3
        assert event["max_output_tokens"] == 512
        assert event["tools"] == [
            {
                "type": "function",
                "name": "t",
                "description": "d",
                "parameters": {"type": "object"},
            }
        ]
        # ``enabled`` is OpenRouter-unified, not a Responses API field.
        assert event["reasoning"] == {"effort": "high"}
        assert event["service_tier"] == "priority"
        assert "websocket_mode" not in event
        assert items == [
            {"role": "user", "content": [{"type": "input_text", "text": "hi"}]}
        ]


class TestProviderWebsocketMode:
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
            provider = OpenAIProvider(
                api_key="test",
                model="test",
                base_url=f"http://127.0.0.1:{port}/v1",
                websocket_mode=True,
            )
            try:
                if not hasattr(provider._client.responses, "connect"):
                    pytest.skip(
                        "Installed OpenAI SDK has no Responses WebSocket support"
                    )
                chunks = [chunk async for chunk in provider.chat(MESSAGES)]
                assert chunks == [text]
                assert len(submissions) == 1
                assert submissions[0]["type"] == "response.create"
            finally:
                await provider.close()

    async def test_replay_capability_is_removed_from_http_fallback(self):
        provider = make_provider(
            websocket_mode=True, extra_body={"responses_reasoning_replay": True}
        )
        provider._client.responses.connect_exc = ConnectionError("no ws upgrade")
        _ = [chunk async for chunk in provider.chat(MESSAGES)]
        assert (
            "responses_reasoning_replay"
            not in provider._client.chat.completions.kwargs.get("extra_body", {})
        )

    async def test_reconnect_replays_captured_and_edited_reasoning(self):
        provider = make_provider(
            websocket_mode=True, extra_body={"responses_reasoning_replay": True}
        )
        connection = provider._client.responses.connection
        connection.scripts = [
            [Ev(type="response.reasoning_text.delta", delta="Think"), completed()]
        ]
        _ = [chunk async for chunk in provider.chat(MESSAGES)]
        history = [
            *MESSAGES,
            {
                "role": "assistant",
                "content": "",
                **provider.last_assistant_extra_fields,
            },
            {"role": "user", "content": "Continue"},
        ]
        await provider._ws_session.close()
        connection.scripts = [[completed("r2")], [completed("r3")]]
        _ = [chunk async for chunk in provider.chat(history)]
        sent = connection.sent[-1]
        assert "previous_response_id" not in sent
        assert sent["input"][1]["content"] == [
            {"type": "reasoning_text", "text": "Think"}
        ]
        history[2]["reasoning_content"] = "Edited"
        _ = [chunk async for chunk in provider.chat(history)]
        assert "previous_response_id" not in connection.sent[-1]
        assert connection.sent[-1]["input"][1]["content"] == [
            {"type": "reasoning_text", "text": "Edited"}
        ]

    async def _drive(self, provider):
        chunks = []
        async for chunk in provider._raw_stream_chat(MESSAGES):
            chunks.append(chunk)
        return chunks

    async def test_ws_turn_streams_and_collects_state(self):
        provider = make_provider(extra_body={"websocket_mode": True})
        provider._client.responses.connection.scripts = [
            [
                Ev(type="response.output_text.delta", delta="hey"),
                Ev(
                    type="response.output_item.done",
                    item=Ev(
                        type="function_call", call_id="c1", name="fn", arguments="{}"
                    ),
                ),
                completed(),
            ]
        ]

        chunks = await self._drive(provider)

        assert chunks == ["hey"]
        assert [tc.name for tc in provider.last_tool_calls] == ["fn"]
        assert provider._last_usage["prompt_tokens"] == 7
        assert provider._client.chat.completions.kwargs is None

    async def test_ws_turn_captures_reasoning_fields(self):
        provider = make_provider(extra_body={"websocket_mode": True})
        provider._client.responses.connection.scripts = [
            [
                Ev(type="response.reasoning_text.delta", delta="think "),
                Ev(type="response.reasoning_text.delta", delta="hard"),
                Ev(type="response.reasoning_summary_text.delta", delta="summary"),
                completed(),
            ]
        ]

        await self._drive(provider)

        assert provider._last_assistant_extra_fields == {
            "reasoning_content": "think hard",
            "reasoning_summary": "summary",
            "_kt_assistant_segments": [
                {"type": "reasoning", "source": "responses_text", "text": "think hard"},
                {"type": "reasoning", "source": "responses_summary", "text": "summary"},
            ],
        }
        assert provider._last_tool_calls == []

    async def test_ws_segments_preserve_reasoning_text_tool_order(self):
        provider = make_provider(extra_body={"websocket_mode": True})
        provider._client.responses.connection.scripts = [
            [
                Ev(type="response.reasoning_text.delta", delta="think 1"),
                Ev(type="response.output_text.delta", delta="answer 1"),
                Ev(
                    type="response.output_item.done",
                    item=Ev(
                        type="function_call",
                        call_id="call_1",
                        name="fn",
                        arguments="{}",
                    ),
                ),
                Ev(type="response.reasoning_text.delta", delta="think 2"),
                completed(),
            ]
        ]

        await self._drive(provider)

        assert provider._last_assistant_extra_fields["_kt_assistant_segments"] == [
            {"type": "reasoning", "source": "responses_text", "text": "think 1"},
            {"type": "text", "text": "answer 1"},
            {"type": "tool_call_ref", "call_id": "call_1"},
            {"type": "reasoning", "source": "responses_text", "text": "think 2"},
        ]

    async def test_connect_failure_falls_back_to_chat_completions(self):
        provider = make_provider(extra_body={"websocket_mode": True})
        provider._client.responses.connect_exc = ConnectionError("no upgrade")

        await self._drive(provider)

        kwargs = provider._client.chat.completions.kwargs
        assert kwargs is not None
        assert kwargs["model"] == "gpt-x"
        # The framework knob never reaches the HTTP body either.
        assert "websocket_mode" not in kwargs.get("extra_body", {})

    @pytest.mark.parametrize("started", [False, True])
    async def test_retry_exhaustion_or_first_event_prevents_further_retry(
        self, started
    ):
        provider = make_provider(websocket_mode=True)
        connection = provider._client.responses.connection
        events = [Ev(type="response.created")] if started else []
        connection.scripts = [[*events, TimeoutError("upstream timeout")]]
        with pytest.raises(ResponsesWSError):
            async for _ in provider.chat(MESSAGES):
                pass
        assert len(connection.sent) == (1 if started else 2)
        assert connection.closed
        assert provider._client.chat.completions.kwargs is None

    async def test_cancelled_provider_turn_closes_without_http_fallback(self):
        provider = make_provider(websocket_mode=True)
        connection = provider._client.responses.connection
        connection.scripts = [[asyncio.CancelledError()]]
        with pytest.raises(asyncio.CancelledError):
            async for _ in provider.chat(MESSAGES):
                pass
        assert connection.closed
        assert len(connection.sent) == 1
        assert provider._client.chat.completions.kwargs is None

    async def test_closing_public_stream_closes_inflight_socket(self):
        provider = make_provider(websocket_mode=True)
        connection = provider._client.responses.connection
        connection.scripts = [[Ev(type="response.output_text.delta", delta="partial")]]
        stream = provider.chat(MESSAGES)
        assert await anext(stream) == "partial"
        await stream.aclose()
        assert connection.closed
        assert not provider._ws_session.busy
        assert len(connection.sent) == 1
        assert provider._client.chat.completions.kwargs is None

    @pytest.mark.parametrize("model", ["gpt-x", "deepseek-flash"])
    @pytest.mark.parametrize("edit", [None, "text", "reasoning"])
    async def test_assistant_echo_edit_controls_delta(self, model, edit):
        provider = make_provider(websocket_mode=True)
        options = {"model": model}
        connection = provider._client.responses.connection
        connection.scripts = [
            [
                Ev(type="response.reasoning_text.delta", delta="plan"),
                Ev(type="response.output_text.delta", delta="original"),
                completed("r1"),
            ],
            [completed("r2")],
        ]
        async for _ in provider.chat(MESSAGES, **options):
            pass
        history = [
            *MESSAGES,
            {
                "role": "assistant",
                "content": "edited" if edit == "text" else "original",
                "reasoning_content": "edited" if edit == "reasoning" else "plan",
            },
            {"role": "user", "content": "next"},
        ]
        async for _ in provider.chat(history, **options):
            pass
        sent = connection.sent[1]
        if edit == "text" or (edit == "reasoning" and model == "deepseek-flash"):
            assert "previous_response_id" not in sent
            assert (
                sent["input"] == build_ws_request(provider, history, None, options)[1]
            )
        else:
            assert sent["previous_response_id"] == "r1"
            assert sent["input"] == [
                {"role": "user", "content": [{"type": "input_text", "text": "next"}]}
            ]

    async def test_disabled_mode_uses_chat_completions_directly(self):
        provider = make_provider()
        await self._drive(provider)
        assert provider._client.chat.completions.kwargs is not None

    async def test_echo_uses_model_at_submission_when_changed_during_stream(self):
        provider = make_provider(websocket_mode=True)
        provider.config.model = "deepseek-flash"
        connection = provider._client.responses.connection
        connection.scripts = [
            [
                Ev(type="response.reasoning_text.delta", delta="plan"),
                Ev(type="response.output_text.delta", delta="answer"),
                completed("r1"),
            ],
            [completed("r2")],
        ]
        async for _ in provider.chat(MESSAGES):
            provider.config.model = "gpt-x"
        history = [
            *MESSAGES,
            {"role": "assistant", "content": "answer", "reasoning_content": "plan"},
            {"role": "user", "content": "next"},
        ]
        async for _ in provider.chat(history):
            pass
        assert "previous_response_id" not in connection.sent[-1]
        assert (
            connection.sent[-1]["input"]
            == build_ws_request(provider, history, None, {})[1]
        )

    def test_with_model_propagates_ws_mode_with_fresh_session(self):
        provider = make_provider(websocket_mode=True)
        provider._ws_session = object()
        clone = provider.with_model("gpt-y")
        assert clone._websocket_mode is True
        assert clone._ws_session is None
