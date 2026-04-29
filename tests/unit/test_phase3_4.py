"""
Phase 3/4 Unit Tests - Controller and Tool Execution

Tests for:
- Job status tracking
- Tool execution
- Executor
- Registry
- Commands
- Controller (basic, without LLM)
"""

import shlex
import sys

import pytest

_SKIP_WINDOWS = pytest.mark.skipif(
    sys.platform == "win32", reason="bash tool tests require Unix shell"
)

from kohakuterrarium.builtins.tools import BashTool, PythonTool
from kohakuterrarium.commands import CommandResult, parse_command_args
from kohakuterrarium.core.executor import Executor
from kohakuterrarium.core.job import (
    JobResult,
    JobState,
    JobStatus,
    JobStore,
    JobType,
    generate_job_id,
)
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.llm.message import ImagePart, TextPart
from kohakuterrarium.modules.tool import ExecutionMode, ToolConfig, ToolResult
from kohakuterrarium.modules.tool.base import BaseTool


class TestJobStatus:
    """Tests for JobStatus."""

    def test_create_job_status(self):
        """Test creating a job status."""
        status = JobStatus(
            job_id="test_123",
            job_type=JobType.TOOL,
            type_name="bash",
        )
        assert status.job_id == "test_123"
        assert status.job_type == JobType.TOOL
        assert status.state == JobState.PENDING
        assert not status.is_complete
        assert not status.is_running

    def test_job_running_state(self):
        """Test running state."""
        status = JobStatus(
            job_id="test_123",
            job_type=JobType.TOOL,
            type_name="bash",
            state=JobState.RUNNING,
        )
        assert status.is_running
        assert not status.is_complete

    def test_job_complete_state(self):
        """Test complete states."""
        for state in [JobState.DONE, JobState.ERROR, JobState.CANCELLED]:
            status = JobStatus(
                job_id="test_123",
                job_type=JobType.TOOL,
                type_name="bash",
                state=state,
            )
            assert status.is_complete
            assert not status.is_running

    def test_job_duration(self):
        """Test duration calculation."""
        status = JobStatus(
            job_id="test_123",
            job_type=JobType.TOOL,
            type_name="bash",
            state=JobState.RUNNING,
        )
        # Duration should be non-negative
        assert status.duration >= 0

    def test_to_context_string(self):
        """Test context string formatting."""
        status = JobStatus(
            job_id="test_123",
            job_type=JobType.TOOL,
            type_name="bash",
            state=JobState.DONE,
            output_lines=5,
            output_bytes=100,
            preview="hello world",
        )
        context = status.to_context_string()
        assert "test_123" in context
        assert "bash" in context
        assert "done" in context


class TestJobResult:
    """Tests for JobResult."""

    def test_create_job_result(self):
        """Test creating a job result."""
        result = JobResult(
            job_id="test_123",
            output="hello world",
            exit_code=0,
        )
        assert result.job_id == "test_123"
        assert result.output == "hello world"
        assert result.success

    def test_failed_result(self):
        """Test failed result."""
        result = JobResult(
            job_id="test_123",
            error="Command failed",
            exit_code=1,
        )
        assert not result.success

    def test_get_lines(self):
        """Test get_lines method."""
        result = JobResult(
            job_id="test_123",
            output="line1\nline2\nline3\nline4",
        )
        lines = result.get_lines()
        assert len(lines) == 4

        lines = result.get_lines(start=1, count=2)
        assert len(lines) == 2
        assert lines[0] == "line2"

    def test_truncated(self):
        """Test truncated output."""
        long_output = "x" * 2000
        result = JobResult(job_id="test_123", output=long_output)
        truncated = result.truncated(max_chars=100)
        assert len(truncated) < len(long_output)
        assert "more chars" in truncated

    def test_multimodal_text_output(self):
        result = JobResult(
            job_id="test_123",
            output=[TextPart(text="hello"), ImagePart(url="https://x/img.png")],
        )
        text = result.get_text_output()
        assert "hello" in text
        assert "https://x/img.png" in text


