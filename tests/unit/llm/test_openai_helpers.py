"""Unit tests for ``llm/openai_helpers.py`` — OpenAI-compat helpers.

Behavior-first: assert the exact extracted usage dict, field-presence
detection across SDK objects / dicts / pydantic-style fakes, the
reasoning-field packing rules, and the stateful-assistant-field
back-fill invariant the docstring promises.
"""

import logging

from kohakuterrarium.llm.openai_helpers import (
    delta_field,
    delta_field_present,
    extract_usage,
    log_token_usage,
    merge_reasoning_detail_stream,
    normalize_stateful_assistant_fields,
    pack_reasoning_fields,
    tool_call_from_pending,
    tool_calls_from_message,
)


class _Usage:
    def __init__(self, prompt=0, completion=0, total=0, details=None):
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.total_tokens = total
        self.prompt_tokens_details = details


class _Details:
    def __init__(self, cached=0, cache_write=0):
        self.cached_tokens = cached
        self.cache_write_tokens = cache_write


class _SDKObj:
    """SDK-style object with model_extra / model_fields_set."""

    def __init__(self, model_extra=None, fields_set=None, **attrs):
        self.model_extra = model_extra
        self.model_fields_set = fields_set
        self.__dict__.update(attrs)


class _Func:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _ToolCall:
    def __init__(self, id, name, arguments):
        self.id = id
        self.function = _Func(name, arguments)


class TestExtractUsage:
    def test_falsy_usage_returns_empty(self):
        assert extract_usage(None) == {}

    def test_basic_token_counts_extracted(self):
        out = extract_usage(_Usage(prompt=10, completion=5, total=15))
        assert out == {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "cached_tokens": 0,
            "cache_write_tokens": 0,
        }

    def test_cache_details_extracted_when_present(self):
        usage = _Usage(prompt=20, completion=8, total=28, details=_Details(7, 3))
        out = extract_usage(usage)
        assert out["cached_tokens"] == 7
        assert out["cache_write_tokens"] == 3

    def test_none_token_fields_coerced_to_zero(self):
        out = extract_usage(_Usage(prompt=None, completion=None, total=None))
        assert out["prompt_tokens"] == 0
        assert out["completion_tokens"] == 0


class TestDeltaField:
    def test_model_extra_takes_priority(self):
        obj = _SDKObj(model_extra={"reasoning_content": "from_extra"})
        obj.reasoning_content = "from_attr"
        assert delta_field(obj, "reasoning_content") == "from_extra"

    def test_dict_lookup(self):
        assert delta_field({"k": "v"}, "k") == "v"

    def test_plain_attr_fallback(self):
        obj = _SDKObj()
        obj.k = "attr_val"
        assert delta_field(obj, "k") == "attr_val"

    def test_missing_field_returns_none(self):
        assert delta_field(_SDKObj(), "missing") is None


class TestDeltaFieldPresent:
    def test_present_in_model_extra(self):
        obj = _SDKObj(model_extra={"reasoning_content": ""})
        # empty-string value but explicitly present
        assert delta_field_present(obj, "reasoning_content") is True

    def test_present_in_dict_even_when_falsy(self):
        assert (
            delta_field_present({"reasoning_content": ""}, "reasoning_content") is True
        )

    def test_present_in_model_fields_set(self):
        obj = _SDKObj(fields_set={"reasoning_content"})
        assert delta_field_present(obj, "reasoning_content") is True

    def test_absent_field_is_not_present(self):
        assert delta_field_present({"other": 1}, "reasoning_content") is False

    def test_attr_with_non_none_value_counts_as_present(self):
        obj = _SDKObj()
        obj.reasoning_content = "value"
        assert delta_field_present(obj, "reasoning_content") is True

    def test_attr_with_none_value_is_not_present(self):
        obj = _SDKObj()
        obj.reasoning_content = None
        assert delta_field_present(obj, "reasoning_content") is False

    def test_present_in_legacy_fields_set(self):
        # pydantic v1-style ``__fields_set__`` attribute
        class _LegacyObj:
            __fields_set__ = {"reasoning_content"}
            model_extra = None

        assert delta_field_present(_LegacyObj(), "reasoning_content") is True


