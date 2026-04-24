"""
Integration tests for tool format configuration flow.

Tests that tool_format config values flow correctly through:
  config loading -> AgentConfig -> _resolve_tool_format -> ParserConfig

Only tests that can run without API keys or external services.
"""

from pathlib import Path

from kohakuterrarium.core.config import AgentConfig, load_agent_config
from kohakuterrarium.parsing.format import BRACKET_FORMAT, XML_FORMAT, ToolCallFormat
from kohakuterrarium.parsing.patterns import ParserConfig
from kohakuterrarium.parsing.state_machine import parse_full


def _write_yaml_config(tmpdir: Path, yaml_content: str) -> Path:
    """Write a YAML config file and return the agent folder path."""
    config_path = tmpdir / "config.yaml"
    config_path.write_text(yaml_content, encoding="utf-8")
    return tmpdir


class TestConfigLoadingToolFormat:
    """Tests that tool_format is correctly loaded from YAML config files."""

    def test_load_bracket_string(self, tmp_path):
        """Loading 'bracket' string from YAML config."""
        agent_dir = _write_yaml_config(
            tmp_path,
            """
name: test-agent
controller:
  tool_format: bracket
""",
        )
        config = load_agent_config(agent_dir)
        assert config.tool_format == "bracket"

    def test_load_xml_string(self, tmp_path):
        """Loading 'xml' string from YAML config."""
        agent_dir = _write_yaml_config(
            tmp_path,
            """
name: test-agent
controller:
  tool_format: xml
""",
        )
        config = load_agent_config(agent_dir)
        assert config.tool_format == "xml"

    def test_load_native_string(self, tmp_path):
        """Loading 'native' string from YAML config."""
        agent_dir = _write_yaml_config(
            tmp_path,
            """
name: test-agent
controller:
  tool_format: native
""",
        )
        config = load_agent_config(agent_dir)
        assert config.tool_format == "native"

    def test_load_custom_dict(self, tmp_path):
        """Loading custom dict from YAML config."""
        agent_dir = _write_yaml_config(
            tmp_path,
            """
name: test-agent
controller:
  tool_format:
    start_char: "{"
    end_char: "}"
    slash_means_open: true
    arg_style: line
    arg_prefix: "@@"
""",
        )
        config = load_agent_config(agent_dir)
        assert isinstance(config.tool_format, dict)
        assert config.tool_format["start_char"] == "{"
        assert config.tool_format["end_char"] == "}"

    def test_load_default_when_missing(self, tmp_path):
        """Default tool_format is 'bracket' when not specified."""
        agent_dir = _write_yaml_config(
            tmp_path,
            """
name: test-agent
controller:
  model: test-model
""",
        )
        config = load_agent_config(agent_dir)
        assert config.tool_format == "bracket"

    def test_load_default_no_controller_section(self, tmp_path):
        """Default tool_format is 'bracket' when controller section is absent."""
        agent_dir = _write_yaml_config(
            tmp_path,
            """
name: test-agent
""",
        )
        config = load_agent_config(agent_dir)
        assert config.tool_format == "bracket"


class TestFormatToParserConfig:
    """Tests that resolved format flows into ParserConfig correctly."""

    def test_parser_config_default_bracket(self):
        """ParserConfig defaults to BRACKET_FORMAT."""
        pc = ParserConfig()
        assert pc.tool_format == BRACKET_FORMAT

    def test_parser_config_accepts_xml(self):
        """ParserConfig accepts XML_FORMAT."""
        pc = ParserConfig(tool_format=XML_FORMAT)
        assert pc.tool_format == XML_FORMAT

    def test_parser_config_accepts_custom(self):
        """ParserConfig accepts custom ToolCallFormat."""
        custom = ToolCallFormat(start_char="{", end_char="}")
        pc = ParserConfig(tool_format=custom)
        assert pc.tool_format.start_char == "{"
        assert pc.tool_format.end_char == "}"


