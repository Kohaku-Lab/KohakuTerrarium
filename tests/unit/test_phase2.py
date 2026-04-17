"""
Phase 2 Unit Tests - Stream Parsing

Tests for:
- ParseEvent types
- XML-style tag parsing
- StreamParser state machine
- Tool, sub-agent, and command detection

These tests run offline without API keys.
"""

import pytest

from kohakuterrarium.parsing import (
    BRACKET_FORMAT,
    XML_FORMAT,
    CommandEvent,
    DEFAULT_COMMANDS,
    DEFAULT_CONTENT_ARG_MAP,
    ParserConfig,
    ParserState,
    StreamParser,
    SubAgentCallEvent,
    TextEvent,
    ToolCallFormat,
    ToolCallEvent,
    extract_subagent_calls,
    extract_text,
    extract_tool_calls,
    is_action_event,
    is_text_event,
    parse_opening_tag,
    parse_closing_tag,
    parse_attributes,
    build_tool_args,
    is_tool_tag,
    is_command_tag,
)

# Test helper: common tools for parsing tests
TEST_KNOWN_TOOLS = {
    "bash",
    "python",
    "read",
    "write",
    "edit",
    "multi_edit",
    "glob",
    "grep",
    "tree",
}


def get_test_parser() -> StreamParser:
    """Create a parser configured for testing with common tools."""
    config = ParserConfig(known_tools=TEST_KNOWN_TOOLS)
    return StreamParser(config)


def parse_complete_with_tools(text: str) -> list:
    """Parse complete text with test tools configured."""
    parser = get_test_parser()
    events = parser.feed(text)
    events.extend(parser.flush())
    return events


class TestParseEvents:
    """Tests for ParseEvent types."""

    def test_text_event(self):
        """Test TextEvent creation."""
        event = TextEvent("hello")
        assert event.text == "hello"
        assert bool(event) is True

        empty = TextEvent("")
        assert bool(empty) is False

    def test_tool_call_event(self):
        """Test ToolCallEvent creation."""
        event = ToolCallEvent(name="bash", args={"command": "ls"})
        assert event.name == "bash"
        assert event.args["command"] == "ls"

    def test_subagent_call_event(self):
        """Test SubAgentCallEvent creation."""
        event = SubAgentCallEvent(name="explore", args={"query": "test"})
        assert event.name == "explore"
        assert event.args["query"] == "test"

    def test_command_event(self):
        """Test CommandEvent creation."""
        event = CommandEvent(command="info", args="bash")
        assert event.command == "info"
        assert "bash" in event.args

    def test_is_action_event(self):
        """Test is_action_event helper."""
        assert is_action_event(ToolCallEvent("test", {}))
        assert is_action_event(SubAgentCallEvent("test", {}))
        assert is_action_event(CommandEvent("test"))
        assert not is_action_event(TextEvent("hello"))

    def test_is_text_event(self):
        """Test is_text_event helper."""
        assert is_text_event(TextEvent("hello"))
        assert not is_text_event(ToolCallEvent("test", {}))


class TestXMLParsing:
    """Tests for XML-style tag parsing."""

    def test_parse_opening_tag(self):
        """Test parsing opening tags."""
        tag, attrs, self_closing = parse_opening_tag("<bash>")
        assert tag == "bash"
        assert attrs == {}
        assert self_closing is False

    def test_parse_opening_tag_with_attrs(self):
        """Test parsing opening tags with attributes."""
        tag, attrs, self_closing = parse_opening_tag('<read path="test.py"/>')
        assert tag == "read"
        assert attrs["path"] == "test.py"
        assert self_closing is True

    def test_parse_opening_tag_multi_attrs(self):
        """Test parsing opening tags with multiple attributes."""
        tag, attrs, self_closing = parse_opening_tag(
            '<grep path="src/" glob="*.py">pattern</grep>'
        )
        assert tag == "grep"
        assert attrs["path"] == "src/"
        assert attrs["glob"] == "*.py"

    def test_parse_closing_tag(self):
        """Test parsing closing tags."""
        tag = parse_closing_tag("</bash>")
        assert tag == "bash"

        tag = parse_closing_tag("</read>")
        assert tag == "read"

    def test_parse_attributes(self):
        """Test attribute parsing."""
        attrs = parse_attributes('path="test.py" limit="10"')
        assert attrs["path"] == "test.py"
        assert attrs["limit"] == "10"

    def test_is_tool_tag(self):
        """Test tool tag detection with known_tools set."""
        known_tools = {"bash", "python", "read", "write", "edit", "multi_edit"}
        assert is_tool_tag("bash", known_tools)
        assert is_tool_tag("python", known_tools)
        assert is_tool_tag("read", known_tools)
        assert not is_tool_tag("unknown", known_tools)
        # Without known_tools, nothing is a tool
        assert not is_tool_tag("bash", None)
        assert not is_tool_tag("bash", set())

    def test_is_command_tag(self):
        """Test command tag detection."""
        # Uses DEFAULT_COMMANDS when None provided
        assert is_command_tag("info")
        assert is_command_tag("read_job")
        assert not is_command_tag("bash")

    def test_build_tool_args_bash(self):
        """Test building args for bash tool."""
        args = build_tool_args("bash", {}, "ls -la")
        assert args["command"] == "ls -la"

    def test_build_tool_args_read(self):
        """Test building args for read tool."""
        args = build_tool_args("read", {"path": "test.py"}, "")
        assert args["path"] == "test.py"

    def test_build_tool_args_write(self):
        """Test building args for write tool."""
        args = build_tool_args("write", {"path": "test.py"}, "content here")
        assert args["path"] == "test.py"
        assert args["content"] == "content here"


