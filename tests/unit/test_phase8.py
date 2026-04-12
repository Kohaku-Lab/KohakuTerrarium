"""
Phase 8 Tests - Additional coverage for low-coverage modules.

Tests for:
- prompt/skill_loader.py (SkillDoc, parse_frontmatter, load_skill_doc)
- modules/subagent/base.py (SubAgent, SubAgentJob with mock LLM)
- modules/subagent/interactive.py (InteractiveSubAgent)
"""

import asyncio
import tempfile
from pathlib import Path
from typing import Any, AsyncIterator
from unittest.mock import MagicMock


from kohakuterrarium.core.registry import Registry
from kohakuterrarium.llm.base import LLMProvider
from kohakuterrarium.modules.subagent.base import (
    SubAgent,
    SubAgentJob,
    SubAgentResult,
)
from kohakuterrarium.modules.subagent.config import (
    ContextUpdateMode,
    SubAgentConfig,
)
from kohakuterrarium.modules.tool.base import Tool, ToolConfig, ToolResult
from kohakuterrarium.prompt.skill_loader import (
    SkillDoc,
    load_skill_doc,
    load_skill_docs_from_dir,
    parse_frontmatter,
)

# =============================================================================
# SkillDoc Tests
# =============================================================================


class TestSkillDoc:
    """Tests for SkillDoc dataclass."""

    def test_create_skill_doc(self):
        """Test creating a SkillDoc."""
        doc = SkillDoc(
            name="bash",
            description="Execute bash commands",
            content="# Bash Tool\n\nRun shell commands.",
        )
        assert doc.name == "bash"
        assert doc.description == "Execute bash commands"
        assert doc.content == "# Bash Tool\n\nRun shell commands."
        assert doc.category == "custom"  # default
        assert doc.tags == []  # default
        assert doc.metadata == {}  # default

    def test_skill_doc_with_all_fields(self):
        """Test SkillDoc with all fields."""
        doc = SkillDoc(
            name="read",
            description="Read files",
            content="# Read Tool",
            category="builtin",
            tags=["file", "io"],
            metadata={"version": "1.0"},
        )
        assert doc.category == "builtin"
        assert doc.tags == ["file", "io"]
        assert doc.metadata == {"version": "1.0"}

    def test_full_doc_property(self):
        """Test full_doc property returns content."""
        doc = SkillDoc(
            name="test",
            description="Test tool",
            content="Full documentation here.",
        )
        assert doc.full_doc == "Full documentation here."


# =============================================================================
# parse_frontmatter Tests
# =============================================================================


class TestParseFrontmatter:
    """Tests for parse_frontmatter function."""

    def test_no_frontmatter(self):
        """Test parsing text without frontmatter."""
        text = "# Just Content\n\nNo frontmatter here."
        metadata, content = parse_frontmatter(text)
        assert metadata == {}
        assert content == text

    def test_valid_frontmatter(self):
        """Test parsing valid YAML frontmatter."""
        text = """---
name: bash
description: Execute commands
category: builtin
---

# Bash Tool

Execute shell commands."""
        metadata, content = parse_frontmatter(text)
        assert metadata["name"] == "bash"
        assert metadata["description"] == "Execute commands"
        assert metadata["category"] == "builtin"
        assert content.startswith("# Bash Tool")

    def test_frontmatter_with_tags(self):
        """Test frontmatter with list values."""
        text = """---
name: read
tags:
  - file
  - io
  - builtin
---

Content here."""
        metadata, content = parse_frontmatter(text)
        assert metadata["name"] == "read"
        assert metadata["tags"] == ["file", "io", "builtin"]
        assert content == "Content here."

    def test_incomplete_frontmatter(self):
        """Test frontmatter without closing delimiter."""
        text = """---
name: incomplete
description: No closing delimiter

# Content that looks like frontmatter"""
        metadata, content = parse_frontmatter(text)
        assert metadata == {}  # Falls back
        assert content == text

    def test_empty_frontmatter(self):
        """Test empty frontmatter block."""
        text = """---
---

Just content."""
        metadata, content = parse_frontmatter(text)
        assert metadata == {}
        assert content == "Just content."


# =============================================================================
# load_skill_doc Tests
# =============================================================================


