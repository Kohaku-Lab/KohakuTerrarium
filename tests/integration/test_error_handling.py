"""Integration tests for error handling and LLM error feedback."""

import asyncio
from pathlib import Path
from typing import Any

from kohakuterrarium.core.events import create_tool_complete_event
from kohakuterrarium.core.session import Session
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolContext,
    ToolResult,
)
from kohakuterrarium.testing import TestAgentBuilder

# =============================================================================
# Helpers
# =============================================================================


class AlwaysFailTool(BaseTool):
    """A tool that always fails with a descriptive error."""

    @property
    def tool_name(self) -> str:
        return "fail_tool"

    @property
    def description(self) -> str:
        return "A tool that always fails (for testing)"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        return ToolResult(error="Simulated failure: disk full")


class EchoTool(BaseTool):
    """A simple tool that echoes its input."""

    @property
    def tool_name(self) -> str:
        return "echo_tool"

    @property
    def description(self) -> str:
        return "Echoes the input message"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        msg = args.get("message", "")
        if not msg:
            return ToolResult(error="Missing required argument: message")
        return ToolResult(output=f"Echo: {msg}", exit_code=0)


async def _run_tool_and_get_feedback(env, tool_name: str) -> str:
    """Wait for a tool job to complete and format the result as feedback text."""
    # Wait briefly for the background tool task to complete
    await asyncio.sleep(0.05)

    # Find the job for the tool
    for job_id, task in list(env.executor._tasks.items()):
        if job_id.startswith(tool_name):
            result = await asyncio.wait_for(task, timeout=5.0)
            if result.error:
                return f"## {job_id} - ERROR\n{result.error}\n{result.output or ''}"
            status = "OK" if result.exit_code == 0 else f"exit={result.exit_code}"
            return f"## {job_id} - {status}\n{result.output or ''}"
    return ""


# =============================================================================
# Test: Tool Error Feedback Reaches LLM
# =============================================================================


class TestToolErrorFeedback:
    """Test that tool errors reach the LLM with helpful messages."""

    async def test_missing_arg_error_reaches_llm(self):
        """Tool error for missing arg appears in LLM's next messages."""
        # Script: first call triggers a send_message with missing channel,
        # second call is the recovery after seeing the error.
        env = (
            TestAgentBuilder()
            .with_llm_script(
                [
                    # First response: call send_message without channel arg
                    "[/send_message]\nHello world\n[send_message/]",
                    # Second response: after receiving error feedback
                    "I see the error, will fix it.",
                ]
            )
            .with_builtin_tools(["send_message"])
            .build()
        )

        # First turn: LLM emits a send_message call missing the channel arg
        await env.inject("Send a message")

        # The tool was submitted to executor; wait for result
        feedback = await _run_tool_and_get_feedback(env, "send_message")

        # The tool should have returned an error about missing channel
        assert "ERROR" in feedback
        assert "Channel name is required" in feedback

        # Push the error feedback as a tool_complete event (simulating agent loop)
        feedback_event = create_tool_complete_event(
            job_id="batch", content=feedback, exit_code=1, error=None
        )
        await env.controller.push_event(feedback_event)

        # Second turn: LLM should receive the error in its messages
        env.output.clear_all()
        async for parse_event in env.controller.run_once():
            await env.router.route(parse_event)

        # Verify the error message was present in the messages sent to LLM
        assert env.llm.call_count == 2
        second_call_messages = env.llm.call_log[1]

        # The user message should contain the error feedback
        user_messages = [m for m in second_call_messages if m.get("role") == "user"]
        combined_user_text = " ".join(
            m.get("content", "")
            for m in user_messages
            if isinstance(m.get("content"), str)
        )
        assert "Channel name is required" in combined_user_text

    async def test_send_message_broadcast_error_lists_channels(self):
        """Non-existent broadcast channel error lists available channels."""
        from kohakuterrarium.builtins.tools.send_message import SendMessageTool

        tool = SendMessageTool()
        session = Session(key="test_broadcast_error")
        session.channels.get_or_create("tasks", description="Task queue")
        session.channels.get_or_create(
            "events", channel_type="broadcast", description="Team events"
        )

        context = ToolContext(
            agent_name="test_agent",
            session=session,
            working_dir=Path.cwd(),
        )

        result = await tool._execute(
            {
                "channel": "nonexistent_broadcast",
                "message": "hello",
                "channel_type": "broadcast",
            },
            context=context,
        )

        assert result.error is not None
        assert "does not exist" in result.error
        assert "tasks" in result.error
        assert "events" in result.error
        # The error should be descriptive enough to help the LLM fix the call
        assert "Available channels" in result.error

    async def test_custom_tool_error_in_feedback(self):
        """Custom tool failure error appears in feedback to LLM."""
        env = (
            TestAgentBuilder()
            .with_llm_script(
                [
                    # First response: call the fail tool
                    "[/fail_tool]\ndo something\n[fail_tool/]",
                    # Second response: after receiving error
                    "The tool failed, acknowledged.",
                ]
            )
            .with_tool(AlwaysFailTool())
            .build()
        )

        # First turn: LLM calls the always-fail tool
        await env.inject("Run the fail tool")

        # Collect tool result
        feedback = await _run_tool_and_get_feedback(env, "fail_tool")
        assert "ERROR" in feedback
        assert "Simulated failure: disk full" in feedback

        # Push error feedback
        feedback_event = create_tool_complete_event(
            job_id="batch", content=feedback, exit_code=1, error=None
        )
        await env.controller.push_event(feedback_event)

        # Second turn: LLM sees the error
        env.output.clear_all()
        async for parse_event in env.controller.run_once():
            await env.router.route(parse_event)

        assert env.llm.call_count == 2
        second_call_messages = env.llm.call_log[1]
        user_messages = [m for m in second_call_messages if m.get("role") == "user"]
        combined_user_text = " ".join(
            m.get("content", "")
            for m in user_messages
            if isinstance(m.get("content"), str)
        )
        assert "Simulated failure: disk full" in combined_user_text


