"""Integration tests for the core service API (events, AgentSession, KohakuManager).

These tests exercise the API surface defined in ideas/api-design.md.
The implementation files live in src/kohakuterrarium/serving/:
  - events.py       (ChannelEvent, OutputEvent)
  - agent_session.py (AgentSession)
  - manager.py       (KohakuManager)
"""

import os
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from kohakuterrarium.serving.events import ChannelEvent, OutputEvent

# Paths reused across test classes
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SWE_AGENT_DIR = str((PROJECT_ROOT / "examples" / "agent-apps" / "swe_agent").resolve())
NOVEL_TERRARIUM_DIR = str(
    (PROJECT_ROOT / "examples" / "terrariums" / "novel_terrarium").resolve()
)

# Environment patch applied to every test that instantiates agents/terrariums
_FAKE_ENV = {"OPENROUTER_API_KEY": "fake-key-for-test"}


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------


class TestEventTypes:
    """Verify ChannelEvent and OutputEvent dataclasses."""

    def test_channel_event_creation(self):
        """ChannelEvent has all required fields."""
        event = ChannelEvent(
            terrarium_id="t1",
            channel="ideas",
            sender="brainstorm",
            content="A fresh idea",
            message_id="msg_001",
        )
        assert event.terrarium_id == "t1"
        assert event.channel == "ideas"
        assert event.sender == "brainstorm"
        assert event.content == "A fresh idea"
        assert event.message_id == "msg_001"

    def test_output_event_creation(self):
        """OutputEvent has all required fields."""
        event = OutputEvent(
            agent_id="agent_abc",
            event_type="text",
            content="Hello world",
        )
        assert event.agent_id == "agent_abc"
        assert event.event_type == "text"
        assert event.content == "Hello world"

    def test_channel_event_defaults(self):
        """Timestamp and metadata have sensible defaults."""
        before = datetime.now()
        event = ChannelEvent(
            terrarium_id="t1",
            channel="ch",
            sender="s",
            content="c",
            message_id="m1",
        )
        after = datetime.now()

        assert before <= event.timestamp <= after
        assert event.metadata == {}

    def test_output_event_defaults(self):
        """OutputEvent timestamp and metadata have sensible defaults."""
        before = datetime.now()
        event = OutputEvent(
            agent_id="a1",
            event_type="tool_start",
            content="running bash",
        )
        after = datetime.now()

        assert before <= event.timestamp <= after
        assert event.metadata == {}

    def test_channel_event_custom_metadata(self):
        """ChannelEvent accepts custom metadata dict."""
        event = ChannelEvent(
            terrarium_id="t1",
            channel="ch",
            sender="s",
            content="c",
            message_id="m1",
            metadata={"priority": "high"},
        )
        assert event.metadata == {"priority": "high"}


# ---------------------------------------------------------------------------
# AgentSession
# ---------------------------------------------------------------------------


class TestAgentSession:
    """Test AgentSession lifecycle and status."""

    @pytest.fixture(autouse=True)
    def _env(self):
        """Ensure a fake API key is set for all tests in this class."""
        with patch.dict(os.environ, _FAKE_ENV):
            yield

    async def test_create_from_path(self):
        """Create session from config path, verify agent_id, and stop."""
        from kohakuterrarium.serving.agent_session import AgentSession

        session = await AgentSession.from_path(SWE_AGENT_DIR)
        try:
            assert session.agent_id is not None
            assert session.agent_id.startswith("agent_")
            assert session._running is True
        finally:
            await session.stop()

    async def test_get_status(self):
        """Status includes agent_id, name, running, and tools."""
        from kohakuterrarium.serving.agent_session import AgentSession

        session = await AgentSession.from_path(SWE_AGENT_DIR)
        try:
            status = session.get_status()
            assert "agent_id" in status
            assert status["name"] == "swe"
            assert status["running"] is True
            assert isinstance(status["tools"], list)
            assert len(status["tools"]) > 0
        finally:
            await session.stop()

    async def test_session_lifecycle(self):
        """Start and stop lifecycle transitions correctly."""
        from kohakuterrarium.serving.agent_session import AgentSession

        agent = __import__(
            "kohakuterrarium.core.agent", fromlist=["Agent"]
        ).Agent.from_path(SWE_AGENT_DIR)
        session = AgentSession(agent)

        # Before start
        assert session._running is False

        await session.start()
        assert session._running is True
        assert session.agent.is_running is True

        await session.stop()
        assert session._running is False


# ---------------------------------------------------------------------------
# KohakuManager — Agents
# ---------------------------------------------------------------------------