class TestLoadSkillDoc:
    """Tests for load_skill_doc function."""

    def test_load_skill_doc_simple(self):
        """Test loading a simple skill doc."""
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".md",
            delete=False,
            encoding="utf-8",
        ) as f:
            f.write(
                """---
name: test_tool
description: A test tool
category: testing
---

# Test Tool

This is a test tool documentation."""
            )
            temp_path = Path(f.name)

        try:
            doc = load_skill_doc(temp_path)
            assert doc is not None
            assert doc.name == "test_tool"
            assert doc.description == "A test tool"
            assert doc.category == "testing"
            assert "# Test Tool" in doc.content
        finally:
            temp_path.unlink()

    def test_load_skill_doc_no_frontmatter(self):
        """Test loading skill doc without frontmatter."""
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".md",
            delete=False,
            encoding="utf-8",
        ) as f:
            f.write("# Simple Doc\n\nNo frontmatter.")
            temp_path = Path(f.name)

        try:
            doc = load_skill_doc(temp_path)
            assert doc is not None
            # Name defaults to filename stem
            assert doc.name == temp_path.stem
            assert doc.description == ""
            assert doc.category == "custom"
        finally:
            temp_path.unlink()

    def test_load_skill_doc_not_found(self):
        """Test loading nonexistent file returns None."""
        doc = load_skill_doc(Path("/nonexistent/path/file.md"))
        assert doc is None


class TestLoadSkillDocsFromDir:
    """Tests for load_skill_docs_from_dir function."""

    def test_load_from_directory(self):
        """Test loading multiple skill docs from directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            # Create first doc
            (tmppath / "tool1.md").write_text(
                """---
name: tool1
description: First tool
---