class TestPackReasoningFields:
    def test_nonempty_text_packed(self):
        out = pack_reasoning_fields("reasoning", [], {})
        assert out == {"reasoning_content": "reasoning"}

    def test_empty_text_dropped_unless_include_flag(self):
        assert pack_reasoning_fields("", [], {}) == {}
        assert pack_reasoning_fields("", [], {}, include_text=True) == {
            "reasoning_content": ""
        }

    def test_empty_details_dropped_unless_include_flag(self):
        assert pack_reasoning_fields("", [], {}, include_details=True) == {
            "reasoning_details": []
        }

    def test_extra_dict_merged_in(self):
        out = pack_reasoning_fields("t", [], {"reasoning": "r"})
        assert out == {"reasoning_content": "t", "reasoning": "r"}


class TestNormalizeStatefulAssistantFields:
    def test_no_stateful_fields_returns_input_unchanged(self):
        messages = [{"role": "assistant", "content": "hi"}]
        assert normalize_stateful_assistant_fields(messages) is messages

    def test_seen_field_backfilled_onto_other_assistant_messages(self):
        messages = [
            {"role": "assistant", "content": "a1", "reasoning_content": "r"},
            {"role": "user", "content": "u"},
            {"role": "assistant", "content": "a2"},  # missing reasoning_content
        ]
        out = normalize_stateful_assistant_fields(messages)
        assert out[2]["reasoning_content"] == ""  # back-filled to empty default
        assert out[1] == {"role": "user", "content": "u"}  # user untouched

    def test_list_default_field_backfilled_as_fresh_list(self):
        messages = [
            {"role": "assistant", "content": "a1", "reasoning_details": [{"x": 1}]},
            {"role": "assistant", "content": "a2"},
        ]
        out = normalize_stateful_assistant_fields(messages)
        assert out[1]["reasoning_details"] == []
        # must be an independent list, not aliased to the default
        out[1]["reasoning_details"].append("z")
        assert messages[0]["reasoning_details"] == [{"x": 1}]

    def test_already_complete_messages_left_unchanged(self):
        messages = [
            {"role": "assistant", "content": "a", "reasoning_content": "r"},
        ]
        out = normalize_stateful_assistant_fields(messages)
        # only one assistant msg, field already present → nothing changed
        assert out is messages


class TestToolCallConverters:
    def test_pending_call_becomes_native_tool_call(self):
        call = tool_call_from_pending({"id": "c1", "name": "bash", "arguments": "{}"})
        assert call.id == "c1" and call.name == "bash" and call.arguments == "{}"

    def test_sdk_message_tool_calls_converted(self):
        calls = tool_calls_from_message([_ToolCall("c1", "bash", '{"cmd": "ls"}')])
        assert len(calls) == 1
        assert calls[0].id == "c1"
        assert calls[0].name == "bash"
        assert calls[0].arguments == '{"cmd": "ls"}'

    def test_none_tool_calls_returns_empty_list(self):
        assert tool_calls_from_message(None) == []


class TestLogTokenUsage:
    def test_nonempty_usage_logged_at_info(self):
        handler = _RecordingHandler()
        kt_logger = logging.getLogger("kohakuterrarium")
        kt_logger.addHandler(handler)
        try:
            log_token_usage({"prompt_tokens": 10, "completion_tokens": 5})
        finally:
            kt_logger.removeHandler(handler)
        info = [r for r in handler.records if r.levelno == logging.INFO]
        assert len(info) == 1
        assert getattr(info[0], "prompt_tokens", None) == 10

    def test_empty_usage_logs_nothing(self):
        handler = _RecordingHandler()
        kt_logger = logging.getLogger("kohakuterrarium")
        kt_logger.addHandler(handler)
        try:
            log_token_usage({})
        finally:
            kt_logger.removeHandler(handler)
        assert handler.records == []


class _RecordingHandler(logging.Handler):
    """Capture records off the (non-propagating) kohakuterrarium logger."""

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


