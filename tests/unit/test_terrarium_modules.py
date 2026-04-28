"""Tests for extracted terrarium modules and API events module.

Covers:
- terrarium/tool_registration.py: idempotent tool registration
- terrarium/persistence.py: Conversation rebuild from message dicts
- terrarium/factory.py: prompt injection, root awareness prompt
- kohakuterrarium/api/events.py: event log management, detail parsing, StreamOutput
"""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kohakuterrarium.core.conversation import Conversation
from kohakuterrarium.terrarium.config import (
    ChannelConfig,
    CreatureConfig,
    TerrariumConfig,
)
from kohakuterrarium.terrarium.factory import (
    build_root_awareness_prompt,
    inject_prompt_section,
)
from kohakuterrarium.terrarium.persistence import build_conversation_from_messages

# ---------------------------------------------------------------------------
# terrarium/tool_registration.py
# ---------------------------------------------------------------------------


class TestToolRegistration:
    """Tests for ensure_terrarium_tools_registered.

    Registration is now declarative: importing
    ``kohakuterrarium.terrarium.tool_registry`` pulls in
    ``tools_creature`` / ``tools_lifecycle`` / ``tools_messaging`` at
    module load time, so the function is a defensive no-op kept for
    back-compat callers.
    """

    def test_ensure_terrarium_tools_registered_returns_none(self):
        """The function exists and returns None without raising."""
        import kohakuterrarium.terrarium.tool_registration as reg

        assert reg.ensure_terrarium_tools_registered() is None

    def test_ensure_terrarium_tools_registered_idempotent(self):
        """Calling repeatedly is safe and still returns None."""
        import kohakuterrarium.terrarium.tool_registration as reg

        assert reg.ensure_terrarium_tools_registered() is None
        assert reg.ensure_terrarium_tools_registered() is None

    def test_terrarium_tool_classes_available_at_import(self):
        """Importing the registry exposes the expected tool classes."""
        from kohakuterrarium.terrarium import tool_registry

        for name in (
            "CreatureInterruptTool",
            "CreatureStartTool",
            "CreatureStopTool",
            "TerrariumCreateTool",
            "TerrariumHistoryTool",
            "TerrariumObserveTool",
            "TerrariumSendTool",
            "TerrariumStatusTool",
            "TerrariumStopTool",
        ):
            assert hasattr(tool_registry, name), name


# ---------------------------------------------------------------------------
# terrarium/persistence.py
# ---------------------------------------------------------------------------


class TestBuildConversationFromMessages:
    """Tests for build_conversation_from_messages."""

    def test_basic(self):
        """Rebuild a simple conversation from message dicts."""
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        conv = build_conversation_from_messages(messages)
        assert isinstance(conv, Conversation)
        raw = conv.get_messages()
        assert len(raw) == 3
        assert raw[0].role == "system"
        assert raw[0].content == "You are helpful."
        assert raw[1].role == "user"
        assert raw[1].content == "Hello"
        assert raw[2].role == "assistant"
        assert raw[2].content == "Hi there!"

    def test_with_tool_calls(self):
        """Messages with tool_calls and tool_call_id are preserved."""
        tool_calls = [
            {
                "id": "call_abc",
                "type": "function",
                "function": {"name": "bash", "arguments": '{"cmd": "ls"}'},
            }
        ]
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": tool_calls,
            },
            {
                "role": "tool",
                "content": "file1.txt\nfile2.txt",
                "tool_call_id": "call_abc",
                "name": "bash",
            },
        ]
        conv = build_conversation_from_messages(messages)
        raw = conv.get_messages()
        assert len(raw) == 2

        assistant_msg = raw[0]
        assert assistant_msg.role == "assistant"
        assert assistant_msg.tool_calls == tool_calls

        tool_msg = raw[1]
        assert tool_msg.role == "tool"
        assert tool_msg.tool_call_id == "call_abc"
        assert tool_msg.name == "bash"

    def test_empty(self):
        """Empty message list produces an empty Conversation."""
        conv = build_conversation_from_messages([])
        assert isinstance(conv, Conversation)
        assert len(conv.get_messages()) == 0