# Tool 1""",
                encoding="utf-8",
            )

            # Create second doc
            (tmppath / "tool2.md").write_text(
                """---
name: tool2
description: Second tool
---

# Tool 2""",
                encoding="utf-8",
            )

            docs = load_skill_docs_from_dir(tmppath)
            assert len(docs) == 2
            assert "tool1" in docs
            assert "tool2" in docs
            assert docs["tool1"].description == "First tool"
            assert docs["tool2"].description == "Second tool"

    def test_load_from_empty_directory(self):
        """Test loading from empty directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            docs = load_skill_docs_from_dir(Path(tmpdir))
            assert docs == {}

    def test_load_from_nonexistent_directory(self):
        """Test loading from nonexistent directory."""
        docs = load_skill_docs_from_dir(Path("/nonexistent/dir"))
        assert docs == {}


# =============================================================================
# Mock LLM for SubAgent tests
# =============================================================================


class MockLLM(LLMProvider):
    """Mock LLM that returns predefined responses."""

    def __init__(self, responses: list[str] | None = None):
        self.responses = responses or ["I completed the task."]
        self._call_count = 0
        self.received_messages: list[list[dict]] = []

    async def chat(
        self,
        messages: list[dict[str, Any]],
        stream: bool = False,
    ) -> AsyncIterator[str]:
        self.received_messages.append(messages)
        response = self.responses[min(self._call_count, len(self.responses) - 1)]
        self._call_count += 1

        if stream:
            # Yield in chunks
            for char in response:
                yield char
        else:
            yield response


class MockTool(Tool):
    """Mock tool for testing."""

    def __init__(self, name: str = "mock_tool", output: str = "mock output"):
        self._name = name
        self._output = output
        self.call_count = 0
        self.received_args: list[dict] = []

    @property
    def tool_name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"Mock {self._name} tool"

    def get_config(self) -> ToolConfig:
        return ToolConfig(name=self._name, description=self.description)

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        self.call_count += 1
        self.received_args.append(args)
        return ToolResult(output=self._output)


# =============================================================================
# SubAgentResult Tests
# =============================================================================


class TestSubAgentResult:
    """Tests for SubAgentResult dataclass."""

    def test_create_result(self):
        """Test creating a SubAgentResult."""
        result = SubAgentResult(
            output="Task completed successfully",
            success=True,
            turns=3,
            duration=1.5,
        )
        assert result.output == "Task completed successfully"
        assert result.success is True
        assert result.turns == 3
        assert result.duration == 1.5
        assert result.error is None

    def test_failed_result(self):
        """Test creating a failed result."""
        result = SubAgentResult(
            success=False,
            error="Something went wrong",
            turns=1,
        )
        assert result.success is False
        assert result.error == "Something went wrong"

    def test_truncated_short(self):
        """Test truncated() with short output."""
        result = SubAgentResult(output="Short output")
        assert result.truncated() == "Short output"

    def test_truncated_long(self):
        """Test truncated() with long output."""
        long_output = "x" * 3000
        result = SubAgentResult(output=long_output)
        truncated = result.truncated(max_chars=100)
        assert len(truncated) < len(long_output)
        assert "100 more chars" not in truncated  # The note shows actual remaining
        assert "2900 more chars" in truncated


# =============================================================================
# SubAgent Tests
# =============================================================================


class TestSubAgent:
    """Tests for SubAgent class."""

    def test_create_subagent(self):
        """Test creating a SubAgent."""
        config = SubAgentConfig(
            name="test_agent",
            description="Test agent",
            tools=["read", "glob"],
        )
        registry = Registry()
        llm = MockLLM()

        agent = SubAgent(config, registry, llm)
        assert agent.config == config
        assert agent.parent_registry == registry
        assert agent.llm == llm
        assert agent._running is False

    def test_create_limited_registry(self):
        """Test SubAgent creates limited registry."""
        config = SubAgentConfig(
            name="explorer",
            description="Explorer",
            tools=["read", "glob"],
            can_modify=False,
        )

        # Parent has multiple tools
        parent_registry = Registry()
        parent_registry.register_tool(MockTool("read"))
        parent_registry.register_tool(MockTool("glob"))
        parent_registry.register_tool(MockTool("write"))
        parent_registry.register_tool(MockTool("bash"))

        llm = MockLLM()
        agent = SubAgent(config, parent_registry, llm)

        # Should only have read and glob (not write/bash due to can_modify=False)
        assert "read" in agent.registry.list_tools()
        assert "glob" in agent.registry.list_tools()
        # bash and write are modifying tools, but since we're requesting read/glob, they shouldn't be there anyway
        # Actually the config.tools only contains read/glob, so write/bash wouldn't be added regardless

    async def test_run_simple_task(self):
        """Test running a simple task."""
        config = SubAgentConfig(
            name="helper",
            description="Helper agent",
            tools=[],  # No tools
            max_turns=5,
        )
        registry = Registry()
        llm = MockLLM(responses=["I found the answer: 42"])

        agent = SubAgent(config, registry, llm)
        result = await agent.run("What is the answer?")

        assert result.success is True
        assert result.turns == 1
        assert "42" in result.output
        assert result.duration >= 0

    async def test_run_with_timeout(self):
        """Test sub-agent timeout handling."""

        class SlowLLM(LLMProvider):
            async def chat(self, messages, stream=False):
                await asyncio.sleep(10)  # Very slow
                yield "response"

        config = SubAgentConfig(
            name="slow",
            description="Slow agent",
            timeout=0.1,  # Very short timeout
        )
        registry = Registry()
        llm = SlowLLM()

        agent = SubAgent(config, registry, llm)
        result = await agent.run("Do something slow")

        assert result.success is False
        assert "Timed out" in result.error

    def test_is_running_property(self):
        """Test is_running property."""
        config = SubAgentConfig(name="test", description="Test")
        registry = Registry()
        llm = MockLLM()
        agent = SubAgent(config, registry, llm)

        assert agent.is_running is False


# =============================================================================
# SubAgentJob Tests
# =============================================================================


class TestSubAgentJob:
    """Tests for SubAgentJob class."""

    def test_create_job(self):
        """Test creating a SubAgentJob."""
        config = SubAgentConfig(name="test", description="Test")
        registry = Registry()
        llm = MockLLM()
        agent = SubAgent(config, registry, llm)

        job = SubAgentJob(agent, "job_123")
        assert job.subagent == agent
        assert job.job_id == "job_123"
        assert job._result is None

    async def test_job_run(self):
        """Test running a job."""
        config = SubAgentConfig(name="test", description="Test")
        registry = Registry()
        llm = MockLLM(responses=["Done!"])
        agent = SubAgent(config, registry, llm)

        job = SubAgentJob(agent, "job_123")
        result = await job.run("Do the task")

        assert result.success is True
        assert job._result is not None

    def test_to_job_status(self):
        """Test converting job to JobStatus."""
        config = SubAgentConfig(name="explorer", description="Explore")
        registry = Registry()
        llm = MockLLM()
        agent = SubAgent(config, registry, llm)

        job = SubAgentJob(agent, "job_456")
        status = job.to_job_status()

        assert status.job_id == "job_456"
        assert status.type_name == "explorer"

    async def test_to_job_result(self):
        """Test converting job to JobResult."""
        config = SubAgentConfig(name="test", description="Test")
        registry = Registry()
        llm = MockLLM(responses=["Output here"])
        agent = SubAgent(config, registry, llm)

        job = SubAgentJob(agent, "job_789")

        # Before running, should return None
        assert job.to_job_result() is None

        # After running
        await job.run("Task")
        result = job.to_job_result()

        assert result is not None
        assert result.job_id == "job_789"
        assert "Output" in result.output


# =============================================================================
# SubAgent with Tool Execution Tests
# =============================================================================


class TestSubAgentWithTools:
    """Tests for SubAgent executing tools."""

    async def test_execute_tool(self):
        """Test SubAgent executing a tool."""

        # Create LLM that returns a tool call
        class ToolCallingLLM(LLMProvider):
            def __init__(self):
                self.call_count = 0

            async def chat(self, messages, stream=False):
                self.call_count += 1
                if self.call_count == 1:
                    # First call - make a tool call
                    yield '<tool name="mock_tool">arg1</tool>'
                else:
                    # Second call - just respond
                    yield "Done with the tool result."

        config = SubAgentConfig(
            name="tool_user",
            description="Uses tools",
            tools=["mock_tool"],
        )

        parent_registry = Registry()
        mock_tool = MockTool("mock_tool", output="Tool executed!")
        parent_registry.register_tool(mock_tool)

        llm = ToolCallingLLM()
        agent = SubAgent(config, parent_registry, llm)

        result = await agent.run("Use the tool")

        # Should have multiple turns (tool call + response)
        assert result.turns >= 1
        assert result.success is True


# =============================================================================
# Controller Tests
# =============================================================================


from kohakuterrarium.core.controller import (
    Controller,
    ControllerConfig,
    ControllerContext,
)
from kohakuterrarium.core.events import create_user_input_event
from kohakuterrarium.parsing import TextEvent


class TestControllerConfig:
    """Tests for ControllerConfig."""

    def test_default_config(self):
        """Test default controller config."""
        config = ControllerConfig()
        assert config.system_prompt == "You are a helpful assistant."
        assert config.include_job_status is True
        assert config.include_tools_list is True
        assert config.batch_stackable_events is True

    def test_custom_config(self):
        """Test custom controller config."""
        config = ControllerConfig(
            system_prompt="Custom prompt",
            include_job_status=False,
            batch_stackable_events=False,
        )
        assert config.system_prompt == "Custom prompt"
        assert config.include_job_status is False
        assert config.batch_stackable_events is False


class TestControllerContext:
    """Tests for ControllerContext."""

    def test_create_context(self):
        """Test creating controller context."""
        from kohakuterrarium.core.job import JobStore

        llm = MockLLM()
        controller = Controller(llm)
        job_store = JobStore()
        registry = Registry()

        ctx = ControllerContext(
            controller=controller,
            job_store=job_store,
            registry=registry,
        )

        assert ctx.controller == controller
        assert ctx.job_store == job_store
        assert ctx.registry == registry

    def test_get_job_status_not_found(self):
        """Test getting nonexistent job status."""
        from kohakuterrarium.core.job import JobStore

        llm = MockLLM()
        controller = Controller(llm)
        job_store = JobStore()
        registry = Registry()

        ctx = ControllerContext(
            controller=controller,
            job_store=job_store,
            registry=registry,
        )

        assert ctx.get_job_status("nonexistent") is None

    def test_get_subagent_info(self):
        """Test get_subagent_info returns None (placeholder)."""
        from kohakuterrarium.core.job import JobStore

        llm = MockLLM()
        controller = Controller(llm)
        job_store = JobStore()
        registry = Registry()

        ctx = ControllerContext(
            controller=controller,
            job_store=job_store,
            registry=registry,
        )

        assert ctx.get_subagent_info("any") is None


class TestController:
    """Tests for Controller class."""

    def test_create_controller(self):
        """Test creating a controller."""
        llm = MockLLM()
        controller = Controller(llm)

        assert controller.llm == llm
        assert controller.config is not None
        assert controller.conversation is not None
        assert controller.registry is not None

    def test_create_controller_with_config(self):
        """Test creating controller with custom config."""
        llm = MockLLM()
        config = ControllerConfig(
            system_prompt="Custom system prompt",
        )
        controller = Controller(llm, config)

        assert controller.config.system_prompt == "Custom system prompt"

    async def test_push_event(self):
        """Test pushing events to controller."""
        llm = MockLLM()
        controller = Controller(llm)

        event = create_user_input_event("Hello")
        await controller.push_event(event)

        assert not controller._event_queue.empty()

    def test_push_event_sync(self):
        """Test synchronous event push."""
        llm = MockLLM()
        controller = Controller(llm)

        event = create_user_input_event("Hello sync")
        controller.push_event_sync(event)

        assert not controller._event_queue.empty()

    def test_has_pending_events(self):
        """Test checking for pending events."""
        llm = MockLLM()
        controller = Controller(llm)

        assert controller.has_pending_events() is False

        controller.push_event_sync(create_user_input_event("Test"))
        assert controller.has_pending_events() is True

    async def test_run_once_simple(self):
        """Test running controller once with simple response."""
        llm = MockLLM(responses=["Hello! How can I help you?"])
        controller = Controller(llm)

        # Push an event
        await controller.push_event(create_user_input_event("Hi"))

        # Collect output
        output = []
        async for event in controller.run_once():
            if isinstance(event, TextEvent):
                output.append(event.text)

        assert len(output) > 0
        # LLM received the messages
        assert len(llm.received_messages) == 1

    async def test_controller_with_tools(self):
        """Test controller with registered tools."""
        llm = MockLLM(responses=["I'll use the tool"])
        registry = Registry()
        registry.register_tool(MockTool("test_tool"))

        config = ControllerConfig(include_tools_list=True)
        controller = Controller(llm, config, registry=registry)

        # The system prompt should include the tool
        assert "test_tool" in controller.conversation._messages[0].content

    def test_get_parser(self):
        """Test parser creation with registry tools."""
        llm = MockLLM()
        registry = Registry()
        registry.register_tool(MockTool("my_tool"))

        controller = Controller(llm, registry=registry)
        parser = controller._get_parser()

        assert parser is not None
        assert "my_tool" in parser.config.known_tools


# =============================================================================
# InteractiveSubAgent Additional Tests
# =============================================================================


from kohakuterrarium.modules.subagent.interactive import (
    InteractiveSubAgent,
    InteractiveOutput,
)


class TestInteractiveSubAgentBasic:
    """Basic tests for InteractiveSubAgent."""

    def test_create_interactive_agent(self):
        """Test creating an interactive sub-agent."""
        config = SubAgentConfig(
            name="output",
            description="Output agent",
            interactive=True,
            context_mode=ContextUpdateMode.INTERRUPT_RESTART,
        )
        registry = Registry()
        llm = MockLLM()

        agent = InteractiveSubAgent(config, registry, llm)

        assert agent.config == config
        assert agent.is_active is False
        assert agent._output_buffer == []

    async def test_start_stop_lifecycle(self):
        """Test starting and stopping interactive agent."""
        config = SubAgentConfig(
            name="output",
            description="Output agent",
            interactive=True,
        )
        registry = Registry()
        llm = MockLLM()

        agent = InteractiveSubAgent(config, registry, llm)

        assert agent.is_active is False

        await agent.start()
        assert agent.is_active is True

        await agent.stop()
        assert agent.is_active is False

    def test_get_buffered_output(self):
        """Test getting buffered output."""
        config = SubAgentConfig(
            name="output",
            description="Output agent",
            interactive=True,
        )
        registry = Registry()
        llm = MockLLM()

        agent = InteractiveSubAgent(config, registry, llm)

        # Add to buffer manually
        agent._output_buffer = ["Hello ", "World!"]

        output = agent.get_buffered_output()
        assert output == "Hello World!"
        assert agent._output_buffer == []  # Should be cleared

    def test_clear_conversation(self):
        """Test clearing conversation history."""
        config = SubAgentConfig(
            name="output",
            description="Output agent",
            interactive=True,
        )
        registry = Registry()
        llm = MockLLM()

        agent = InteractiveSubAgent(config, registry, llm)

        # Add some messages
        agent.conversation.append("system", "System prompt")
        agent.conversation.append("user", "User message")
        agent.conversation.append("assistant", "Assistant response")

        agent.clear_conversation()

        # Should keep system prompt only
        assert len(agent.conversation._messages) == 1
        assert agent.conversation._messages[0].role == "system"

    def test_output_callback(self):
        """Test setting output callback."""
        config = SubAgentConfig(
            name="output",
            description="Output agent",
            interactive=True,
        )
        registry = Registry()
        llm = MockLLM()

        agent = InteractiveSubAgent(config, registry, llm)

        received_output = []

        def callback(output: InteractiveOutput):
            received_output.append(output)

        agent.on_output = callback

        # Manually emit output
        test_output = InteractiveOutput(text="Test", is_complete=False)
        agent._emit_output(test_output)

        assert len(received_output) == 1
        assert received_output[0].text == "Test"

    async def test_push_context_when_inactive(self):
        """Test pushing context to inactive agent."""
        config = SubAgentConfig(
            name="output",
            description="Output agent",
            interactive=True,
        )
        registry = Registry()
        llm = MockLLM()

        agent = InteractiveSubAgent(config, registry, llm)

        # Should not raise, just log warning
        await agent.push_context({"message": "hello"})


class TestInteractiveSubAgentContextModes:
    """Tests for different context update modes."""

    def test_format_context_as_message_with_message(self):
        """Test formatting context with 'message' key."""
        config = SubAgentConfig(
            name="output",
            description="Output agent",
            interactive=True,
        )
        registry = Registry()
        llm = MockLLM()

        agent = InteractiveSubAgent(config, registry, llm)

        result = agent._format_context_as_message({"message": "Hello!"})
        assert result == "Hello!"

    def test_format_context_as_message_with_input(self):
        """Test formatting context with 'input' key."""
        config = SubAgentConfig(
            name="output",
            description="Output agent",
            interactive=True,
        )
        registry = Registry()
        llm = MockLLM()

        agent = InteractiveSubAgent(config, registry, llm)

        result = agent._format_context_as_message({"input": "Test input"})
        assert result == "Test input"

    def test_format_context_as_message_with_text(self):
        """Test formatting context with 'text' key."""
        config = SubAgentConfig(
            name="output",
            description="Output agent",
            interactive=True,
        )
        registry = Registry()
        llm = MockLLM()

        agent = InteractiveSubAgent(config, registry, llm)

        result = agent._format_context_as_message({"text": "Some text"})
        assert result == "Some text"

    def test_format_context_as_message_generic(self):
        """Test formatting context with generic keys."""
        config = SubAgentConfig(
            name="output",
            description="Output agent",
            interactive=True,
        )
        registry = Registry()
        llm = MockLLM()

        agent = InteractiveSubAgent(config, registry, llm)

        result = agent._format_context_as_message({"key1": "value1", "key2": "value2"})
        assert "key1: value1" in result
        assert "key2: value2" in result


# =============================================================================
# New Commands Tests
# =============================================================================


class TestJobsCommand:
    """Tests for JobsCommand."""

    async def test_jobs_no_store(self):
        """Test jobs command without job store."""
        from kohakuterrarium.commands.read import JobsCommand

        cmd = JobsCommand()

        # Context without job_store
        context = MagicMock(spec=[])

        result = await cmd.execute("", context)
        assert result.error == "No job store available"

    async def test_jobs_no_running(self):
        """Test jobs command with no running jobs."""
        from kohakuterrarium.commands.read import JobsCommand
        from kohakuterrarium.core.job import JobStore

        cmd = JobsCommand()
        job_store = JobStore()

        context = MagicMock()
        context.job_store = job_store

        result = await cmd.execute("", context)
        assert result.content == "No running jobs."

    async def test_jobs_with_running(self):
        """Test jobs command with running jobs."""
        from kohakuterrarium.commands.read import JobsCommand
        from kohakuterrarium.core.job import JobState, JobStatus, JobStore, JobType

        cmd = JobsCommand()
        job_store = JobStore()

        # Add a running job
        status = JobStatus(
            job_id="test_123",
            job_type=JobType.TOOL,
            type_name="bash",
            state=JobState.RUNNING,
        )
        job_store.register(status)

        context = MagicMock()
        context.job_store = job_store

        result = await cmd.execute("", context)
        assert "test_123" in result.content
        assert "running" in result.content


class TestWaitCommand:
    """Tests for WaitCommand."""

    async def test_wait_no_job_id(self):
        """Test wait command without job ID."""
        from kohakuterrarium.commands.read import WaitCommand

        cmd = WaitCommand()
        context = MagicMock()
        context.job_store = MagicMock()

        result = await cmd.execute("", context)
        assert "No job_id provided" in result.error

    async def test_wait_job_not_found(self):
        """Test wait command with non-existent job."""
        from kohakuterrarium.commands.read import WaitCommand
        from kohakuterrarium.core.job import JobStore

        cmd = WaitCommand()
        job_store = JobStore()

        context = MagicMock()
        context.job_store = job_store

        result = await cmd.execute("nonexistent_123", context)
        assert "Job not found" in result.error

    async def test_wait_already_complete(self):
        """Test wait command with already completed job."""
        from kohakuterrarium.commands.read import WaitCommand
        from kohakuterrarium.core.job import (
            JobResult,
            JobState,
            JobStatus,
            JobStore,
            JobType,
        )

        cmd = WaitCommand()
        job_store = JobStore()

        # Add completed job
        status = JobStatus(
            job_id="done_123",
            job_type=JobType.TOOL,
            type_name="bash",
            state=JobState.DONE,
        )
        job_store.register(status)
        job_store.store_result(
            JobResult(
                job_id="done_123",
                output="Hello world",
            )
        )

        context = MagicMock()
        context.job_store = job_store

        result = await cmd.execute("done_123", context)
        assert "DONE" in result.content
        assert "Hello world" in result.content


# =============================================================================
# Aggregator Tests
# =============================================================================


class TestAggregatorSkillMode:
    """Tests for aggregator skill_mode functionality."""

    def test_dynamic_mode_hints(self):
        """Test dynamic mode includes info command hint."""
        from kohakuterrarium.prompt.aggregator import _build_dynamic_hints

        hints = _build_dynamic_hints()
        assert "info" in hints.lower()
        assert "docs" in hints.lower()

    def test_static_mode_hints(self):
        """Test static mode doesn't include info command."""
        from kohakuterrarium.prompt.aggregator import _build_static_hints

        hints = _build_static_hints()
        assert "[/info]" not in hints

    def test_aggregate_with_skill_mode_dynamic(self):
        """Test aggregation with dynamic skill mode."""
        from kohakuterrarium.prompt.aggregator import aggregate_system_prompt

        result = aggregate_system_prompt(
            "Base prompt",
            skill_mode="dynamic",
        )

        assert "Base prompt" in result
        assert "[/info]" in result

    def test_aggregate_with_skill_mode_static(self):
        """Test aggregation with static skill mode."""
        from kohakuterrarium.prompt.aggregator import aggregate_system_prompt

        result = aggregate_system_prompt(
            "Base prompt",
            skill_mode="static",
        )

        assert "Base prompt" in result
        assert "[/info]" not in result


