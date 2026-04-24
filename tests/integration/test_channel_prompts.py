"""Integration tests for channel prompt awareness.

Tests that the prompt aggregator correctly generates channel communication
hints and that tools provide helpful errors for non-existent channels.
"""

from pathlib import Path
from unittest.mock import MagicMock

from kohakuterrarium.core.channel import AgentChannel, ChannelRegistry, SubAgentChannel
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.prompt.aggregator import (
    _build_channel_hints,
    aggregate_system_prompt,
)

# =============================================================================
# Channel Description Tests
# =============================================================================


class TestChannelDescriptions:
    """Tests for channel description field."""

    def test_subagent_channel_description(self):
        """SubAgentChannel stores description."""
        ch = SubAgentChannel("tasks", description="Task dispatch queue")
        assert ch.description == "Task dispatch queue"

    def test_agent_channel_description(self):
        """AgentChannel stores description."""
        ch = AgentChannel("discussion", description="Team group chat")
        assert ch.description == "Team group chat"

    def test_registry_passes_description(self):
        """Registry passes description to created channels."""
        reg = ChannelRegistry()
        ch = reg.get_or_create(
            "results",
            channel_type="queue",
            description="Completed task results",
        )
        assert ch.description == "Completed task results"

    def test_registry_get_channel_info(self):
        """Registry returns channel info for prompt injection."""
        reg = ChannelRegistry()
        reg.get_or_create("tasks", description="Task queue")
        reg.get_or_create("events", channel_type="broadcast", description="Team events")

        info = reg.get_channel_info()
        assert len(info) == 2

        tasks_info = next(c for c in info if c["name"] == "tasks")
        assert tasks_info["type"] == "queue"
        assert tasks_info["description"] == "Task queue"

        events_info = next(c for c in info if c["name"] == "events")
        assert events_info["type"] == "broadcast"
        assert events_info["description"] == "Team events"


# =============================================================================
# Channel Prompt Hints Tests
# =============================================================================


class TestChannelPromptHints:
    """Tests for _build_channel_hints in aggregator.

    The channel hints are for STANDALONE agents only (internal sub-agent
    channels). When a ``channels`` list is provided in extra_context,
    it means the terrarium topology prompt already covers channel docs,
    so ``_build_channel_hints`` returns empty.
    """

    def _registry_with_channel_tools(self) -> Registry:
        """Create registry with send_message tool."""
        registry = Registry()
        mock_send = MagicMock()
        mock_send.tool_name = "send_message"
        mock_send.description = "Send a message to a channel"
        mock_send.get_parameters_schema.return_value = {}
        registry.register_tool(mock_send)
        return registry

    def test_no_hints_without_channel_tools(self):
        """No channel section when channel tools not registered."""
        registry = Registry()
        result = _build_channel_hints(registry, None)
        assert result == ""

    def test_hints_with_channel_tools(self):
        """Channel section generated when channel tools registered (no channels context)."""
        registry = self._registry_with_channel_tools()
        result = _build_channel_hints(registry, None)

        assert "Internal Channels" in result
        assert "send_message" in result

    def test_hints_empty_when_channels_provided(self):
        """When channels are in extra_context, hints are empty (topology prompt handles it)."""
        registry = self._registry_with_channel_tools()
        channels = [
            {"name": "tasks", "type": "queue", "description": "Task dispatch"},
        ]
        result = _build_channel_hints(registry, {"channels": channels})
        assert result == ""

    def test_aggregate_includes_channel_hints_standalone(self):
        """aggregate_system_prompt includes channel section for standalone agents."""
        registry = self._registry_with_channel_tools()

        # No channels context = standalone agent, should get internal channel hints
        result = aggregate_system_prompt(
            "You are a test agent.",
            registry,
        )

        assert "Internal Channels" in result
        assert "send_message" in result


# =============================================================================
# Error Hint Tests (send_message to non-existent broadcast)
# =============================================================================


class TestChannelErrorHints:
    """Tests for helpful error messages on non-existent channels."""

    async def test_send_to_nonexistent_broadcast_shows_available(self):
        """Sending to non-existent broadcast channel returns error with listing."""
        from kohakuterrarium.builtins.tools.send_message import SendMessageTool
        from kohakuterrarium.core.session import Session
        from kohakuterrarium.modules.tool.base import ToolContext

        tool = SendMessageTool()
        session = Session(key="test_error")
        session.channels.get_or_create(
            "tasks",
            description="Task queue",
        )
        session.channels.get_or_create(
            "events",
            channel_type="broadcast",
            description="Team events",
        )

        context = ToolContext(
            agent_name="test_agent",
            session=session,
            working_dir=Path.cwd(),
        )

        result = await tool._execute(
            {
                "channel": "nonexistent",
                "message": "hello",
                "channel_type": "broadcast",
            },
            context=context,
        )

        assert result.error is not None
        assert "does not exist" in result.error
        assert "tasks" in result.error
        assert "events" in result.error

    async def test_send_to_queue_creates_on_fly(self):
        """Sending to non-existent queue channel creates it (no error)."""
        from kohakuterrarium.builtins.tools.send_message import SendMessageTool
        from kohakuterrarium.core.session import Session
        from kohakuterrarium.modules.tool.base import ToolContext

        tool = SendMessageTool()
        session = Session(key="test_queue_create")

        context = ToolContext(
            agent_name="test_agent",
            session=session,
            working_dir=Path.cwd(),
        )

        result = await tool._execute(
            {"channel": "new_channel", "message": "hello"},
            context=context,
        )

        # Queue channels auto-create, no error
        assert result.error is None
        assert "Delivered to" in result.output

        # Channel was created
        ch = session.channels.get("new_channel")
        assert ch is not None
        assert ch.channel_type == "queue"
