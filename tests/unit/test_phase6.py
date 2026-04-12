"""
Phase 6 Unit Tests - Sub-Agent System

Tests for:
- SubAgentConfig
- SubAgent base class
- SubAgentManager
- Builtin sub-agent configurations
- Agent tag parsing
"""

from kohakuterrarium.builtins.subagents import (
    BUILTIN_SUBAGENTS,
    get_builtin_subagent_config,
    list_builtin_subagents,
)
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.modules.subagent import (
    OutputTarget,
    SubAgent,
    SubAgentConfig,
    SubAgentInfo,
    SubAgentManager,
    SubAgentResult,
)
from kohakuterrarium.parsing import (
    ParserConfig,
    StreamParser,
    SubAgentCallEvent,
    extract_subagent_calls,
    is_subagent_tag,
)

# Test helper: common tools for parsing tests
TEST_KNOWN_TOOLS = {"bash", "python", "read", "write", "edit", "multi_edit", "glob", "grep", "tree"}


def parse_complete_with_tools(text: str) -> list:
    """Parse complete text with test tools configured."""
    config = ParserConfig(known_tools=TEST_KNOWN_TOOLS)
    parser = StreamParser(config)
    events = parser.feed(text)
    events.extend(parser.flush())
    return events


class TestSubAgentConfig:
    """Tests for SubAgentConfig."""

    def test_create_config(self):
        """Test creating a sub-agent config."""
        config = SubAgentConfig(
            name="test_agent",
            description="A test agent",
            tools=["glob", "grep", "read"],
        )
        assert config.name == "test_agent"
        assert config.description == "A test agent"
        assert len(config.tools) == 3
        assert config.can_modify is False
        assert config.stateless is True

    def test_config_defaults(self):
        """Test config default values."""
        config = SubAgentConfig(name="test")
        assert config.max_turns == 0  # unlimited
        assert config.timeout == 0  # no timeout
        assert config.output_to == OutputTarget.CONTROLLER
        assert config.interactive is False

    def test_config_from_dict(self):
        """Test creating config from dictionary."""
        data = {
            "name": "explore",
            "description": "Search codebase",
            "tools": ["glob", "grep"],
            "can_modify": False,
            "output_to": "controller",
        }
        config = SubAgentConfig.from_dict(data)
        assert config.name == "explore"
        assert config.tools == ["glob", "grep"]
        assert config.output_to == OutputTarget.CONTROLLER

    def test_output_target_enum(self):
        """Test OutputTarget enum values."""
        assert OutputTarget.CONTROLLER.value == "controller"
        assert OutputTarget.EXTERNAL.value == "external"

    def test_config_with_prompt(self):
        """Test config with inline prompt."""
        config = SubAgentConfig(
            name="test",
            system_prompt="You are a test agent.",
        )
        prompt = config.load_prompt()
        assert "test agent" in prompt


class TestSubAgentInfo:
    """Tests for SubAgentInfo."""

    def test_create_info(self):
        """Test creating sub-agent info."""
        info = SubAgentInfo(
            name="explore",
            description="Search codebase",
            can_modify=False,
        )
        assert info.name == "explore"
        assert not info.can_modify

    def test_to_prompt_line(self):
        """Test formatting for system prompt."""
        info = SubAgentInfo(name="explore", description="Search codebase")
        line = info.to_prompt_line()
        assert "explore" in line
        assert "Search codebase" in line

    def test_to_prompt_line_with_modify(self):
        """Test prompt line with can_modify flag."""
        info = SubAgentInfo(name="coder", description="Write code", can_modify=True)
        line = info.to_prompt_line()
        assert "[can modify files]" in line

    def test_from_config(self):
        """Test creating info from config."""
        config = SubAgentConfig(
            name="test",
            description="Test agent",
            can_modify=True,
        )
        info = SubAgentInfo.from_config(config)
        assert info.name == "test"
        assert info.description == "Test agent"
        assert info.can_modify is True


class TestSubAgentResult:
    """Tests for SubAgentResult."""

    def test_success_result(self):
        """Test successful result."""
        result = SubAgentResult(
            output="Found 5 files",
            success=True,
            turns=3,
            duration=1.5,
        )
        assert result.success
        assert result.turns == 3

    def test_failed_result(self):
        """Test failed result."""
        result = SubAgentResult(
            success=False,
            error="Timeout",
        )
        assert not result.success
        assert result.error == "Timeout"

    def test_truncated(self):
        """Test output truncation."""
        long_output = "x" * 5000
        result = SubAgentResult(output=long_output)
        truncated = result.truncated(max_chars=100)
        assert len(truncated) < len(long_output)
        assert "more chars" in truncated