class TestKohakuManagerAgents:
    """Test KohakuManager standalone agent operations."""

    @pytest.fixture(autouse=True)
    def _env(self):
        with patch.dict(os.environ, _FAKE_ENV):
            yield

    @pytest.fixture()
    async def manager(self):
        """Create a KohakuManager and shut it down after the test."""
        from kohakuterrarium.serving.manager import KohakuManager

        mgr = KohakuManager()
        yield mgr
        await mgr.shutdown()

    async def test_create_agent(self, manager):
        """Create a standalone agent and verify it is listed."""
        agent_id = await manager.agent_create(config_path=SWE_AGENT_DIR)
        assert agent_id is not None

        agents = manager.agent_list()
        ids = [a["agent_id"] for a in agents]
        assert agent_id in ids

    async def test_stop_agent(self, manager):
        """Stop an agent and verify it is removed."""
        agent_id = await manager.agent_create(config_path=SWE_AGENT_DIR)
        await manager.agent_stop(agent_id)

        agents = manager.agent_list()
        ids = [a["agent_id"] for a in agents]
        assert agent_id not in ids

    async def test_list_agents(self, manager):
        """List returns all running agents."""
        id1 = await manager.agent_create(config_path=SWE_AGENT_DIR)
        id2 = await manager.agent_create(config_path=SWE_AGENT_DIR)

        agents = manager.agent_list()
        ids = {a["agent_id"] for a in agents}
        assert {id1, id2} <= ids

    async def test_get_agent_status(self, manager):
        """Get status of a specific agent."""
        agent_id = await manager.agent_create(config_path=SWE_AGENT_DIR)
        status = manager.agent_status(agent_id)

        assert status is not None
        assert status["agent_id"] == agent_id
        assert status["name"] == "swe"
        assert status["running"] is True
        assert isinstance(status["tools"], list)

    async def test_stop_nonexistent_agent(self, manager):
        """Stopping a nonexistent agent does not raise."""
        # Should complete without error
        await manager.agent_stop("nonexistent_agent_id_12345")


# ---------------------------------------------------------------------------
# KohakuManager — Terrariums
# ---------------------------------------------------------------------------


class TestKohakuManagerTerrariums:
    """Test KohakuManager terrarium operations."""

    @pytest.fixture(autouse=True)
    def _env(self):
        with patch.dict(os.environ, _FAKE_ENV):
            yield

    @pytest.fixture()
    async def manager(self):
        """Create a KohakuManager and shut it down after the test."""
        from kohakuterrarium.serving.manager import KohakuManager

        mgr = KohakuManager()
        yield mgr
        await mgr.shutdown()

    async def test_create_terrarium(self, manager):
        """Create terrarium from config path."""
        tid = await manager.terrarium_create(config_path=NOVEL_TERRARIUM_DIR)
        assert tid is not None

        terrariums = manager.terrarium_list()
        ids = [t["terrarium_id"] for t in terrariums]
        assert tid in ids

    async def test_stop_terrarium(self, manager):
        """Stop terrarium and verify removed."""
        tid = await manager.terrarium_create(config_path=NOVEL_TERRARIUM_DIR)
        await manager.terrarium_stop(tid)

        terrariums = manager.terrarium_list()
        ids = [t["terrarium_id"] for t in terrariums]
        assert tid not in ids

    async def test_list_terrariums(self, manager):
        """List returns running terrariums."""
        tid = await manager.terrarium_create(config_path=NOVEL_TERRARIUM_DIR)

        listing = manager.terrarium_list()
        assert len(listing) >= 1
        assert any(t["terrarium_id"] == tid for t in listing)

    async def test_get_terrarium_status(self, manager):
        """Status includes creatures and channels."""
        tid = await manager.terrarium_create(config_path=NOVEL_TERRARIUM_DIR)
        status = manager.terrarium_status(tid)

        assert status is not None
        assert "creatures" in status
        assert "channels" in status
        assert status["running"] is True

    async def test_hot_plug_via_manager(self, manager):
        """Add creature/channel through manager."""
        from kohakuterrarium.terrarium.config import CreatureConfig

        tid = await manager.terrarium_create(config_path=NOVEL_TERRARIUM_DIR)

        # Add a new channel
        await manager.terrarium_channel_add(
            tid, name="review", channel_type="queue", description="Review notes"
        )

        status = manager.terrarium_status(tid)
        channel_names = [ch["name"] for ch in status["channels"]]
        assert "review" in channel_names

        # Add a new creature wired to the new channel
        creature_cfg = CreatureConfig(
            name="reviewer",
            config_data={"base_config": SWE_AGENT_DIR},
            base_dir=Path("."),
            listen_channels=["review"],
            send_channels=[],
        )
        creature_name = await manager.creature_add(tid, config=creature_cfg)
        assert creature_name is not None

        status = manager.terrarium_status(tid)
        assert "reviewer" in status["creatures"]

    async def test_send_to_channel(self, manager):
        """Send message to channel via manager."""
        tid = await manager.terrarium_create(config_path=NOVEL_TERRARIUM_DIR)

        # "seed" channel exists in novel_terrarium config
        msg_id = await manager.terrarium_channel_send(
            tid, channel="seed", content="Write about space.", sender="human"
        )
        assert msg_id is not None
        assert isinstance(msg_id, str)
        assert len(msg_id) > 0


# ---------------------------------------------------------------------------
# KohakuManager — Shutdown
# ---------------------------------------------------------------------------


class TestKohakuManagerShutdown:
    """Test KohakuManager full shutdown."""

    @pytest.fixture(autouse=True)
    def _env(self):
        with patch.dict(os.environ, _FAKE_ENV):
            yield

    async def test_shutdown_stops_everything(self):
        """Shutdown stops all agents and terrariums."""
        from kohakuterrarium.serving.manager import KohakuManager

        mgr = KohakuManager()

        await mgr.agent_create(config_path=SWE_AGENT_DIR)
        await mgr.terrarium_create(config_path=NOVEL_TERRARIUM_DIR)

        # Verify they exist
        assert len(mgr.agent_list()) >= 1
        assert len(mgr.terrarium_list()) >= 1

        await mgr.shutdown()

        assert mgr.agent_list() == []
        assert mgr.terrarium_list() == []
