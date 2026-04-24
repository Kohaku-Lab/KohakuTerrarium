"""Integration tests for the hot-plug system.

Tests runtime modification of agents and terrariums:
- Adding/removing triggers on a running agent
- Updating system prompts at runtime
- Adding/removing creatures and channels on a running terrarium

NOTE: These tests are written against the expected hot-plug API.
If the implementing agents have not finished yet, some tests may
fail with AttributeError. That is expected and not a bug in the tests.
"""

import asyncio
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.channel import ChannelMessage
from kohakuterrarium.core.config import load_agent_config
from kohakuterrarium.core.session import remove_session
from kohakuterrarium.modules.trigger.channel import ChannelTrigger
from kohakuterrarium.terrarium.config import (
    ChannelConfig,
    CreatureConfig,
    TerrariumConfig,
)
from kohakuterrarium.terrarium.runtime import TerrariumRuntime

# ---------------------------------------------------------------------------
# Shared paths
# ---------------------------------------------------------------------------

SWE_AGENT_DIR = (
    Path(__file__).resolve().parents[2] / "examples" / "agent-apps" / "swe_agent"
)

# Fake API key for all tests that create real Agent instances
FAKE_ENV = {"OPENROUTER_API_KEY": "fake-key-for-test"}


# =========================================================================
# Agent-level hot-plug: triggers
# =========================================================================


class TestAgentHotPlugTriggers:
    """Test adding/removing triggers on a running agent."""

    @pytest.fixture()
    def agent(self):
        """Create a minimal agent from the SWE config with a fake API key."""
        swe_path = str(SWE_AGENT_DIR.resolve())
        with patch.dict(os.environ, FAKE_ENV):
            config = load_agent_config(swe_path)
            agent = Agent(config)
        return agent

    @pytest.fixture(autouse=True)
    def cleanup(self, agent):
        """Ensure the agent is stopped after each test."""
        yield
        if agent.is_running:
            loop = asyncio.get_event_loop()
            loop.run_until_complete(agent.stop())

    async def test_add_trigger_to_running_agent(self, agent):
        """Add a ChannelTrigger to a running agent and verify it registers."""
        with patch.dict(os.environ, FAKE_ENV):
            await agent.start()

        # Create a channel and trigger
        session = agent.session
        session.channels.get_or_create("hotplug_inbox", channel_type="queue")
        trigger = ChannelTrigger(
            channel_name="hotplug_inbox",
            subscriber_id="test_agent",
            session=session,
        )

        initial_count = len(agent.trigger_manager.list())

        # Hot-plug: add the trigger at runtime
        trigger_id = await agent.add_trigger(trigger)

        assert len(agent.trigger_manager.list()) == initial_count + 1
        assert agent.trigger_manager.get(trigger_id) is not None
        assert trigger.is_running

        await agent.stop()

    async def test_remove_trigger(self, agent):
        """Remove a trigger from a running agent."""
        with patch.dict(os.environ, FAKE_ENV):
            await agent.start()

        session = agent.session
        session.channels.get_or_create("removable_ch", channel_type="queue")
        trigger = ChannelTrigger(
            channel_name="removable_ch",
            subscriber_id="test_agent",
            session=session,
        )
        trigger_id = await agent.add_trigger(trigger)
        assert agent.trigger_manager.get(trigger_id) is not None

        # Hot-plug: remove
        await agent.remove_trigger(trigger)

        assert agent.trigger_manager.get(trigger_id) is None
        assert not trigger.is_running

        await agent.stop()

    async def test_remove_nonexistent_trigger_is_noop(self, agent):
        """Removing a trigger that was never added should not raise."""
        with patch.dict(os.environ, FAKE_ENV):
            await agent.start()

        session = agent.session
        trigger = ChannelTrigger(
            channel_name="phantom",
            subscriber_id="ghost",
            session=session,
        )
        # Should complete without error
        await agent.remove_trigger(trigger)

        await agent.stop()

    async def test_add_trigger_fires_on_message(self, agent):
        """A hot-plugged trigger should fire when a message arrives."""
        with patch.dict(os.environ, FAKE_ENV):
            await agent.start()

        session = agent.session
        channel = session.channels.get_or_create("live_ch", channel_type="queue")
        trigger = ChannelTrigger(
            channel_name="live_ch",
            subscriber_id="test_agent",
            session=session,
        )
        await agent.add_trigger(trigger)

        # Send a message on the channel. The trigger task is running
        # in the background and should pick it up.
        msg = ChannelMessage(sender="external", content="hello from hotplug")
        await channel.send(msg)

        # Give the trigger loop a moment to process
        await asyncio.sleep(0.2)

        # We cannot easily verify the event was processed without a real LLM,
        # but we can verify the trigger is still alive and running.
        assert trigger.is_running

        await agent.stop()