class TestBuiltinSubAgents:
    """Tests for builtin sub-agent configurations."""

    def test_list_builtin_subagents(self):
        """Test listing builtin sub-agents."""
        names = list_builtin_subagents()
        assert "explore" in names
        assert "plan" in names
        assert "memory_read" in names
        assert "memory_write" in names

    def test_builtin_subagents_constant(self):
        """Test BUILTIN_SUBAGENTS constant."""
        assert isinstance(BUILTIN_SUBAGENTS, list)
        assert len(BUILTIN_SUBAGENTS) >= 4

    def test_get_explore_config(self):
        """Test getting explore sub-agent config."""
        config = get_builtin_subagent_config("explore")
        assert config is not None
        assert config.name == "explore"
        assert "glob" in config.tools
        assert "grep" in config.tools
        assert "read" in config.tools
        assert config.can_modify is False

    def test_get_plan_config(self):
        """Test getting plan sub-agent config."""
        config = get_builtin_subagent_config("plan")
        assert config is not None
        assert config.name == "plan"
        assert config.can_modify is False

    def test_get_memory_read_config(self):
        """Test getting memory_read sub-agent config."""
        config = get_builtin_subagent_config("memory_read")
        assert config is not None
        assert config.name == "memory_read"
        assert config.can_modify is False

    def test_get_memory_write_config(self):
        """Test getting memory_write sub-agent config."""
        config = get_builtin_subagent_config("memory_write")
        assert config is not None
        assert config.name == "memory_write"
        assert config.can_modify is True
        assert "write" in config.tools or "edit" in config.tools

    def test_get_nonexistent_config(self):
        """Test getting non-existent config."""
        config = get_builtin_subagent_config("nonexistent")
        assert config is None

    def test_explore_has_system_prompt(self):
        """Test that explore config has system prompt."""
        config = get_builtin_subagent_config("explore")
        prompt = config.load_prompt()
        assert len(prompt) > 100
        assert "exploration" in prompt.lower() or "search" in prompt.lower()


class TestAgentTagParsing:
    """Tests for parsing sub-agent calls via [/name]...[name/] syntax."""

    def test_is_subagent_tag(self):
        """Test is_subagent_tag function."""
        assert is_subagent_tag("agent")
        assert not is_subagent_tag("bash")
        assert not is_subagent_tag("read")

    def test_parse_agent_tag(self):
        """Test parsing sub-agent call by name."""
        text = "[/explore]Find Python files[explore/]"
        config = ParserConfig(known_subagents={"explore"})
        parser = StreamParser(config)
        events = parser.feed(text)
        events.extend(parser.flush())
        subagents = extract_subagent_calls(events)

        assert len(subagents) == 1
        assert subagents[0].name == "explore"
        assert subagents[0].args["task"] == "Find Python files"

    def test_parse_agent_tag_default_type(self):
        """Test parsing a known sub-agent call."""
        text = "[/explore]Search for something[explore/]"
        config = ParserConfig(known_subagents={"explore"})
        parser = StreamParser(config)
        events = parser.feed(text)
        events.extend(parser.flush())
        subagents = extract_subagent_calls(events)

        assert len(subagents) == 1
        assert subagents[0].name == "explore"
        assert subagents[0].args["task"] == "Search for something"

    def test_parse_multiple_agent_tags(self):
        """Test parsing multiple sub-agent calls."""
        text = """
[/explore]Find auth code[explore/]

[/plan]Plan auth implementation[plan/]
"""
        config = ParserConfig(known_subagents={"explore", "plan"})
        parser = StreamParser(config)
        events = parser.feed(text)
        events.extend(parser.flush())
        subagents = extract_subagent_calls(events)

        assert len(subagents) == 2
        assert subagents[0].name == "explore"
        assert subagents[1].name == "plan"

    def test_parse_agent_with_attributes(self):
        """Test parsing sub-agent call with attributes."""
        text = "[/memory_read]\n@@path=./memory\nUser preferences\n[memory_read/]"
        config = ParserConfig(known_subagents={"memory_read"})
        parser = StreamParser(config)
        events = parser.feed(text)
        events.extend(parser.flush())
        subagents = extract_subagent_calls(events)

        assert len(subagents) == 1
        assert subagents[0].name == "memory_read"
        assert subagents[0].args.get("path") == "./memory"

    def test_mixed_tools_and_agents(self):
        """Test parsing mixed tool and sub-agent calls."""
        text = """
[/glob]*.py[glob/]

[/explore]Find main entry point[explore/]

[/read]@@path=main.py[read/]
"""
        config = ParserConfig(
            known_tools=TEST_KNOWN_TOOLS,
            known_subagents={"explore"},
        )
        parser = StreamParser(config)
        events = parser.feed(text)
        events.extend(parser.flush())
        from kohakuterrarium.parsing import extract_tool_calls

        tools = extract_tool_calls(events)
        subagents = extract_subagent_calls(events)

        assert len(tools) == 2  # glob and read
        assert len(subagents) == 1


