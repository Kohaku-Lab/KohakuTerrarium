"""Unit tests for ``llm/openai_ws.py`` and OpenAIProvider websocket mode."""

from kohakuterrarium.llm.base import ToolSchema
from kohakuterrarium.llm.openai import OpenAIProvider
from kohakuterrarium.llm.openai_ws import build_ws_request


class Ev:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class FakeWSConnection:
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

    async def test_connect_failure_falls_back_to_chat_completions(self):
        provider = make_provider(extra_body={"websocket_mode": True})
        provider._client.responses.connect_exc = ConnectionError("no upgrade")

        await self._drive(provider)

        kwargs = provider._client.chat.completions.kwargs
        assert kwargs is not None
        assert kwargs["model"] == "gpt-x"
        # The framework knob never reaches the HTTP body either.
        assert "websocket_mode" not in kwargs.get("extra_body", {})

    async def test_disabled_mode_uses_chat_completions_directly(self):
        provider = make_provider()
        await self._drive(provider)
        assert provider._client.chat.completions.kwargs is not None

    def test_with_model_propagates_ws_mode_with_fresh_session(self):
        provider = make_provider(websocket_mode=True)
        provider._ws_session = object()
        clone = provider.with_model("gpt-y")
        assert clone._websocket_mode is True
        assert clone._ws_session is None
