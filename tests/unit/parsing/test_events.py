"""Unit tests for :mod:`kohakuterrarium.parsing.events`."""

from kohakuterrarium.parsing.events import (
    AssistantImageEvent,
    BlockEndEvent,
    BlockStartEvent,
    CommandEvent,
    CommandResultEvent,
    OutputCallEvent,
    SubAgentCallEvent,
    TextEvent,
    ToolCallEvent,
    is_action_event,
    is_text_event,
)


class TestTextEvent:
    def test_holds_text(self):
        e = TextEvent("hello")
        assert e.text == "hello"

    def test_bool_truthy_with_content(self):
        assert bool(TextEvent("hi")) is True

    def test_bool_falsy_when_empty(self):
        assert bool(TextEvent("")) is False


class TestToolCallEvent:
    def test_defaults(self):
        e = ToolCallEvent("bash")
        assert e.name == "bash"
        assert e.args == {}
        assert e.raw == ""

    def test_args_and_raw(self):
        e = ToolCallEvent("bash", {"command": "ls"}, raw="[/bash]ls[bash/]")
        assert e.args == {"command": "ls"}
        assert e.raw == "[/bash]ls[bash/]"

    def test_repr_includes_name_and_args(self):
        e = ToolCallEvent("bash", {"command": "ls"})
        r = repr(e)
        assert "bash" in r
        assert "command" in r
        # ``raw`` is intentionally NOT in repr to keep it short.
        assert "raw=" not in r

    def test_args_field_is_per_instance(self):
        a = ToolCallEvent("a")
        b = ToolCallEvent("b")
        a.args["x"] = 1
        assert b.args == {}


class TestSubAgentCallEvent:
    def test_defaults(self):
        e = SubAgentCallEvent("agent")
        assert e.name == "agent"
        assert e.args == {}
        assert e.raw == ""

    def test_repr(self):
        e = SubAgentCallEvent("planner", {"task": "x"})
        r = repr(e)
        assert "planner" in r
        assert "task" in r


class TestCommandEvent:
    def test_defaults(self):
        e = CommandEvent("info")
        assert e.command == "info"
        assert e.args == ""
        assert e.raw == ""

    def test_repr(self):
        e = CommandEvent("info", "bash")
        r = repr(e)
        assert "info" in r
        assert "bash" in r


class TestOutputCallEvent:
    def test_defaults(self):
        e = OutputCallEvent("discord")
        assert e.target == "discord"
        assert e.content == ""

    def test_repr_truncates_long_content(self):
        e = OutputCallEvent("tts", content="x" * 200)
        r = repr(e)
        # Repr keeps only first 50 chars + ``...``.
        assert "..." in r
        assert "tts" in r


class TestBlockEvents:
    def test_block_start_default_name(self):
        e = BlockStartEvent("tool")
        assert e.block_type == "tool"
        assert e.name is None

    def test_block_end_default_success(self):
        e = BlockEndEvent("tool")
        assert e.block_type == "tool"
        assert e.success is True
        assert e.error is None

    def test_block_end_error_path(self):
        e = BlockEndEvent("tool", success=False, error="boom")
        assert e.success is False
        assert e.error == "boom"


class TestCommandResultEvent:
    def test_success_default(self):
        e = CommandResultEvent("info", content="ok")
        assert e.command == "info"
        assert e.content == "ok"
        assert e.error is None

    def test_error_path(self):
        e = CommandResultEvent("read", error="not found")
        assert e.error == "not found"
        assert e.content == ""


class TestAssistantImageEvent:
    def test_minimal(self):
        e = AssistantImageEvent(url="/api/sessions/x/artifacts/img.png")
        assert e.url.endswith("img.png")
        assert e.detail == "auto"
        assert e.source_type is None
        assert e.source_name is None
        assert e.revised_prompt is None

    def test_full(self):
        e = AssistantImageEvent(
            url="x",
            detail="high",
            source_type="image_gen",
            source_name="abc.png",
            revised_prompt="a cat",
        )
        assert e.detail == "high"
        assert e.source_type == "image_gen"
        assert e.source_name == "abc.png"
        assert e.revised_prompt == "a cat"


class TestEventClassifiers:
    def test_is_action_event_for_tool(self):
        assert is_action_event(ToolCallEvent("bash")) is True

    def test_is_action_event_for_subagent(self):
        assert is_action_event(SubAgentCallEvent("agent")) is True

    def test_is_action_event_for_command(self):
        assert is_action_event(CommandEvent("info")) is True

    def test_is_action_event_false_for_text(self):
        assert is_action_event(TextEvent("hi")) is False

    def test_is_action_event_false_for_command_result(self):
        # CommandResult is the OUTPUT of running a command, not an
        # action request — must not be classified as actionable.
        assert is_action_event(CommandResultEvent("info")) is False

    def test_is_action_event_false_for_block_markers(self):
        assert is_action_event(BlockStartEvent("tool")) is False
        assert is_action_event(BlockEndEvent("tool")) is False

    def test_is_action_event_false_for_output(self):
        assert is_action_event(OutputCallEvent("discord")) is False

    def test_is_text_event(self):
        assert is_text_event(TextEvent("hi")) is True
        assert is_text_event(ToolCallEvent("bash")) is False