class TestStreamParser:
    """Tests for StreamParser."""

    def test_empty_input(self):
        """Test parser with empty input."""
        parser = get_test_parser()
        events = parser.feed("")
        events.extend(parser.flush())
        assert len(events) == 0

    def test_text_only(self):
        """Test parser with text only (no blocks)."""
        parser = get_test_parser()
        events = parser.feed("Hello world!")
        events.extend(parser.flush())

        text = extract_text(events)
        assert "Hello world!" in text

    def test_single_tool_call(self):
        """Test parsing a single tool call."""
        text = """Some text before.

[/bash]ls -la[bash/]

Some text after."""

        events = parse_complete_with_tools(text)
        tools = extract_tool_calls(events)

        assert len(tools) == 1
        assert tools[0].name == "bash"
        assert tools[0].args.get("command") == "ls -la"

    def test_tool_with_attributes(self):
        """Test parsing tool call with attributes."""
        text = "[/read]@@path=src/main.py[read/]"

        events = parse_complete_with_tools(text)
        tools = extract_tool_calls(events)

        assert len(tools) == 1
        assert tools[0].name == "read"
        assert tools[0].args.get("path") == "src/main.py"

    def test_tool_with_attrs_and_content(self):
        """Test parsing tool with both attributes and content."""
        text = """[/write]
@@path=test.py
def hello():
    print("Hello")
[write/]"""

        events = parse_complete_with_tools(text)
        tools = extract_tool_calls(events)

        assert len(tools) == 1
        assert tools[0].name == "write"
        assert tools[0].args.get("path") == "test.py"
        assert "def hello" in tools[0].args.get("content", "")

    def test_multiple_tool_calls(self):
        """Test parsing multiple tool calls."""
        text = """[/bash]ls[bash/]

[/bash]pwd[bash/]"""

        events = parse_complete_with_tools(text)
        tools = extract_tool_calls(events)

        assert len(tools) == 2
        assert tools[0].args.get("command") == "ls"
        assert tools[1].args.get("command") == "pwd"

    def test_command(self):
        """Test parsing framework command."""
        text = "Check this: [/info]bash[info/]"

        events = parse_complete_with_tools(text)
        commands = [e for e in events if isinstance(e, CommandEvent)]

        assert len(commands) == 1
        assert commands[0].command == "info"
        assert "bash" in commands[0].args

    def test_read_job_command(self):
        """Test parsing read_job as a framework command."""
        text = "Check this: [/read_job]job_123[read_job/]"

        events = parse_complete_with_tools(text)
        commands = [e for e in events if isinstance(e, CommandEvent)]

        assert len(commands) == 1
        assert commands[0].command == "read_job"
        assert commands[0].args == "job_123"

    def test_read_remains_tool_not_framework_command(self):
        """Test that read stays classified as the file-read tool."""
        text = "[/read]README.md[read/]"

        events = parse_complete_with_tools(text)
        tools = extract_tool_calls(events)
        commands = [e for e in events if isinstance(e, CommandEvent)]

        assert len(tools) == 1
        assert tools[0].name == "read"
        assert tools[0].args == {"path": "README.md"}
        assert len(commands) == 0

    def test_streaming_chunks(self):
        """Test that streaming works correctly."""
        parser = get_test_parser()

        # Feed in small chunks
        chunks = ["[/ba", "sh]l", "s -la[bash/]"]

        all_events = []
        for chunk in chunks:
            events = parser.feed(chunk)
            all_events.extend(events)
        all_events.extend(parser.flush())

        tools = extract_tool_calls(all_events)
        assert len(tools) == 1
        assert tools[0].args.get("command") == "ls -la"

    def test_character_by_character(self):
        """Test feeding character by character."""
        text = "[/bash]test[bash/]"
        parser = get_test_parser()

        all_events = []
        for char in text:
            events = parser.feed(char)
            all_events.extend(events)
        all_events.extend(parser.flush())

        tools = extract_tool_calls(all_events)
        assert len(tools) == 1

    def test_parser_state(self):
        """Test parser state tracking."""
        parser = get_test_parser()

        assert parser.state == ParserState.NORMAL

        parser.feed("[/bash]")
        assert parser.state == ParserState.IN_BLOCK

        parser.feed("test[bash/]")
        parser.flush()
        assert parser.state == ParserState.NORMAL

    def test_incomplete_block(self):
        """Test handling of incomplete block at stream end."""
        parser = get_test_parser()

        events = parser.feed("<bash>test")
        # No tool event yet (block not closed)
        tools = extract_tool_calls(events)
        assert len(tools) == 0

        # Flush should handle incomplete block
        final_events = parser.flush()
        # Still no tool event (incomplete)
        tools = extract_tool_calls(final_events)
        assert len(tools) == 0

    def test_false_marker(self):
        """Test that false markers are handled as text."""
        text = "Use < for less than and > for greater than"

        events = parse_complete_with_tools(text)
        text_content = extract_text(events)

        assert "<" in text_content or ">" in text_content

    def test_mixed_content(self):
        """Test response with various content types."""
        text = """Text before

[/bash]ls[bash/]

[/info]bash[info/]

Text after"""

        events = parse_complete_with_tools(text)

        tools = extract_tool_calls(events)
        commands = [e for e in events if isinstance(e, CommandEvent)]

        assert len(tools) == 1
        assert len(commands) == 1