# =========================================================================
# Agent-level hot-plug: system prompt
# =========================================================================


class TestAgentSystemPromptUpdate:
    """Test updating system prompt at runtime."""

    @pytest.fixture()
    def agent(self):
        """Create a minimal agent."""
        swe_path = str(SWE_AGENT_DIR.resolve())
        with patch.dict(os.environ, FAKE_ENV):
            config = load_agent_config(swe_path)
            agent = Agent(config)
        return agent

    def test_get_system_prompt(self, agent):
        """Read current system prompt."""
        prompt = agent.get_system_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_update_system_prompt_append(self, agent):
        """Append content to system prompt."""
        original = agent.get_system_prompt()
        addition = "NEW HOTPLUG SECTION: always be helpful"

        agent.update_system_prompt(addition)

        updated = agent.get_system_prompt()
        assert addition in updated
        # Original content should still be present
        assert original in updated

    def test_update_system_prompt_replace(self, agent):
        """Replace entire system prompt."""
        replacement = "You are a completely new agent."

        agent.update_system_prompt(replacement, replace=True)

        updated = agent.get_system_prompt()
        assert updated == replacement

    def test_update_system_prompt_multiple_appends(self, agent):
        """Multiple appends accumulate."""
        agent.update_system_prompt("Section A")
        agent.update_system_prompt("Section B")

        prompt = agent.get_system_prompt()
        assert "Section A" in prompt
        assert "Section B" in prompt


# =========================================================================
# Terrarium-level hot-plug: creatures
# =========================================================================


class TestTerrariumHotPlugCreatures:
    """Test adding/removing creatures to a running terrarium."""

    @pytest.fixture()
    def terrarium_config(self) -> TerrariumConfig:
        """Minimal terrarium with one creature and one channel."""
        swe_path = str(SWE_AGENT_DIR.resolve())
        return TerrariumConfig(
            name="hotplug_test",
            creatures=[
                CreatureConfig(
                    name="alpha",
                    config_data={"base_config": swe_path},
                    base_dir=Path("."),
                    listen_channels=["work"],
                    send_channels=["results"],
                ),
            ],
            channels=[
                ChannelConfig(
                    name="work", channel_type="queue", description="Work items"
                ),
                ChannelConfig(
                    name="results", channel_type="queue", description="Results"
                ),
            ],
        )

    @pytest.fixture(autouse=True)
    def cleanup_sessions(self, terrarium_config: TerrariumConfig):
        """Remove session created by runtime after each test."""
        yield
        remove_session(f"terrarium_{terrarium_config.name}")

    async def test_add_creature(self, terrarium_config: TerrariumConfig):
        """Add a creature to a running terrarium."""
        runtime = TerrariumRuntime(terrarium_config)
        with patch.dict(os.environ, FAKE_ENV):
            await runtime.start()

        try:
            swe_path = str(SWE_AGENT_DIR.resolve())
            new_creature = CreatureConfig(
                name="beta",
                config_data={"base_config": swe_path},
                base_dir=Path("."),
                listen_channels=["results"],
                send_channels=["work"],
            )

            with patch.dict(os.environ, FAKE_ENV):
                await runtime.add_creature(new_creature)

            status = runtime.get_status()
            assert "beta" in status["creatures"]
            assert status["creatures"]["beta"]["listen_channels"] == ["results"]
            assert status["creatures"]["beta"]["send_channels"] == ["work"]
        finally:
            await runtime.stop()

    async def test_add_creature_duplicate_raises(
        self, terrarium_config: TerrariumConfig
    ):
        """Adding a creature with an existing name raises an error."""
        runtime = TerrariumRuntime(terrarium_config)
        with patch.dict(os.environ, FAKE_ENV):
            await runtime.start()

        try:
            swe_path = str(SWE_AGENT_DIR.resolve())
            duplicate = CreatureConfig(
                name="alpha",  # Already exists
                config_data={"base_config": swe_path},
                base_dir=Path("."),
                listen_channels=[],
                send_channels=[],
            )

            with pytest.raises((ValueError, KeyError, RuntimeError)):
                with patch.dict(os.environ, FAKE_ENV):
                    await runtime.add_creature(duplicate)
        finally:
            await runtime.stop()

    async def test_remove_creature(self, terrarium_config: TerrariumConfig):
        """Remove a creature from a running terrarium."""
        # Start with two creatures
        swe_path = str(SWE_AGENT_DIR.resolve())
        terrarium_config.creatures.append(
            CreatureConfig(
                name="beta",
                config_data={"base_config": swe_path},
                base_dir=Path("."),
                listen_channels=["results"],
                send_channels=["work"],
            )
        )

        runtime = TerrariumRuntime(terrarium_config)
        with patch.dict(os.environ, FAKE_ENV):
            await runtime.start()

        try:
            assert "beta" in runtime.get_status()["creatures"]

            result = await runtime.remove_creature("beta")
            assert result is True

            status = runtime.get_status()
            assert "beta" not in status["creatures"]
            assert "alpha" in status["creatures"]
        finally:
            await runtime.stop()

    async def test_remove_nonexistent_returns_false(
        self, terrarium_config: TerrariumConfig
    ):
        """Removing a nonexistent creature returns False."""
        runtime = TerrariumRuntime(terrarium_config)
        with patch.dict(os.environ, FAKE_ENV):
            await runtime.start()

        try:
            result = await runtime.remove_creature("nonexistent")
            assert result is False
        finally:
            await runtime.stop()


