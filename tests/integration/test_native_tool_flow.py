"""Integration tests for native tool calling conversation flow.

Tests that the conversation format is correct when using native tool calling:
- Assistant message includes tool_calls metadata
- Tool results are role="tool" messages with tool_call_id
- Model receives results and can proceed
- run_in_background flag controls direct vs background
"""

from kohakuterrarium.core.controller import Controller, ControllerConfig
from kohakuterrarium.core.executor import Executor
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.llm.message import Message
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from kohakuterrarium.testing import ScriptedLLM


class SimpleTool(BaseTool):
    """Test tool that returns a fixed output."""

    @property
    def tool_name(self) -> str:
        return "test_tool"

    @property
    def description(self) -> str:
        return "A test tool"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args, context=None):
        return ToolResult(output=f"result: {args.get('input', 'none')}", exit_code=0)


class TestNativeToolConversation:
    """Test that native mode produces correct conversation format."""

    def _make_controller_with_tools(self, llm):
        """Create a controller with native mode and a test tool."""
        registry = Registry()
        registry.register_tool(SimpleTool())
        executor = Executor()
        executor.register_tool(SimpleTool())

        config = ControllerConfig(
            system_prompt="You are a test agent.",
            tool_format="native",
        )
        return Controller(llm, config, executor=executor, registry=registry)

    async def test_conversation_append_with_tool_calls(self):
        """Conversation correctly stores and serializes tool_calls on assistant."""
        from kohakuterrarium.core.conversation import Conversation, ConversationConfig

        conv = Conversation(ConversationConfig())
        conv.append("system", "You are helpful.")
        conv.append("user", "Run a command.")
        conv.append(
            "assistant",
            "Running command.",
            tool_calls=[
                {
                    "id": "call_abc123",
                    "type": "function",
                    "function": {"name": "bash", "arguments": '{"command":"ls"}'},
                }
            ],
        )
        conv.append(
            "tool", "file1.py\nfile2.py", tool_call_id="call_abc123", name="bash"
        )
        conv.append("assistant", "Found 2 files.")

        messages = conv.to_messages()
        assert messages[2]["tool_calls"][0]["id"] == "call_abc123"
        assert messages[3]["role"] == "tool"
        assert messages[3]["tool_call_id"] == "call_abc123"
        assert messages[4]["content"] == "Found 2 files."

    async def test_native_mode_controller_config(self):
        """Controller in native mode has correct config."""
        llm = ScriptedLLM(["Hello"])
        controller = self._make_controller_with_tools(llm)
        assert controller.config.tool_format == "native"
        assert controller._is_native_mode


class TestMessageFormat:
    """Test Message class supports tool_calls field."""

    def test_message_with_tool_calls(self):
        """Message can store tool_calls for assistant messages."""
        msg = Message(
            role="assistant",
            content="I'll check that.",
            tool_calls=[
                {
                    "id": "call_abc",
                    "type": "function",
                    "function": {"name": "bash", "arguments": '{"command":"ls"}'},
                }
            ],
        )
        d = msg.to_dict()
        assert d["role"] == "assistant"
        assert "tool_calls" in d
        assert d["tool_calls"][0]["id"] == "call_abc"

    def test_message_with_none_content(self):
        """Assistant message with tool_calls can have content=None."""
        msg = Message(
            role="assistant",
            content=None,
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "bash", "arguments": "{}"},
                }
            ],
        )
        d = msg.to_dict()
        assert d["content"] is None
        assert "tool_calls" in d

    def test_tool_message_format(self):
        """Tool result message has correct format."""
        msg = Message(
            role="tool",
            content="file listing...",
            tool_call_id="call_abc",
            name="bash",
        )
        d = msg.to_dict()
        assert d["role"] == "tool"
        assert d["tool_call_id"] == "call_abc"
        assert d["content"] == "file listing..."

    def test_message_without_tool_calls(self):
        """Normal message doesn't include tool_calls key."""
        msg = Message(role="assistant", content="Hello!")
        d = msg.to_dict()
        assert "tool_calls" not in d


class TestRunInBackground:
    """Test the run_in_background parameter on tool schemas."""

    def test_tool_schemas_have_run_in_background(self):
        """All tool schemas include run_in_background parameter."""
        from kohakuterrarium.llm.tools import build_tool_schemas

        registry = Registry()
        registry.register_tool(SimpleTool())
        schemas = build_tool_schemas(registry)

        assert len(schemas) >= 1
        tool_schema = schemas[0]
        props = tool_schema.parameters.get("properties", {})
        assert "run_in_background" in props
        assert props["run_in_background"]["type"] == "boolean"

    def test_all_builtin_tools_are_direct(self):
        """All builtin tools default to DIRECT execution mode."""
        from kohakuterrarium.builtins.tools import get_builtin_tool

        for name in ["bash", "python", "read", "write", "edit", "multi_edit", "glob", "grep"]:
            tool = get_builtin_tool(name)
            if tool and isinstance(tool, BaseTool):
                assert (
                    tool.execution_mode == ExecutionMode.DIRECT
                ), f"{name} should be DIRECT, got {tool.execution_mode}"


class TestNativePromptHints:
    """Test that native mode hints mention run_in_background."""

    def test_native_hints_mention_background(self):
        """Native hints explain run_in_background."""
        from kohakuterrarium.prompt.aggregator import _build_native_hints

        hints = _build_native_hints()
        assert "run_in_background" in hints
        assert "immediately" in hints.lower()
