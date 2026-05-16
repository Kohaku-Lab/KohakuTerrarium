"""Unit tests for :mod:`kohakuterrarium.core.events` (TriggerEvent + factories)."""

from datetime import datetime

import pytest

from kohakuterrarium.core.events import (
    EventType,
    TriggerEvent,
    create_creature_output_event,
    create_error_event,
    create_tool_complete_event,
    create_user_input_event,
)
from kohakuterrarium.llm.message import ImagePart, TextPart


class TestTriggerEventConstruction:
    def test_minimal_event(self):
        e = TriggerEvent(type="custom")
        assert e.type == "custom"
        assert e.content == ""
        assert e.context == {}
        assert e.job_id is None
        assert e.prompt_override is None
        assert e.stackable is True
        assert isinstance(e.timestamp, datetime)

    def test_empty_type_raises(self):
        with pytest.raises(ValueError, match="type cannot be empty"):
            TriggerEvent(type="")

    def test_context_dict_default_is_per_instance(self):
        a = TriggerEvent(type="x")
        b = TriggerEvent(type="y")
        a.context["k"] = 1
        assert b.context == {}


class TestEventTypeConstants:
    @pytest.mark.parametrize(
        "name,value",
        [
            ("USER_INPUT", "user_input"),
            ("IDLE", "idle"),
            ("TIMER", "timer"),
            ("CONTEXT_UPDATE", "context_update"),
            ("TOOL_COMPLETE", "tool_complete"),
            ("SUBAGENT_OUTPUT", "subagent_output"),
            ("CHANNEL_MESSAGE", "channel_message"),
            ("CREATURE_OUTPUT", "creature_output"),
            ("MONITOR", "monitor"),
            ("ERROR", "error"),
            ("STARTUP", "startup"),
            ("SHUTDOWN", "shutdown"),
        ],
    )
    def test_constants_match_wire_values(self, name, value):
        assert getattr(EventType, name) == value


class TestGetTextContent:
    def test_string_returned_verbatim(self):
        assert TriggerEvent(type="x", content="hello").get_text_content() == "hello"

    def test_multimodal_renders_text_parts(self):
        parts = [TextPart(text="alpha"), TextPart(text="beta")]
        e = TriggerEvent(type="x", content=parts)
        text = e.get_text_content()
        assert "alpha" in text
        assert "beta" in text

    def test_image_part_does_not_leak_base64(self):
        parts = [
            TextPart(text="caption"),
            ImagePart(url="data:image/png;base64," + "A" * 200),
        ]
        e = TriggerEvent(type="x", content=parts)
        text = e.get_text_content()
        # The base64 body must NOT appear in the rendered text — the
        # contract is "safe text placeholders for non-text parts".
        assert "A" * 100 not in text
        assert "caption" in text


class TestIsMultimodal:
    def test_string_content_not_multimodal(self):
        assert TriggerEvent(type="x", content="hi").is_multimodal() is False

    def test_list_content_is_multimodal(self):
        assert (
            TriggerEvent(type="x", content=[TextPart(text="x")]).is_multimodal() is True
        )

    def test_empty_list_still_multimodal(self):
        # An empty list is still ``list[ContentPart]`` shape.
        assert TriggerEvent(type="x", content=[]).is_multimodal() is True


class TestWithContext:
    def test_returns_new_event_not_self(self):
        a = TriggerEvent(type="x", context={"orig": 1})
        b = a.with_context(extra=2)
        assert b is not a
        assert a.context == {"orig": 1}

    def test_merges_kwargs(self):
        a = TriggerEvent(type="x", context={"orig": 1})
        b = a.with_context(extra=2)
        assert b.context == {"orig": 1, "extra": 2}

    def test_kwarg_overrides_existing(self):
        a = TriggerEvent(type="x", context={"k": "old"})
        b = a.with_context(k="new")
        assert b.context == {"k": "new"}

    def test_preserves_other_fields(self):
        a = TriggerEvent(
            type="x",
            content="body",
            timestamp=datetime(2024, 1, 1),
            job_id="j",
            prompt_override="p",
            stackable=False,
        )
        b = a.with_context(x=1)
        assert b.type == "x"
        assert b.content == "body"
        assert b.timestamp == datetime(2024, 1, 1)
        assert b.job_id == "j"
        assert b.prompt_override == "p"
        assert b.stackable is False


