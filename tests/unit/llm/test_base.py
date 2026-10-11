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
    OverflowRecoveryState,
    ToolSchema,
    fork_llm,
)
from kohakuterrarium.llm.message import Message
from kohakuterrarium.llm.recovery import ErrorClass
from kohakuterrarium.llm.request_budget import MAX_REQUEST_SHRINKS, RequestMeasure


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
        self.stream_closed = False

    async def _stream_chat(
        self, messages, *, tools=None, provider_native_tools=None, **kw
    ):
        self.streamed_messages = messages
        try:
            yield "chunk-a"
            yield "chunk-b"
        finally:
            self.stream_closed = True

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
        assert provider.stream_closed

    async def test_closing_chat_finishes_owned_provider_stream(self):
        provider = _StubProvider()
        stream = provider.chat([{"role": "user", "content": "x"}])
        assert await anext(stream) == "chunk-a"
        await stream.aclose()
        assert provider.stream_closed

    async def test_stream_iterator_without_aclose_remains_supported(self):
        class Iterator:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

        class IteratorProvider(_StubProvider):
            def _stream_chat(self, messages, **kwargs):
                return Iterator()

        assert [chunk async for chunk in IteratorProvider().chat([])] == []

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


def _media_round():
    return [
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "u"}}]},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "c", "type": "function", "function": {"name": "read"}}
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "c",
            "content": [{"type": "image_url", "image_url": {"url": "p"}}],
        },
    ]


class TestContentRecovery:
    async def test_ladder_strips_newest_then_all_then_drops_then_gives_up(self):
        provider = _StubProvider()
        repairs = []
        provider.on_context_repair(repairs.append)
        state = OverflowRecoveryState()
        exc = RuntimeError("invalid_image: page 3")
        current = _media_round()
        steps = []
        while True:
            current = await provider._recover_context(
                ErrorClass.CONTENT, exc, current, state
            )
            if current is None:
                break
            steps.append([m["role"] for m in current])
        assert [(r.kind, r.scope) for r in repairs] == [
            ("strip_media", "newest"),
            ("strip_media", "all"),
            ("drop_tool_round", "newest"),
        ]
        assert steps[-1] == ["user", "user"]
        assert all(r.reason == "invalid_image: page 3" for r in repairs)

    async def test_stage_that_changes_nothing_is_skipped(self):
        provider = _StubProvider()
        repairs = []
        provider.on_context_repair(repairs.append)
        state = OverflowRecoveryState()
        messages = [{"role": "user", "content": "no media"}]
        assert (
            await provider._recover_context(
                ErrorClass.CONTENT, RuntimeError("x"), messages, state
            )
            is None
        )
        assert repairs == [] and state.content_stage == 3

    async def test_other_classes_are_not_repaired(self):
        provider = _StubProvider()
        state = OverflowRecoveryState()
        for cls in (ErrorClass.USER_ERROR, ErrorClass.SERVER, ErrorClass.TRANSIENT):
            assert (
                await provider._recover_context(
                    cls, RuntimeError("invalid image"), _media_round(), state
                )
                is None
            )
        assert state.content_stage == 0

    def test_failing_repair_callback_does_not_break_others(self):
        provider = _StubProvider()
        seen = []
        provider.on_context_repair(lambda r: (_ for _ in ()).throw(RuntimeError()))
        provider.on_context_repair(seen.append)
        provider._notify_context_repair("repair")
        assert seen == ["repair"]