class TestJobStore:
    """Tests for JobStore."""

    def test_register_and_get(self):
        """Test registering and getting a job."""
        store = JobStore()
        status = JobStatus(
            job_id="test_123",
            job_type=JobType.TOOL,
            type_name="bash",
        )
        store.register(status)

        retrieved = store.get_status("test_123")
        assert retrieved is not None
        assert retrieved.job_id == "test_123"

    def test_update_status(self):
        """Test updating job status."""
        store = JobStore()
        status = JobStatus(
            job_id="test_123",
            job_type=JobType.TOOL,
            type_name="bash",
            state=JobState.RUNNING,
        )
        store.register(status)

        updated = store.update_status(
            "test_123",
            state=JobState.DONE,
            output_lines=10,
        )
        assert updated is not None
        assert updated.state == JobState.DONE
        assert updated.output_lines == 10

    def test_get_running_jobs(self):
        """Test getting running jobs."""
        store = JobStore()

        # Add some jobs
        for i, state in enumerate([JobState.RUNNING, JobState.DONE, JobState.RUNNING]):
            status = JobStatus(
                job_id=f"test_{i}",
                job_type=JobType.TOOL,
                type_name="bash",
                state=state,
            )
            store.register(status)

        running = store.get_running_jobs()
        assert len(running) == 2

    def test_generate_job_id(self):
        """Test job ID generation."""
        id1 = generate_job_id("bash")
        id2 = generate_job_id("bash")
        assert id1 != id2
        assert id1.startswith("bash_")


class TestToolBase:
    """Tests for tool base classes."""

    def test_tool_result_success(self):
        """Test ToolResult success property."""
        result = ToolResult(output="hello", exit_code=0)
        assert result.success

        result2 = ToolResult(output="", exit_code=1)
        assert not result2.success

        result3 = ToolResult(error="failed")
        assert not result3.success

    def test_tool_config_defaults(self):
        """Test ToolConfig defaults."""
        config = ToolConfig()
        assert config.timeout == 60.0
        assert config.max_output == 64 * 1024
        assert config.working_dir is None

    def test_execution_mode_values(self):
        """Test ExecutionMode enum."""
        assert ExecutionMode.DIRECT.value == "direct"
        assert ExecutionMode.BACKGROUND.value == "background"
        assert ExecutionMode.STATEFUL.value == "stateful"


class TestBashTool:
    """Tests for BashTool."""

    @_SKIP_WINDOWS
    @pytest.mark.asyncio
    async def test_bash_tool_echo(self):
        """Test basic echo command."""
        tool = BashTool()
        result = await tool.execute({"command": "echo hello"})
        assert result.success
        assert "hello" in result.output.lower()

    @pytest.mark.asyncio
    async def test_bash_tool_no_command(self):
        """Test with no command provided."""
        tool = BashTool()
        result = await tool.execute({})
        assert not result.success
        assert result.error is not None

    def test_bash_tool_properties(self):
        """Test tool properties."""
        tool = BashTool()
        assert tool.tool_name == "bash"
        assert len(tool.description) > 0
        assert tool.execution_mode == ExecutionMode.DIRECT

    def test_bash_tool_schema_includes_timeout(self):
        """Test per-call timeout appears in the shell schema."""
        tool = BashTool()
        schema = tool.get_parameters_schema()
        assert schema["properties"]["timeout"]["type"] == "number"

    @_SKIP_WINDOWS
    @pytest.mark.asyncio
    async def test_bash_tool_per_call_timeout(self):
        """Test bash accepts a per-call timeout override."""
        tool = BashTool(ToolConfig(timeout=60))
        command = f"{shlex.quote(sys.executable)} -c 'import time; time.sleep(1)'"
        result = await tool.execute({"command": command, "timeout": 0.01})
        assert not result.success
        assert result.error == "Command timed out after 0.01s"
        assert result.metadata["timeout"] == 0.01


class TestPythonTool:
    """Tests for PythonTool."""

    @pytest.mark.asyncio
    async def test_python_tool_per_call_timeout(self):
        """Test python accepts a per-call timeout override."""
        tool = PythonTool(ToolConfig(timeout=60))
        result = await tool.execute(
            {"code": "import time; time.sleep(1)", "timeout": 0.01}
        )
        assert not result.success
        assert result.error == "Python execution timed out after 0.01s"
        assert result.metadata["timeout"] == 0.01

    @pytest.mark.asyncio
    async def test_python_tool_rejects_invalid_timeout(self):
        """Test python reports invalid timeout values."""
        tool = PythonTool()
        result = await tool.execute({"code": "print('x')", "timeout": "bad"})
        assert not result.success
        assert result.error == "timeout must be numeric, got 'bad'"