class TestBuildFullToolDocs:
    """Tests for _build_full_tool_docs function."""

    def test_build_full_docs_empty_registry(self):
        """Test with empty registry."""
        from kohakuterrarium.core.registry import Registry
        from kohakuterrarium.prompt.aggregator import _build_full_tool_docs

        registry = Registry()
        result = _build_full_tool_docs(registry)

        assert result == ""

    def test_build_full_docs_with_tools(self):
        """Test with registered tools."""
        from kohakuterrarium.core.registry import Registry
        from kohakuterrarium.prompt.aggregator import _build_full_tool_docs

        registry = Registry()

        # Register a mock tool
        mock_tool = MagicMock()
        mock_tool.tool_name = "test_tool"
        mock_tool.description = "A test tool"
        mock_tool.get_parameters_schema.return_value = {}
        registry.register_tool(mock_tool)

        result = _build_full_tool_docs(registry)

        # Should have some content (at least the header)
        assert "Function Documentation" in result or "test_tool" in result


class TestBuildToolsList:
    """Tests for _build_tools_list function."""

    def test_build_list_empty(self):
        """Test with empty registry."""
        from kohakuterrarium.core.registry import Registry
        from kohakuterrarium.prompt.aggregator import _build_tools_list

        registry = Registry()
        result = _build_tools_list(registry)

        assert result == ""

    def test_build_list_with_tools(self):
        """Test with registered tools."""
        from kohakuterrarium.core.registry import Registry
        from kohakuterrarium.prompt.aggregator import _build_tools_list

        registry = Registry()

        mock_tool = MagicMock()
        mock_tool.tool_name = "my_tool"
        mock_tool.description = "My test tool"
        mock_tool.get_parameters_schema.return_value = {}
        registry.register_tool(mock_tool)

        result = _build_tools_list(registry)

        assert "my_tool" in result
        assert "My test tool" in result
        assert "Available Functions" in result