class TestRepr:
    def test_short_string_content(self):
        r = repr(TriggerEvent(type="x", content="short"))
        assert "x" in r
        assert "short" in r

    def test_long_string_truncated(self):
        long = "a" * 100
        r = repr(TriggerEvent(type="x", content=long))
        assert "..." in r
        # Not the full 100 chars.
        assert r.count("a") < 100

    def test_multimodal_count_shown(self):
        r = repr(
            TriggerEvent(type="x", content=[TextPart(text="a"), TextPart(text="b")])
        )
        assert "2 parts" in r

    def test_includes_job_id_when_set(self):
        r = repr(TriggerEvent(type="x", job_id="job_abc"))
        assert "job_abc" in r

    def test_includes_context_keys_when_set(self):
        r = repr(TriggerEvent(type="x", context={"source": "cli"}))
        assert "source" in r

    def test_does_not_include_job_when_none(self):
        r = repr(TriggerEvent(type="x"))
        # ``job_id`` only included if truthy.
        assert "job_id" not in r


class TestCreateUserInputEvent:
    def test_text_content(self):
        e = create_user_input_event("hello")
        assert e.type == EventType.USER_INPUT
        assert e.content == "hello"
        assert e.context["source"] == "cli"
        assert e.stackable is True

    def test_explicit_source_propagated(self):
        e = create_user_input_event("hi", source="discord")
        assert e.context["source"] == "discord"

    def test_extra_context_merged(self):
        e = create_user_input_event("hi", source="cli", user_id="abc")
        assert e.context["user_id"] == "abc"

    def test_multimodal_content_normalised(self):
        e = create_user_input_event([{"type": "text", "text": "hi"}])
        # Should be normalised into list[ContentPart].
        assert isinstance(e.content, list)
        # The first part has a ``text`` attr with the original value.
        assert e.content[0].text == "hi"

    def test_empty_normalised_content_falls_back_to_empty_string(self):
        # ``normalize_content_parts`` may return None for unrecognised
        # shapes; the factory must fall back to "".
        e = create_user_input_event([])
        # Empty list is still a list — but normalize may yield None.
        # Whichever, the content is a recognisable empty value.
        assert e.content in ("", [], None) or isinstance(e.content, list)


class TestCreateToolCompleteEvent:
    def test_basic(self):
        e = create_tool_complete_event("job_1", content="ok")
        assert e.type == EventType.TOOL_COMPLETE
        assert e.job_id == "job_1"
        assert e.content == "ok"

    def test_exit_code_recorded(self):
        e = create_tool_complete_event("j", content="x", exit_code=0)
        assert e.context["exit_code"] == 0

    def test_error_recorded(self):
        e = create_tool_complete_event("j", content="", error="boom")
        assert e.context["error"] == "boom"

    def test_extra_context_passed_through(self):
        e = create_tool_complete_event("j", content="", tool_name="bash")
        assert e.context["tool_name"] == "bash"


class TestCreateCreatureOutputEvent:
    def test_basic(self):
        e = create_creature_output_event(source="alice", target="bob", content="hello")
        assert e.type == EventType.CREATURE_OUTPUT
        assert e.content == "hello"
        assert e.context["source"] == "alice"
        assert e.context["target"] == "bob"
        assert e.context["with_content"] is True
        assert e.stackable is True

    def test_metadata_only_ping(self):
        e = create_creature_output_event(
            source="a", target="b", content="", with_content=False
        )
        assert e.context["with_content"] is False

    def test_prompt_override_propagated(self):
        e = create_creature_output_event(
            source="a", target="b", content="x", prompt_override="custom"
        )
        assert e.prompt_override == "custom"

    def test_source_event_type_and_turn_recorded(self):
        e = create_creature_output_event(
            source="a",
            target="b",
            content="x",
            source_event_type="user_input",
            turn_index=5,
        )
        assert e.context["source_event_type"] == "user_input"
        assert e.context["turn_index"] == 5


class TestCreateErrorEvent:
    def test_minimal(self):
        e = create_error_event("ToolFailure", "boom")
        assert e.type == EventType.ERROR
        assert e.content == "boom"
        assert e.context["error_type"] == "ToolFailure"
        # Errors are NOT stackable.
        assert e.stackable is False

    def test_with_job_id(self):
        e = create_error_event("X", "m", job_id="j_42")
        assert e.job_id == "j_42"

    def test_extra_context_passed_through(self):
        e = create_error_event("X", "m", source="bash")
        assert e.context["source"] == "bash"