# ---------------------------------------------------------------------------
# terrarium/factory.py
# ---------------------------------------------------------------------------


class TestInjectPromptSection:
    """Tests for inject_prompt_section."""

    def test_appends_to_system(self):
        """Section text is appended to the existing system message."""
        # Build a minimal mock agent with a real Conversation
        conv = Conversation()
        conv.append("system", "Base prompt.")
        conv.append("user", "Hello")

        agent = MagicMock()
        agent.controller.conversation = conv
        # Delegate get_system_message to the real conversation
        agent.controller.conversation.get_system_message = conv.get_system_message

        inject_prompt_section(agent, "## Extra Section\nMore info.")

        sys_msg = conv.get_system_message()
        assert sys_msg is not None
        assert sys_msg.content == "Base prompt.\n\n## Extra Section\nMore info."

    def test_no_system_message_is_noop(self):
        """If no system message exists, inject_prompt_section does nothing."""
        conv = Conversation()
        conv.append("user", "Hello")

        agent = MagicMock()
        agent.controller.conversation = conv
        agent.controller.conversation.get_system_message = conv.get_system_message

        # Should not raise
        inject_prompt_section(agent, "## Extra")
        # No system message to modify
        assert conv.get_system_message() is None


class TestBuildRootAwarenessPrompt:
    """Tests for build_root_awareness_prompt."""

    @staticmethod
    def _make_config(
        creatures: list[str] | None = None,
        channels: list[ChannelConfig] | None = None,
    ) -> TerrariumConfig:
        creature_list = []
        for name in creatures or []:
            creature_list.append(
                CreatureConfig(
                    name=name,
                    config_data={},
                    base_dir=Path("."),
                )
            )
        return TerrariumConfig(
            name="test_terrarium",
            creatures=creature_list,
            channels=channels or [],
        )

    def test_contains_creatures(self):
        """Prompt lists all creature names."""
        config = self._make_config(creatures=["writer", "reviewer", "planner"])
        prompt = build_root_awareness_prompt(config)

        assert "writer" in prompt
        assert "reviewer" in prompt
        assert "planner" in prompt
        assert "writer, reviewer, planner" in prompt

    def test_contains_channels(self):
        """Prompt lists channel names and types."""
        channels = [
            ChannelConfig(
                name="draft", channel_type="queue", description="Draft review"
            ),
            ChannelConfig(name="announce", channel_type="broadcast"),
        ]
        config = self._make_config(creatures=["a", "b"], channels=channels)
        prompt = build_root_awareness_prompt(config)

        assert "`draft` (queue)" in prompt
        assert "Draft review" in prompt
        assert "`announce` (broadcast)" in prompt

    def test_contains_terrarium_name(self):
        """Prompt includes the terrarium name for tool calls."""
        config = self._make_config(creatures=["x"])
        prompt = build_root_awareness_prompt(config)
        assert "test_terrarium" in prompt

    def test_direct_channels_listed(self):
        """Each creature gets a direct channel entry."""
        config = self._make_config(creatures=["alpha", "beta"])
        prompt = build_root_awareness_prompt(config)
        assert "`alpha` (queue)" in prompt
        assert "`beta` (queue)" in prompt
        assert "direct channel to alpha" in prompt
        assert "direct channel to beta" in prompt


# ---------------------------------------------------------------------------
# kohakuterrarium/api/events.py
# ---------------------------------------------------------------------------