class TestParserConfig:
    """Tests for ParserConfig."""

    def test_default_config(self):
        """Test default configuration."""
        config = ParserConfig()
        assert config.emit_block_events is False

    def test_custom_config(self):
        """Test custom configuration."""
        config = ParserConfig(
            emit_block_events=True,
            buffer_text=False,
        )
        assert config.emit_block_events is True
        assert config.buffer_text is False


class TestExtractFunctions:
    """Tests for extraction helper functions."""

    def test_extract_tool_calls(self):
        """Test extract_tool_calls function."""
        events = [
            TextEvent("hello"),
            ToolCallEvent("tool1", {}),
            TextEvent("world"),
            ToolCallEvent("tool2", {}),
        ]

        tools = extract_tool_calls(events)
        assert len(tools) == 2
        assert tools[0].name == "tool1"
        assert tools[1].name == "tool2"

    def test_extract_subagent_calls(self):
        """Test extract_subagent_calls function."""
        events = [
            TextEvent("hello"),
            SubAgentCallEvent("agent1", {}),
        ]

        subagents = extract_subagent_calls(events)
        assert len(subagents) == 1
        assert subagents[0].name == "agent1"

    def test_extract_text(self):
        """Test extract_text function."""
        events = [
            TextEvent("Hello "),
            ToolCallEvent("tool", {}),
            TextEvent("World"),
        ]

        text = extract_text(events)
        assert text == "Hello World"


class TestKnownTags:
    """Tests for known tool and command tags."""

    def test_default_content_arg_map(self):
        """Test that default content arg map has expected tools."""
        expected = {"bash", "python", "read", "write", "edit", "glob", "grep", "tree"}
        assert expected.issubset(set(DEFAULT_CONTENT_ARG_MAP.keys()))
        assert "multi_edit" not in DEFAULT_CONTENT_ARG_MAP

    def test_known_commands(self):
        """Test that all expected commands are in DEFAULT_COMMANDS."""
        expected = {"info", "read_job"}
        assert expected.issubset(DEFAULT_COMMANDS)