class TestOversizeRecovery:
    async def _recover(self, provider, state, current, measure):
        provider._last_request_measure = measure
        return await provider._recover_context(
            ErrorClass.OVERSIZE, RuntimeError("413 Payload Too Large"), current, state
        )

    async def test_shrinks_while_each_refit_is_smaller_then_repairs(self):
        provider = _StubProvider()
        repairs = []
        provider.on_context_repair(repairs.append)
        state = OverflowRecoveryState()
        current = _media_round()
        retried = await self._recover(
            provider, state, current, RequestMeasure(4_000_000, 2)
        )
        assert retried == current and repairs == []
        assert provider.request_ceiling().max_bytes == 3_000_000
        assert provider._request_byte_target() == 3_000_000
        retried = await self._recover(
            provider, state, current, RequestMeasure(2_900_000, 2)
        )
        assert retried == current and state.shrinks == 2
        # A refit that did not get smaller ends shrinking: the ladder takes over.
        repaired = await self._recover(
            provider, state, current, RequestMeasure(2_900_000, 2)
        )
        assert [(r.kind, r.scope) for r in repairs] == [("strip_media", "newest")]
        assert repaired != current
        assert repairs[0].reason == "413 Payload Too Large"

    async def test_text_only_or_unmeasured_requests_go_straight_to_repair(self):
        for measure in (RequestMeasure(9_000_000, 0), None):
            provider = _StubProvider()
            repairs = []
            provider.on_context_repair(repairs.append)
            state = OverflowRecoveryState()
            await self._recover(provider, state, _media_round(), measure)
            assert provider.request_ceiling().max_bytes is None
            assert state.shrinks == 0 and len(repairs) == 1

    async def test_unscanned_request_may_shrink(self):
        provider = _StubProvider()
        state = OverflowRecoveryState()
        current = _media_round()
        retried = await self._recover(
            provider, state, current, RequestMeasure(8_000_000, None)
        )
        assert retried == current
        assert provider.request_ceiling().max_bytes == 6_000_000

    async def test_shrinks_are_capped(self):
        provider = _StubProvider()
        state = OverflowRecoveryState()
        current = [{"role": "user", "content": "plain"}]
        size = 8_000_000
        outcomes = []
        for _ in range(MAX_REQUEST_SHRINKS + 1):
            size = size * 3 // 4
            outcomes.append(
                await self._recover(provider, state, current, RequestMeasure(size, 1))
            )
        assert outcomes[:MAX_REQUEST_SHRINKS] == [current] * MAX_REQUEST_SHRINKS
        assert outcomes[-1] is None
        assert state.shrinks == MAX_REQUEST_SHRINKS

    async def test_exhausted_ladder_falls_back_to_overflow_rescue(self):
        provider = _StubProvider()
        rescued = [{"role": "user", "content": "summary"}]

        async def rescue():
            return rescued

        provider._overflow_rescue = rescue
        state = OverflowRecoveryState()
        current = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
        ]
        result = await self._recover(provider, state, current, RequestMeasure(10, 0))
        assert result == rescued and state.rescue_attempted

    def test_configured_target_and_learned_ceiling_combine(self):
        provider = _StubProvider()
        assert provider._request_byte_target() is None
        provider._request_max_bytes = 5_000_000
        assert provider._request_byte_target() == 5_000_000
        provider.request_ceiling().lower(4_000_000)
        assert provider._request_byte_target() == 3_000_000

    async def test_fit_request_records_the_measure_per_fork(self):
        provider = _StubProvider()
        body = {"messages": [{"role": "user", "content": "hi"}]}
        assert await provider._fit_request(body) is body
        assert provider._last_request_measure.image_count is None
        fork = provider.fork()
        assert fork._last_request_measure is None
        assert fork.request_ceiling() is provider.request_ceiling()


class TestFork:
    def test_fork_owns_turn_state_and_hooks_but_borrows_the_client(self):
        provider = _StubProvider(LLMConfig(model="m"))
        provider._client = client = object()
        provider.extra_body = {"websocket_mode": True}
        provider._last_tool_calls = [NativeToolCall(id="1", name="n", arguments="{}")]
        provider._last_usage = {"prompt_tokens": 3}
        provider.on_emergency_drop(lambda m: None)
        provider.on_context_repair(lambda r: None)
        provider._overflow_rescue = lambda: None
        fork = provider.fork()
        assert fork is not provider and fork._client is client
        assert fork._borrowed_client and not provider._borrowed_client
        assert fork.last_tool_calls == [] and fork.last_usage == {}
        assert fork._emergency_drop_callbacks == []
        assert fork._context_repair_callbacks == []
        assert fork._overflow_rescue is None
        fork.extra_body["websocket_mode"] = False
        assert provider.extra_body == {"websocket_mode": True}
        assert len(provider._emergency_drop_callbacks) == 1

    def test_fork_llm_falls_back_to_the_same_object_without_fork(self):
        plain = object()
        assert fork_llm(plain) is plain
        provider = _StubProvider()
        assert fork_llm(provider) is not provider


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
