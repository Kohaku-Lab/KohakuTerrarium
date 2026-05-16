"""Unit tests for ``llm/base.py`` — provider protocol + base class.

Behavior-first: assert the exact API-format conversion, JSON-argument
parsing fallback, the BaseLLMProvider message-normalisation contract,
the streaming vs non-streaming dispatch, emergency-drop callback
fan-out, and ``with_model`` reuse/refusal semantics.
"""

import pytest

from kohakuterrarium.llm.base import (
    BaseLLMProvider,
    ChatResponse,
    LLMConfig,
    NativeToolCall,
    ToolSchema,
)
from kohakuterrarium.llm.message import Message


class TestToolSchema:
    def test_to_api_format_wraps_function_block(self):
        schema = ToolSchema(
            name="bash",
            description="run",
            parameters={"type": "object", "properties": {"c": {"type": "string"}}},
        )
        assert schema.to_api_format() == {
            "type": "function",
            "function": {
                "name": "bash",
                "description": "run",
                "parameters": {
                    "type": "object",
                    "properties": {"c": {"type": "string"}},
                },
            },
        }

    def test_default_parameters_is_empty_object_schema(self):
        schema = ToolSchema(name="ping", description="d")
        assert schema.parameters == {"type": "object", "properties": {}}


class TestNativeToolCall:
    def test_parsed_arguments_decodes_json(self):
        call = NativeToolCall(id="c1", name="bash", arguments='{"cmd": "ls"}')
        assert call.parsed_arguments() == {"cmd": "ls"}

    def test_invalid_json_falls_back_to_raw_wrapper(self):
        call = NativeToolCall(id="c1", name="bash", arguments="not json")
        assert call.parsed_arguments() == {"_raw": "not json"}


class _StubProvider(BaseLLMProvider):
    """Concrete provider that records what _stream_chat / _complete_chat saw."""

    def __init__(self, config=None):
        super().__init__(config)
        self.streamed_messages = None
        self.completed_messages = None

    async def _stream_chat(
        self, messages, *, tools=None, provider_native_tools=None, **kw
    ):
        self.streamed_messages = messages
        yield "chunk-a"
        yield "chunk-b"

    async def _complete_chat(self, messages, **kw):
        self.completed_messages = messages
        return ChatResponse(
            content="full-response",
            finish_reason="stop",
            usage={"prompt_tokens": 1},
            model="m",
        )


class TestBaseLLMProviderNormalisation:
    def test_empty_messages_normalise_to_empty_list(self):
        provider = _StubProvider()
        assert provider._normalize_messages([]) == []

    def test_dict_messages_passed_through(self):
        provider = _StubProvider()
        dicts = [{"role": "user", "content": "hi"}]
        assert provider._normalize_messages(dicts) is dicts

    def test_message_objects_converted_to_dicts(self):
        provider = _StubProvider()
        out = provider._normalize_messages([Message(role="user", content="hi")])
        assert out == [{"role": "user", "content": "hi"}]


class TestBaseLLMProviderChat:
    async def test_streaming_chat_yields_each_chunk(self):
        provider = _StubProvider()
        chunks = [c async for c in provider.chat([{"role": "user", "content": "x"}])]
        assert chunks == ["chunk-a", "chunk-b"]
        assert provider.streamed_messages == [{"role": "user", "content": "x"}]

    async def test_non_streaming_chat_yields_single_full_response(self):
        provider = _StubProvider()
        chunks = [
            c
            async for c in provider.chat(
                [{"role": "user", "content": "x"}], stream=False
            )
        ]
        assert chunks == ["full-response"]

    async def test_chat_resets_last_tool_calls(self):
        provider = _StubProvider()
        provider._last_tool_calls = [NativeToolCall("c", "n", "{}")]
        async for _ in provider.chat([{"role": "user", "content": "x"}]):
            pass
        assert provider.last_tool_calls == []

    async def test_chat_complete_returns_full_response(self):
        provider = _StubProvider()
        resp = await provider.chat_complete([Message(role="user", content="hi")])
        assert resp.content == "full-response"
        # Message objects were normalised before reaching _complete_chat
        assert provider.completed_messages == [{"role": "user", "content": "hi"}]


class TestBaseLLMProviderProperties:
    def test_last_usage_defaults_to_empty_dict(self):
        assert _StubProvider().last_usage == {}

    def test_last_assistant_content_parts_defaults_to_none(self):
        assert _StubProvider().last_assistant_content_parts is None

    def test_last_assistant_extra_fields_defaults_to_empty_dict(self):
        assert _StubProvider().last_assistant_extra_fields == {}

    def test_translate_provider_native_tool_default_is_none(self):
        assert _StubProvider().translate_provider_native_tool(object()) is None


class TestEmergencyDropCallbacks:
    def test_registered_callback_invoked_with_messages(self):
        provider = _StubProvider()
        seen = []
        provider.on_emergency_drop(lambda msgs: seen.append(msgs))
        recovered = [{"role": "user", "content": "recovered"}]
        provider._notify_emergency_drop(recovered)
        assert seen == [recovered]

    def test_failing_callback_does_not_break_others(self):
        provider = _StubProvider()
        seen = []

        def boom(_msgs):
            raise RuntimeError("callback failed")

        provider.on_emergency_drop(boom)
        provider.on_emergency_drop(lambda msgs: seen.append("ok"))
        # one callback raising must not stop the fan-out
        provider._notify_emergency_drop([])
        assert seen == ["ok"]


class TestWithModel:
    def test_same_model_returns_self(self):
        provider = _StubProvider(LLMConfig(model="gpt-x"))
        assert provider.with_model("gpt-x") is provider

    def test_empty_name_returns_self(self):
        provider = _StubProvider(LLMConfig(model="gpt-x"))
        assert provider.with_model("") is provider

    def test_different_model_refused_by_base_implementation(self):
        provider = _StubProvider(LLMConfig(model="gpt-x"))
        with pytest.raises(ValueError, match="cannot switch"):
            provider.with_model("gpt-y")


class TestBaseProviderAbstractMethods:
    async def test_stream_chat_not_implemented_on_base(self):
        base = BaseLLMProvider()
        with pytest.raises(NotImplementedError):
            async for _ in base._stream_chat([]):
                pass

    async def test_complete_chat_not_implemented_on_base(self):
        base = BaseLLMProvider()
        with pytest.raises(NotImplementedError):
            await base._complete_chat([])