class TestXMLFormat:
    """Tests for XML-style tool call format."""

    def test_xml_tool_call(self):
        """Test basic XML format tool call."""
        config = ParserConfig(tool_format=XML_FORMAT, known_tools={"bash"})
        parser = StreamParser(config)
        events = parser.feed("<bash>ls -la</bash>")
        events.extend(parser.flush())
        tools = extract_tool_calls(events)
        assert len(tools) == 1
        assert tools[0].name == "bash"
        assert tools[0].args.get("command") == "ls -la"

    def test_xml_with_attributes(self):
        """Test XML format with inline attributes and self-closing tag."""
        config = ParserConfig(tool_format=XML_FORMAT, known_tools={"read"})
        parser = StreamParser(config)
        events = parser.feed('<read path="main.py"/>')
        events.extend(parser.flush())
        tools = extract_tool_calls(events)
        assert len(tools) == 1
        assert tools[0].args.get("path") == "main.py"

    def test_xml_mixed_html_not_parsed(self):
        """HTML tags that aren't known tools should pass through as text."""
        config = ParserConfig(tool_format=XML_FORMAT, known_tools={"bash"})
        parser = StreamParser(config)
        events = parser.feed("<div>hello</div>")
        events.extend(parser.flush())
        tools = extract_tool_calls(events)
        assert len(tools) == 0  # div is not a known tool
        text = extract_text(events)
        assert "hello" in text

    def test_xml_with_attrs_and_content(self):
        """Test XML format with both attributes and body content."""
        config = ParserConfig(tool_format=XML_FORMAT, known_tools={"write"})
        parser = StreamParser(config)
        events = parser.feed('<write path="test.py">print("hello")</write>')
        events.extend(parser.flush())
        tools = extract_tool_calls(events)
        assert len(tools) == 1
        assert tools[0].args.get("path") == "test.py"
        assert tools[0].args.get("content") == 'print("hello")'

    def test_xml_streaming_chunks(self):
        """Test XML format with character-by-character streaming."""
        config = ParserConfig(tool_format=XML_FORMAT, known_tools={"bash"})
        parser = StreamParser(config)
        text = "<bash>echo hi</bash>"
        all_events = []
        for char in text:
            all_events.extend(parser.feed(char))
        all_events.extend(parser.flush())
        tools = extract_tool_calls(all_events)
        assert len(tools) == 1
        assert tools[0].args.get("command") == "echo hi"

    def test_xml_multiple_tool_calls(self):
        """Test multiple XML tool calls in sequence."""
        config = ParserConfig(tool_format=XML_FORMAT, known_tools={"bash"})
        parser = StreamParser(config)
        events = parser.feed("<bash>ls</bash>\n<bash>pwd</bash>")
        events.extend(parser.flush())
        tools = extract_tool_calls(events)
        assert len(tools) == 2
        assert tools[0].args.get("command") == "ls"
        assert tools[1].args.get("command") == "pwd"

    def test_xml_text_around_tool(self):
        """Test that text before and after XML tool calls is preserved."""
        config = ParserConfig(tool_format=XML_FORMAT, known_tools={"bash"})
        parser = StreamParser(config)
        events = parser.feed("before <bash>ls</bash> after")
        events.extend(parser.flush())
        tools = extract_tool_calls(events)
        assert len(tools) == 1
        text = extract_text(events)
        assert "before" in text
        assert "after" in text

    def test_xml_command(self):
        """Test XML format with a framework command."""
        config = ParserConfig(tool_format=XML_FORMAT, known_tools={"bash"})
        parser = StreamParser(config)
        events = parser.feed("<info>bash</info>")
        events.extend(parser.flush())
        commands = [e for e in events if isinstance(e, CommandEvent)]
        assert len(commands) == 1
        assert commands[0].command == "info"
        assert commands[0].args == "bash"

    def test_xml_self_closing_no_content(self):
        """Test self-closing XML tag produces tool call with no body."""
        config = ParserConfig(tool_format=XML_FORMAT, known_tools={"read"})
        parser = StreamParser(config)
        events = parser.feed('<read path="src/main.py"/>')
        events.extend(parser.flush())
        tools = extract_tool_calls(events)
        assert len(tools) == 1
        assert tools[0].args.get("path") == "src/main.py"
        # No content/body arg should be set
        assert "content" not in tools[0].args

    def test_xml_false_close_tag_in_normal(self):
        """Test that </tag> in normal state (no open block) is just text."""
        config = ParserConfig(tool_format=XML_FORMAT, known_tools={"bash"})
        parser = StreamParser(config)
        events = parser.feed("some </bash> text")
        events.extend(parser.flush())
        tools = extract_tool_calls(events)
        assert len(tools) == 0
        text = extract_text(events)
        assert "</bash>" in text

    def test_bracket_format_unchanged(self):
        """Verify default bracket format still works identically."""
        config = ParserConfig(tool_format=BRACKET_FORMAT, known_tools={"bash", "write"})
        parser = StreamParser(config)
        events = parser.feed("[/bash]ls -la[bash/]")
        events.extend(parser.flush())
        tools = extract_tool_calls(events)
        assert len(tools) == 1
        assert tools[0].name == "bash"
        assert tools[0].args.get("command") == "ls -la"

    def test_tool_call_format_presets(self):
        """Test that preset formats have correct values."""
        assert BRACKET_FORMAT.start_char == "["
        assert BRACKET_FORMAT.end_char == "]"
        assert BRACKET_FORMAT.slash_means_open is True
        assert BRACKET_FORMAT.arg_style == "line"

        assert XML_FORMAT.start_char == "<"
        assert XML_FORMAT.end_char == ">"
        assert XML_FORMAT.slash_means_open is False
        assert XML_FORMAT.arg_style == "inline"

    def test_tool_call_format_frozen(self):
        """Test that ToolCallFormat is immutable."""
        fmt = ToolCallFormat()
        with pytest.raises(AttributeError):
            fmt.start_char = "{"