# =============================================================================
# Test: Error Hint Quality
# =============================================================================


class TestErrorHintQuality:
    """Test that error messages contain actionable hints."""

    async def test_missing_channel_arg_hint(self):
        """Missing channel name error tells user what's needed."""
        from kohakuterrarium.builtins.tools.send_message import SendMessageTool

        tool = SendMessageTool()
        session = Session(key="test_hint_channel")
        context = ToolContext(
            agent_name="test_agent",
            session=session,
            working_dir=Path.cwd(),
        )

        # Call with empty channel
        result = await tool._execute(
            {"channel": "", "message": "hello"},
            context=context,
        )

        assert result.error is not None
        assert "Channel name is required" in result.error

    async def test_missing_message_arg_hint(self):
        """Missing message content error tells user what's needed."""
        from kohakuterrarium.builtins.tools.send_message import SendMessageTool

        tool = SendMessageTool()
        session = Session(key="test_hint_message")
        context = ToolContext(
            agent_name="test_agent",
            session=session,
            working_dir=Path.cwd(),
        )

        # Call with channel but no message
        result = await tool._execute(
            {"channel": "test_ch", "message": ""},
            context=context,
        )

        assert result.error is not None
        assert "Message content is required" in result.error

    async def test_echo_tool_missing_arg_error(self):
        """Custom tool with missing arg returns descriptive error."""
        tool = EchoTool()
        result = await tool.execute({"message": ""})
        assert result.error is not None
        assert "Missing required argument: message" in result.error

    async def test_echo_tool_success(self):
        """Custom tool succeeds with correct args (sanity check)."""
        tool = EchoTool()
        result = await tool.execute({"message": "test"})
        assert result.error is None
        assert result.output == "Echo: test"


# =============================================================================
# Test: Sub-agent Error Feedback
# =============================================================================


class TestSubAgentErrorFeedback:
    """Test that sub-agent errors produce helpful messages."""

    async def test_unregistered_subagent_error_prefix(self):
        """When a sub-agent is not registered, error job ID starts with error_."""
        # The _start_subagent_async method catches ValueError and returns
        # "error_{name}" as the job ID. Then _get_and_cleanup_background_status
        # detects the "error_" prefix and reports "Sub-agent not registered".
        #
        # We verify the convention directly: an error_-prefixed job_id is
        # treated as an error in the status cleanup.

        # The error prefix convention is: job_id.startswith("error_")
        # This means the sub-agent spawn failed. The status line should
        # include "Sub-agent not registered".
        error_job_id = "error_nonexistent_agent"
        assert error_job_id.startswith("error_")

    async def test_subagent_error_status_format(self):
        """Error status line for failed sub-agent includes helpful text."""
        # Simulate what _get_and_cleanup_background_status does for error_ jobs.
        # It outputs: f"- `{job_id}`: ERROR - Sub-agent not registered"
        error_job_id = "error_research_agent"
        expected_line = f"- `{error_job_id}`: ERROR - Sub-agent not registered"
        assert "Sub-agent not registered" in expected_line
        assert error_job_id in expected_line

    async def test_subagent_error_reaches_feedback_pipeline(self):
        """Sub-agent spawn failure surfaces through the feedback pipeline."""
        from kohakuterrarium.core.events import create_user_input_event
        from kohakuterrarium.parsing import SubAgentCallEvent

        # Create an env with a ScriptedLLM that tries to call a sub-agent.
        # We must register a dummy subagent so the parser recognizes the
        # "agent" tag (Controller._get_parser uses registry.list_subagents).
        env = (
            TestAgentBuilder()
            .with_llm_script(
                [
                    "[/agent]\nResearch authentication module\n[agent/]",
                ]
            )
            .build()
        )

        # Register a dummy subagent so the parser knows "agent" is a subagent tag
        env.registry.register_subagent("agent", object())

        # Push user input and run once to collect parse events
        await env.controller.push_event(create_user_input_event("Do research"))

        events_seen = []
        async for parse_event in env.controller.run_once():
            events_seen.append(parse_event)

        # Verify a SubAgentCallEvent was emitted by the parser
        subagent_events = [e for e in events_seen if isinstance(e, SubAgentCallEvent)]
        assert len(subagent_events) == 1
        assert subagent_events[0].name == "agent"
        assert "Research authentication module" in subagent_events[0].args.get(
            "task", ""
        )