class TestGetEventLog:
    """Tests for get_event_log."""

    def setup_method(self):
        """Clear the global event log dict before each test."""
        from kohakuterrarium.studio.attach._event_stream import _event_logs

        _event_logs.clear()

    def test_creates_new(self):
        """get_event_log creates a fresh list for an unknown key."""
        from kohakuterrarium.studio.attach._event_stream import get_event_log

        log = get_event_log("terrarium:writer")
        assert isinstance(log, list)
        assert len(log) == 0

    def test_returns_existing(self):
        """get_event_log returns the same list on repeated calls."""
        from kohakuterrarium.studio.attach._event_stream import get_event_log

        log1 = get_event_log("terrarium:writer")
        log1.append({"type": "test"})
        log2 = get_event_log("terrarium:writer")
        assert log1 is log2
        assert len(log2) == 1


class TestParseDetail:
    """Tests for _parse_detail."""

    def test_with_brackets(self):
        """Extracts name from [name] prefix."""
        from kohakuterrarium.studio.attach._event_stream import _parse_detail

        name, detail = _parse_detail("[bash] Running ls command")
        assert name == "bash"
        assert detail == "Running ls command"

    def test_without_brackets(self):
        """Returns 'unknown' when no bracket prefix."""
        from kohakuterrarium.studio.attach._event_stream import _parse_detail

        name, detail = _parse_detail("plain detail text")
        assert name == "unknown"
        assert detail == "plain detail text"

    def test_malformed(self):
        """Handles opening bracket without closing bracket."""
        from kohakuterrarium.studio.attach._event_stream import _parse_detail

        name, detail = _parse_detail("[broken detail text")
        assert name == "unknown"
        assert detail == "[broken detail text"


class TestStreamOutput:
    """Tests for StreamOutput."""

    def _make_stream(self):
        from kohakuterrarium.studio.attach._event_stream import StreamOutput

        queue = asyncio.Queue()
        log: list = []
        stream = StreamOutput(source="writer", queue=queue, log=log)
        return stream, queue, log

    @pytest.mark.asyncio
    async def test_write(self):
        """write() enqueues a text message and appends to log."""
        stream, queue, log = self._make_stream()
        await stream.write("Hello world")

        assert queue.qsize() == 1
        msg = queue.get_nowait()
        assert msg["type"] == "text"
        assert msg["content"] == "Hello world"
        assert msg["source"] == "writer"
        assert "ts" in msg

        # Log mirrors the queue
        assert len(log) == 1
        assert log[0] is msg

    def test_on_activity(self):
        """on_activity() enqueues an activity message with parsed detail."""
        stream, queue, log = self._make_stream()
        stream.on_activity("tool_start", "[bash] ls -la")

        msg = queue.get_nowait()
        assert msg["type"] == "activity"
        assert msg["activity_type"] == "tool_start"
        assert msg["name"] == "bash"
        assert msg["detail"] == "ls -la"
        assert msg["id"] == "tool_start_0"
        assert msg["source"] == "writer"

    def test_on_activity_with_metadata(self):
        """on_activity_with_metadata() includes selected metadata keys."""
        stream, queue, log = self._make_stream()
        metadata = {
            "job_id": "j_123",
            "result": "success",
            "total_tokens": 500,
            "irrelevant_key": "should_not_appear",
        }
        stream.on_activity_with_metadata("tool_end", "[bash] done", metadata)

        msg = queue.get_nowait()
        assert msg["type"] == "activity"
        assert msg["job_id"] == "j_123"
        assert msg["result"] == "success"
        assert msg["total_tokens"] == 500
        assert "irrelevant_key" not in msg

    def test_activity_counter_increments(self):
        """Each activity call increments the counter in the id."""
        stream, queue, _ = self._make_stream()
        stream.on_activity("a", "[x] first")
        stream.on_activity("b", "[y] second")

        msg1 = queue.get_nowait()
        msg2 = queue.get_nowait()
        assert msg1["id"] == "a_0"
        assert msg2["id"] == "b_1"

    @pytest.mark.asyncio
    async def test_write_stream_empty_ignored(self):
        """write_stream with empty string does not enqueue."""
        stream, queue, log = self._make_stream()
        await stream.write_stream("")
        assert queue.qsize() == 0
        assert len(log) == 0
