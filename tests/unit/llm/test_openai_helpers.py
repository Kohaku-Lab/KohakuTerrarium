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