class TestMergeReasoningDetailStream:
    """Pins the streaming-merge fix for OpenRouter+Claude thinking blocks.

    OpenRouter streams ``reasoning_details`` as N+1 separate entries
    sharing ``index``: N partial-text entries followed by one entry
    carrying the HMAC ``signature``. A plain ``list.extend`` keeps them
    as N+1 entries; sending that back trips Anthropic's
    ``Invalid signature in thinking block`` 400 (verified live against
    ``anthropic/claude-haiku-4.5`` over OpenRouter). The merge collapses
    them by ``(type, index)`` so the assistant message carries one
    entry per logical block with text accumulated and signature intact.
    """

    def test_single_entry_appended_as_is(self):
        acc: list[dict] = []
        merge_reasoning_detail_stream(
            acc,
            {"type": "reasoning.text", "index": 0, "text": "hello", "format": "x"},
        )
        assert acc == [
            {"type": "reasoning.text", "index": 0, "text": "hello", "format": "x"}
        ]

    def test_text_chunks_concatenated_into_one_block(self):
        acc: list[dict] = []
        chunks = [
            {"type": "reasoning.text", "index": 0, "text": "I need to ", "format": "x"},
            {"type": "reasoning.text", "index": 0, "text": "calculate ", "format": "x"},
            {"type": "reasoning.text", "index": 0, "text": "17*23.", "format": "x"},
        ]
        for c in chunks:
            merge_reasoning_detail_stream(acc, c)
        assert len(acc) == 1
        assert acc[0]["text"] == "I need to calculate 17*23."

    def test_final_signature_merged_into_existing_block(self):
        acc: list[dict] = []
        merge_reasoning_detail_stream(
            acc,
            {"type": "reasoning.text", "index": 0, "text": "thinking…", "format": "x"},
        )
        merge_reasoning_detail_stream(
            acc,
            {
                "type": "reasoning.text",
                "index": 0,
                "signature": "ABCDEF0123",
                "format": "x",
            },
        )
        assert len(acc) == 1
        assert acc[0]["text"] == "thinking…"
        assert acc[0]["signature"] == "ABCDEF0123"
        # ``format`` carried through from the first entry, not duplicated.
        assert acc[0]["format"] == "x"

    def test_distinct_indexes_kept_as_separate_blocks(self):
        # Anthropic can emit multiple thinking blocks (e.g. between
        # tool calls). They share ``type`` but have different ``index``.
        acc: list[dict] = []
        for idx in (0, 1):
            merge_reasoning_detail_stream(
                acc,
                {
                    "type": "reasoning.text",
                    "index": idx,
                    "text": f"block{idx}",
                    "format": "x",
                },
            )
            merge_reasoning_detail_stream(
                acc,
                {
                    "type": "reasoning.text",
                    "index": idx,
                    "signature": f"sig{idx}",
                    "format": "x",
                },
            )
        assert len(acc) == 2
        sigs = {e["index"]: e["signature"] for e in acc}
        assert sigs == {0: "sig0", 1: "sig1"}

    def test_distinct_types_kept_separate(self):
        # ``reasoning.text`` vs ``reasoning.encrypted`` (hypothetical
        # future Anthropic shape) share ``index=0`` but are different
        # block types — must NOT collapse into one entry.
        acc: list[dict] = []
        merge_reasoning_detail_stream(
            acc, {"type": "reasoning.text", "index": 0, "text": "a"}
        )
        merge_reasoning_detail_stream(
            acc, {"type": "reasoning.encrypted", "index": 0, "data": "ZZZ"}
        )
        assert len(acc) == 2

    def test_non_dict_piece_ignored(self):
        acc: list[dict] = []
        merge_reasoning_detail_stream(acc, "garbage")  # type: ignore[arg-type]
        merge_reasoning_detail_stream(acc, None)  # type: ignore[arg-type]
        assert acc == []

    def test_signature_does_not_overwrite_with_empty(self):
        # The provider could emit a follow-up entry with an empty
        # signature field — that's not a "reset"; keep the last real
        # value.
        acc: list[dict] = []
        merge_reasoning_detail_stream(
            acc, {"type": "reasoning.text", "index": 0, "signature": "REAL"}
        )
        merge_reasoning_detail_stream(
            acc, {"type": "reasoning.text", "index": 0, "signature": ""}
        )
        assert acc[0]["signature"] == "REAL"

    def test_accumulator_isolated_from_provider_object(self):
        # The provider's delta object should not be aliased into the
        # accumulator — subsequent provider mutations would otherwise
        # corrupt our captured state.
        first = {"type": "reasoning.text", "index": 0, "text": "x"}
        acc: list[dict] = []
        merge_reasoning_detail_stream(acc, first)
        first["text"] = "MUTATED"
        assert acc[0]["text"] == "x"