class TestRegistry:
    """Tests for Registry."""

    def test_register_tool(self):
        """Test registering a tool."""
        registry = Registry()
        tool = BashTool()
        registry.register_tool(tool)

        retrieved = registry.get_tool("bash")
        assert retrieved is not None
        assert retrieved.tool_name == "bash"

    def test_list_tools(self):
        """Test listing tools."""
        registry = Registry()
        registry.register_tool(BashTool())

        tools = registry.list_tools()
        assert "bash" in tools

    def test_get_tools_prompt(self):
        """Test getting tools prompt."""
        registry = Registry()
        registry.register_tool(BashTool())

        prompt = registry.get_tools_prompt()
        assert "Available Tools" in prompt
        assert "bash" in prompt

    def test_register_command(self):
        """Test registering a command."""
        registry = Registry()

        async def my_command(args):
            return "result"

        registry.register_command("test", my_command)
        retrieved = registry.get_command("test")
        assert retrieved is not None

    def test_clear(self):
        """Test clearing registry."""
        registry = Registry()
        registry.register_tool(BashTool())
        registry.clear()
        assert len(registry.list_tools()) == 0


class TestCommands:
    """Tests for command utilities."""

    def test_parse_command_args_simple(self):
        """Test parsing simple args."""
        positional, kwargs = parse_command_args("job_123")
        assert positional == "job_123"
        assert len(kwargs) == 0

    def test_parse_command_args_with_flags(self):
        """Test parsing args with flags."""
        positional, kwargs = parse_command_args("job_123 --lines 50")
        assert positional == "job_123"
        assert kwargs["lines"] == "50"

    def test_parse_command_args_multiple_flags(self):
        """Test parsing multiple flags."""
        positional, kwargs = parse_command_args("job_123 --lines 50 --offset 10")
        assert positional == "job_123"
        assert kwargs["lines"] == "50"
        assert kwargs["offset"] == "10"

    def test_parse_command_args_empty(self):
        """Test parsing empty args."""
        positional, kwargs = parse_command_args("")
        assert positional == ""
        assert len(kwargs) == 0

    def test_command_result_success(self):
        """Test CommandResult success."""
        result = CommandResult(content="hello")
        assert result.success

        result2 = CommandResult(error="failed")
        assert not result2.success


class TestExecutorBasic:
    """Basic executor tests (without async execution)."""

    def test_register_tool(self):
        """Test registering tools with executor."""
        executor = Executor()
        executor.register_tool(BashTool())

        assert executor.get_tool("bash") is not None
        assert "bash" in executor.list_tools()

    def test_get_nonexistent_tool(self):
        """Test getting non-existent tool."""
        executor = Executor()
        assert executor.get_tool("nonexistent") is None


class LongOutputTool(BaseTool):
    @property
    def tool_name(self) -> str:
        return "long_output"

    @property
    def description(self) -> str:
        return "Long output test tool"

    async def _execute(self, args, **kwargs):
        return ToolResult(output="abcdef")


class TestExecutorAsync:
    """Async executor tests."""

    @_SKIP_WINDOWS
    @pytest.mark.asyncio
    async def test_submit_and_wait(self):
        """Test submitting and waiting for a job."""
        executor = Executor()
        executor.register_tool(BashTool())

        job_id = await executor.submit("bash", {"command": "echo test"})
        assert job_id is not None

        result = await executor.wait_for(job_id, timeout=10.0)
        assert result is not None
        assert result.success
        assert "test" in result.output.lower()

    @_SKIP_WINDOWS
    @pytest.mark.asyncio
    async def test_get_status_while_running(self):
        """Test getting status while job runs."""
        executor = Executor()
        executor.register_tool(BashTool())

        job_id = await executor.submit("bash", {"command": "echo hello"})
        status = executor.get_status(job_id)
        assert status is not None

        # Wait for completion
        await executor.wait_for(job_id)
        status = executor.get_status(job_id)
        assert status is not None
        assert status.is_complete

    @pytest.mark.asyncio
    async def test_submit_invalid_tool(self):
        """Test submitting with invalid tool."""
        executor = Executor()

        with pytest.raises(ValueError, match="not registered"):
            await executor.submit("nonexistent", {})

    @pytest.mark.asyncio
    async def test_executor_applies_max_output_centrally(self):
        tool = LongOutputTool(ToolConfig(max_output=3))
        executor = Executor()
        executor.register_tool(tool)

        job_id = await executor.submit("long_output", {})
        result = await executor.wait_for(job_id)

        assert result is not None
        assert isinstance(result.output, str)
        assert result.output.startswith("abc")
        assert "truncated" in result.output
        status = executor.get_status(job_id)
        assert status is not None
        assert status.output_bytes > 0