class TestBracketFormatEndToEnd:
    """End-to-end tests for bracket format parsing (existing behavior)."""

    def test_simple_tool_call(self):
        """Bracket format tool call parses correctly."""
        config = ParserConfig(
            known_tools={"bash"},
            tool_format=BRACKET_FORMAT,
        )
        text = "[/bash]ls -la[bash/]"
        events = parse_full(text, config)

        tool_events = [e for e in events if hasattr(e, "name") and e.name == "bash"]
        assert len(tool_events) == 1
        assert tool_events[0].args.get("command") == "ls -la"

    def test_tool_call_with_args(self):
        """Bracket format tool call with @@ args parses correctly."""
        config = ParserConfig(
            known_tools={"write"},
            tool_format=BRACKET_FORMAT,
        )
        text = "[/write]\n@@path=test.py\nhello world\n[write/]"
        events = parse_full(text, config)

        tool_events = [e for e in events if hasattr(e, "name") and e.name == "write"]
        assert len(tool_events) == 1
        assert tool_events[0].args.get("path") == "test.py"
        assert tool_events[0].args.get("content") == "hello world"

    def test_text_around_tool_call(self):
        """Text before and after tool calls is preserved."""
        config = ParserConfig(
            known_tools={"bash"},
            tool_format=BRACKET_FORMAT,
        )
        text = "Let me run this: [/bash]pwd[bash/] and check."
        events = parse_full(text, config)

        from kohakuterrarium.parsing.events import TextEvent, ToolCallEvent

        text_events = [e for e in events if isinstance(e, TextEvent)]
        tool_events = [e for e in events if isinstance(e, ToolCallEvent)]
        assert len(tool_events) == 1
        assert len(text_events) >= 1
        # Check that "Let me run this: " is in the text output
        all_text = "".join(e.text for e in text_events)
        assert "Let me run this: " in all_text

    def test_multiple_tool_calls(self):
        """Multiple bracket format tool calls in one stream."""
        config = ParserConfig(
            known_tools={"bash", "read"},
            tool_format=BRACKET_FORMAT,
        )
        text = "[/bash]ls[bash/]\n[/read]file.py[read/]"
        events = parse_full(text, config)

        from kohakuterrarium.parsing.events import ToolCallEvent

        tool_events = [e for e in events if isinstance(e, ToolCallEvent)]
        assert len(tool_events) == 2
        assert tool_events[0].name == "bash"
        assert tool_events[1].name == "read"


class TestResolutionFlowIntegration:
    """Tests for full resolution flow: config -> resolve -> ToolCallFormat."""

    def _make_mixin(self, tool_format):
        """Create a minimal AgentInitMixin for testing."""
        from kohakuterrarium.bootstrap.agent_init import AgentInitMixin

        mixin = object.__new__(AgentInitMixin)
        mixin.config = AgentConfig(name="test", tool_format=tool_format)
        return mixin

    def test_bracket_config_to_parser(self):
        """Bracket config resolves and can be used with ParserConfig."""
        mixin = self._make_mixin("bracket")
        fmt = mixin._resolve_tool_format()
        pc = ParserConfig(tool_format=fmt)
        assert pc.tool_format == BRACKET_FORMAT

    def test_xml_config_to_parser(self):
        """XML config resolves and can be used with ParserConfig."""
        mixin = self._make_mixin("xml")
        fmt = mixin._resolve_tool_format()
        pc = ParserConfig(tool_format=fmt)
        assert pc.tool_format == XML_FORMAT

    def test_custom_dict_config_to_parser(self):
        """Custom dict config resolves and can be used with ParserConfig."""
        mixin = self._make_mixin(
            {
                "start_char": "{",
                "end_char": "}",
                "slash_means_open": True,
                "arg_style": "line",
                "arg_prefix": "@@",
            }
        )
        fmt = mixin._resolve_tool_format()
        pc = ParserConfig(tool_format=fmt)
        assert pc.tool_format.start_char == "{"
        assert pc.tool_format.end_char == "}"

    def test_native_config_returns_none(self):
        """Native config resolves to None, not passed to ParserConfig."""
        mixin = self._make_mixin("native")
        fmt = mixin._resolve_tool_format()
        assert fmt is None

    def test_yaml_to_resolution(self, tmp_path):
        """Full flow: YAML file -> load_agent_config -> _resolve_tool_format."""
        agent_dir = _write_yaml_config(
            tmp_path,
            """
name: test-agent
controller:
  tool_format: xml
""",
        )
        config = load_agent_config(agent_dir)

        from kohakuterrarium.bootstrap.agent_init import AgentInitMixin

        mixin = object.__new__(AgentInitMixin)
        mixin.config = config
        fmt = mixin._resolve_tool_format()
        assert fmt == XML_FORMAT

    def test_yaml_custom_dict_to_resolution(self, tmp_path):
        """Full flow: YAML custom dict -> load -> resolve -> ToolCallFormat."""
        agent_dir = _write_yaml_config(
            tmp_path,
            """
name: test-agent
controller:
  tool_format:
    start_char: "("
    end_char: ")"
    slash_means_open: true
    arg_style: line
    arg_prefix: "!!"
    arg_kv_sep: ":"
""",
        )
        config = load_agent_config(agent_dir)

        from kohakuterrarium.bootstrap.agent_init import AgentInitMixin

        mixin = object.__new__(AgentInitMixin)
        mixin.config = config
        fmt = mixin._resolve_tool_format()
        assert isinstance(fmt, ToolCallFormat)
        assert fmt.start_char == "("
        assert fmt.end_char == ")"
        assert fmt.arg_prefix == "!!"
        assert fmt.arg_kv_sep == ":"