class TestSubAgentManager:
    """Tests for SubAgentManager (without LLM)."""

    def test_create_manager(self):
        """Test creating sub-agent manager."""
        registry = Registry()
        manager = SubAgentManager(
            parent_registry=registry,
            llm=None,  # No LLM for unit tests
        )
        assert len(manager.list_subagents()) == 0

    def test_register_subagent(self):
        """Test registering a sub-agent."""
        registry = Registry()
        manager = SubAgentManager(parent_registry=registry, llm=None)

        config = SubAgentConfig(
            name="test",
            description="Test agent",
            tools=["glob"],
        )
        manager.register(config)

        assert "test" in manager.list_subagents()
        assert manager.get_config("test") is not None

    def test_register_multiple(self):
        """Test registering multiple sub-agents."""
        registry = Registry()
        manager = SubAgentManager(parent_registry=registry, llm=None)

        for name in ["explore", "plan", "memory"]:
            config = SubAgentConfig(name=name, description=f"{name} agent")
            manager.register(config)

        assert len(manager.list_subagents()) == 3

    def test_get_subagent_info(self):
        """Test getting sub-agent info."""
        registry = Registry()
        manager = SubAgentManager(parent_registry=registry, llm=None)

        config = SubAgentConfig(
            name="explore",
            description="Search codebase",
            can_modify=False,
        )
        manager.register(config)

        info = manager.get_subagent_info("explore")
        assert info is not None
        assert info.name == "explore"
        assert not info.can_modify

    def test_get_subagents_prompt(self):
        """Test generating sub-agents prompt."""
        registry = Registry()
        manager = SubAgentManager(parent_registry=registry, llm=None)

        config = SubAgentConfig(
            name="explore",
            description="Search codebase",
        )
        manager.register(config)

        prompt = manager.get_subagents_prompt()
        assert "Available Sub-Agents" in prompt
        assert "explore" in prompt


class TestSubAgentCreation:
    """Tests for SubAgent class creation (without running)."""

    def test_create_subagent(self):
        """Test creating a sub-agent instance."""
        registry = Registry()
        config = SubAgentConfig(
            name="explore",
            tools=["glob", "grep"],
            system_prompt="You are an explore agent.",
        )

        # Can't fully test without LLM, but can test creation
        subagent = SubAgent(
            config=config,
            parent_registry=registry,
            llm=None,  # No LLM for unit test
        )

        assert subagent.config.name == "explore"
        assert not subagent.is_running

    def test_limited_registry(self):
        """Test that sub-agent has limited registry."""
        from kohakuterrarium.builtins.tools import (
            BashTool,
            GlobTool,
            GrepTool,
            ReadTool,
        )

        registry = Registry()
        registry.register_tool(BashTool())
        registry.register_tool(GlobTool())
        registry.register_tool(GrepTool())
        registry.register_tool(ReadTool())

        # Sub-agent only gets glob, grep, read (not bash)
        config = SubAgentConfig(
            name="explore",
            tools=["glob", "grep", "read"],
            can_modify=False,
        )

        subagent = SubAgent(
            config=config,
            parent_registry=registry,
            llm=None,
        )

        # Should have 3 tools (not bash since can_modify=False)
        assert "glob" in subagent.registry.list_tools()
        assert "grep" in subagent.registry.list_tools()
        assert "read" in subagent.registry.list_tools()
        # bash might be filtered due to can_modify=False
        # (depends on implementation - it's not in the tools list anyway)


class TestSubAgentCallEvent:
    """Tests for SubAgentCallEvent."""

    def test_create_event(self):
        """Test creating SubAgentCallEvent."""
        event = SubAgentCallEvent(
            name="explore",
            args={"task": "Find files"},
            raw="<agent>Find files</agent>",
        )
        assert event.name == "explore"
        assert event.args["task"] == "Find files"

    def test_event_repr(self):
        """Test event string representation."""
        event = SubAgentCallEvent(name="explore", args={"task": "test"})
        repr_str = repr(event)
        assert "explore" in repr_str
        assert "SubAgentCallEvent" in repr_str