# =========================================================================
# Terrarium-level hot-plug: channels
# =========================================================================


class TestTerrariumHotPlugChannels:
    """Test adding channels and wiring at runtime."""

    @pytest.fixture()
    def terrarium_config(self) -> TerrariumConfig:
        """Minimal terrarium for channel tests."""
        swe_path = str(SWE_AGENT_DIR.resolve())
        return TerrariumConfig(
            name="hotplug_ch_test",
            creatures=[
                CreatureConfig(
                    name="alpha",
                    config_data={"base_config": swe_path},
                    base_dir=Path("."),
                    listen_channels=["inbox"],
                    send_channels=["outbox"],
                ),
            ],
            channels=[
                ChannelConfig(name="inbox", channel_type="queue"),
                ChannelConfig(name="outbox", channel_type="queue"),
            ],
        )

    @pytest.fixture(autouse=True)
    def cleanup_sessions(self, terrarium_config: TerrariumConfig):
        """Remove session created by runtime after each test."""
        yield
        remove_session(f"terrarium_{terrarium_config.name}")

    async def test_add_channel(self, terrarium_config: TerrariumConfig):
        """Add a new channel to a running terrarium."""
        runtime = TerrariumRuntime(terrarium_config)
        with patch.dict(os.environ, FAKE_ENV):
            await runtime.start()

        try:
            await runtime.add_channel("new_channel", "queue", "A hot-plugged channel")

            channel_names = runtime.environment.shared_channels.list_channels()
            assert "new_channel" in channel_names

            # Verify the channel has the right type
            ch = runtime.environment.shared_channels.get("new_channel")
            assert ch is not None
            assert ch.channel_type == "queue"
        finally:
            await runtime.stop()

    async def test_add_broadcast_channel(self, terrarium_config: TerrariumConfig):
        """Add a broadcast channel at runtime."""
        runtime = TerrariumRuntime(terrarium_config)
        with patch.dict(os.environ, FAKE_ENV):
            await runtime.start()

        try:
            await runtime.add_channel(
                "announcements", "broadcast", "Team announcements"
            )

            ch = runtime.environment.shared_channels.get("announcements")
            assert ch is not None
            assert ch.channel_type == "broadcast"
        finally:
            await runtime.stop()

    async def test_wire_channel_listen(self, terrarium_config: TerrariumConfig):
        """Wire a creature to listen on a new channel."""
        runtime = TerrariumRuntime(terrarium_config)
        with patch.dict(os.environ, FAKE_ENV):
            await runtime.start()

        try:
            # Add a new channel
            await runtime.add_channel("alerts", "queue", "Alert channel")

            # Wire alpha to listen on it
            await runtime.wire_channel("alpha", "alerts", "listen")

            # Verify the creature now has a trigger for the new channel
            handle = runtime._creatures["alpha"]
            trigger_channels = [
                t.channel_name
                for t in handle.agent.trigger_manager._triggers.values()
                if isinstance(t, ChannelTrigger)
            ]
            assert "alerts" in trigger_channels
        finally:
            await runtime.stop()

    async def test_wire_channel_send(self, terrarium_config: TerrariumConfig):
        """Wire a creature to send on a channel."""
        runtime = TerrariumRuntime(terrarium_config)
        with patch.dict(os.environ, FAKE_ENV):
            await runtime.start()

        try:
            await runtime.add_channel("reports", "queue", "Report channel")

            # Wire alpha to send on it
            await runtime.wire_channel("alpha", "reports", "send")

            handle = runtime._creatures["alpha"]
            assert "reports" in handle.send_channels
        finally:
            await runtime.stop()

    async def test_wire_channel_nonexistent_creature_raises(
        self, terrarium_config: TerrariumConfig
    ):
        """Wiring a nonexistent creature raises an error."""
        runtime = TerrariumRuntime(terrarium_config)
        with patch.dict(os.environ, FAKE_ENV):
            await runtime.start()

        try:
            with pytest.raises((ValueError, KeyError)):
                await runtime.wire_channel("nonexistent", "inbox", "listen")
        finally:
            await runtime.stop()
