"""Integration tests for the terrarium multi-agent runtime."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from kohakuterrarium.core.session import remove_session
from kohakuterrarium.modules.trigger.channel import ChannelTrigger
from kohakuterrarium.terrarium.config import (
    ChannelConfig,
    CreatureConfig,
    TerrariumConfig,
    build_channel_topology_prompt,
    load_terrarium_config,
)
from kohakuterrarium.terrarium.runtime import TerrariumRuntime

# Paths used across multiple tests
NOVEL_TERRARIUM_DIR = (
    Path(__file__).resolve().parents[2] / "examples" / "terrariums" / "novel_terrarium"
)
SWE_AGENT_DIR = (
    Path(__file__).resolve().parents[2] / "examples" / "agent-apps" / "swe_agent"
)


# ---------------------------------------------------------------------------
# Config loading tests
# ---------------------------------------------------------------------------


class TestConfigLoading:
    """Test load_terrarium_config with the novel_terrarium example."""

    def test_load_novel_terrarium_config(self):
        """Load the real novel_terrarium config and verify top-level fields."""
        config = load_terrarium_config(NOVEL_TERRARIUM_DIR)
        assert config.name == "novel_writer"
        assert len(config.creatures) == 3
        assert len(config.channels) == 6

    def test_creature_names(self):
        """Creature names match the YAML entries."""
        config = load_terrarium_config(NOVEL_TERRARIUM_DIR)
        names = [c.name for c in config.creatures]
        assert names == ["brainstorm", "planner", "writer"]

    def test_channel_types(self):
        """Channel types parsed correctly (queue vs broadcast)."""
        config = load_terrarium_config(NOVEL_TERRARIUM_DIR)
        ch_map = {ch.name: ch for ch in config.channels}

        assert ch_map["ideas"].channel_type == "queue"
        assert ch_map["outline"].channel_type == "queue"
        assert ch_map["team_chat"].channel_type == "broadcast"

    def test_channel_descriptions(self):
        """Channel descriptions survive parsing."""
        config = load_terrarium_config(NOVEL_TERRARIUM_DIR)
        ch_map = {ch.name: ch for ch in config.channels}
        assert "brainstorm" in ch_map["ideas"].description.lower()

    def test_creature_channels(self):
        """Creature listen/send channels match the YAML."""
        config = load_terrarium_config(NOVEL_TERRARIUM_DIR)
        brainstorm = config.creatures[0]
        assert brainstorm.listen_channels == ["seed", "feedback", "team_chat"]
        assert "ideas" in brainstorm.send_channels
        assert "team_chat" in brainstorm.send_channels

    def test_creature_config_data_has_base_config(self):
        """Creature config_data contains base_config for inheritance."""
        config = load_terrarium_config(NOVEL_TERRARIUM_DIR)
        brainstorm = config.creatures[0]
        # config_data should have base_config (converted from old "config" key)
        assert "base_config" in brainstorm.config_data
        assert brainstorm.base_dir == NOVEL_TERRARIUM_DIR

    def test_load_by_directory(self):
        """Passing a directory finds terrarium.yaml automatically."""
        config = load_terrarium_config(NOVEL_TERRARIUM_DIR)
        assert config.name == "novel_writer"

    def test_load_by_file_path(self):
        """Passing the YAML file directly also works."""
        config = load_terrarium_config(NOVEL_TERRARIUM_DIR / "terrarium.yaml")
        assert config.name == "novel_writer"

    def test_missing_path_raises(self):
        """Non-existent path raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_terrarium_config("/tmp/nonexistent_terrarium_xyz")


# ---------------------------------------------------------------------------
# Channel topology prompt tests
# ---------------------------------------------------------------------------


class TestChannelTopologyPrompt:
    """Test build_channel_topology_prompt directly with config objects."""

    def _make_config(
        self,
        channels: list[ChannelConfig],
        creature_listen: list[str] | None = None,
        creature_send: list[str] | None = None,
    ) -> tuple[TerrariumConfig, CreatureConfig]:
        creature = CreatureConfig(
            name="alpha",
            config_data={"name": "alpha"},
            base_dir=Path("."),
            listen_channels=creature_listen or [],
            send_channels=creature_send or [],
        )
        terrarium = TerrariumConfig(
            name="test",
            creatures=[creature],
            channels=channels,
        )
        return terrarium, creature

    def test_listen_channels_marked(self):
        """Channels in listen list are annotated with (listen)."""
        channels = [ChannelConfig(name="inbox", channel_type="queue")]
        config, creature = self._make_config(channels, creature_listen=["inbox"])
        prompt = build_channel_topology_prompt(config, creature)
        assert "(listen)" in prompt
        assert "`inbox`" in prompt

    def test_send_channels_marked(self):
        """Channels in send list are annotated with (send)."""
        channels = [ChannelConfig(name="outbox", channel_type="queue")]
        config, creature = self._make_config(channels, creature_send=["outbox"])
        prompt = build_channel_topology_prompt(config, creature)
        assert "(send)" in prompt
        assert "`outbox`" in prompt

    def test_both_listen_and_send(self):
        """A channel that is both listen and send shows both roles."""
        channels = [ChannelConfig(name="bidir", channel_type="queue")]
        config, creature = self._make_config(
            channels, creature_listen=["bidir"], creature_send=["bidir"]
        )
        prompt = build_channel_topology_prompt(config, creature)
        assert "listen" in prompt
        assert "send" in prompt

    def test_broadcast_always_included(self):
        """Broadcast channels appear even if creature doesn't listen/send."""
        channels = [
            ChannelConfig(name="news", channel_type="broadcast", description="Updates"),
        ]
        config, creature = self._make_config(channels)
        prompt = build_channel_topology_prompt(config, creature)
        assert "`news`" in prompt
        assert "[broadcast]" in prompt

    def test_queue_not_included_if_irrelevant(self):
        """Queue channels are excluded when creature has no relation to them."""
        channels = [
            ChannelConfig(name="private", channel_type="queue"),
        ]
        config, creature = self._make_config(channels)
        prompt = build_channel_topology_prompt(config, creature)
        assert prompt == ""

    def test_description_included(self):
        """Channel description appears in the topology prompt."""
        channels = [
            ChannelConfig(
                name="tasks",
                channel_type="queue",
                description="Pending work items",
            ),
        ]
        config, creature = self._make_config(channels, creature_listen=["tasks"])
        prompt = build_channel_topology_prompt(config, creature)
        assert "Pending work items" in prompt

    def test_send_channel_listed(self):
        """When creature can send, the channel is listed with (send) role."""
        channels = [ChannelConfig(name="out", channel_type="queue")]
        config, creature = self._make_config(channels, creature_send=["out"])
        prompt = build_channel_topology_prompt(config, creature)
        assert "(send)" in prompt

    def test_listen_channel_listed(self):
        """When creature listens, the channel is listed with (listen) role."""
        channels = [ChannelConfig(name="in", channel_type="queue")]
        config, creature = self._make_config(channels, creature_listen=["in"])
        prompt = build_channel_topology_prompt(config, creature)
        assert "(listen)" in prompt

    def test_channel_semantics_explained(self):
        """Prompt explains channel rules (send_message required, triggers automatic)."""
        channels = [ChannelConfig(name="ch", channel_type="queue")]
        config, creature = self._make_config(channels, creature_listen=["ch"])
        prompt = build_channel_topology_prompt(config, creature)
        assert "send_message" in prompt
        assert "automatically" in prompt.lower() or "trigger" in prompt.lower()


# ---------------------------------------------------------------------------
# Runtime lifecycle tests
# ---------------------------------------------------------------------------


class TestRuntimeLifecycle:
    """Test TerrariumRuntime start/stop without running the LLM loop."""

    @pytest.fixture()
    def terrarium_config(self) -> TerrariumConfig:
        """Minimal terrarium config pointing at a real agent directory."""
        swe_path = str(SWE_AGENT_DIR.resolve())
        return TerrariumConfig(
            name="test_terrarium",
            creatures=[
                CreatureConfig(
                    name="alpha",
                    config_data={"base_config": str(swe_path)},
                    base_dir=Path("."),
                    listen_channels=["ch_alpha"],
                    send_channels=["ch_beta"],
                ),
                CreatureConfig(
                    name="beta",
                    config_data={"base_config": str(swe_path)},
                    base_dir=Path("."),
                    listen_channels=["ch_beta"],
                    send_channels=["ch_alpha"],
                ),
            ],
            channels=[
                ChannelConfig(
                    name="ch_alpha", channel_type="queue", description="A to B"
                ),
                ChannelConfig(
                    name="ch_beta", channel_type="queue", description="B to A"
                ),
            ],
        )

    @pytest.fixture(autouse=True)
    def cleanup_sessions(self, terrarium_config: TerrariumConfig):
        """Remove session created by runtime after each test."""
        yield
        remove_session(f"terrarium_{terrarium_config.name}")

    async def test_start_creates_channels(self, terrarium_config: TerrariumConfig):
        """After start(), all declared channels exist in the environment."""
        runtime = TerrariumRuntime(terrarium_config)
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "fake-key-for-test"}):
            await runtime.start()

        try:
            channel_names = runtime.environment.shared_channels.list_channels()
            assert "ch_alpha" in channel_names
            assert "ch_beta" in channel_names
            # Backward-compat: _session.channels points at shared_channels
            assert runtime._session.channels is runtime.environment.shared_channels
        finally:
            await runtime.stop()

    async def test_start_injects_channel_triggers(
        self, terrarium_config: TerrariumConfig
    ):
        """After start(), creatures have ChannelTrigger for their listen channels."""
        runtime = TerrariumRuntime(terrarium_config)
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "fake-key-for-test"}):
            await runtime.start()

        try:
            alpha_handle = runtime._creatures["alpha"]
            trigger_channels = [
                t.channel_name
                for t in alpha_handle.agent.trigger_manager._triggers.values()
                if isinstance(t, ChannelTrigger)
            ]
            assert "ch_alpha" in trigger_channels
        finally:
            await runtime.stop()

    async def test_start_injects_topology_prompt(
        self, terrarium_config: TerrariumConfig
    ):
        """After start(), system prompt mentions the creature's channels."""
        runtime = TerrariumRuntime(terrarium_config)
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "fake-key-for-test"}):
            await runtime.start()

        try:
            alpha_handle = runtime._creatures["alpha"]
            sys_msg = alpha_handle.agent.controller.conversation.get_system_message()
            assert sys_msg is not None
            content = sys_msg.content if isinstance(sys_msg.content, str) else ""
            assert "ch_alpha" in content
            assert "ch_beta" in content
        finally:
            await runtime.stop()

    async def test_stop_cleans_up(self, terrarium_config: TerrariumConfig):
        """After stop(), runtime reports not running."""
        runtime = TerrariumRuntime(terrarium_config)
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "fake-key-for-test"}):
            await runtime.start()
        await runtime.stop()

        assert not runtime._running

    async def test_get_status_structure(self, terrarium_config: TerrariumConfig):
        """get_status() returns expected keys and creature info."""
        runtime = TerrariumRuntime(terrarium_config)
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "fake-key-for-test"}):
            await runtime.start()

        try:
            status = runtime.get_status()
            assert status["name"] == "test_terrarium"
            assert status["running"] is True
            assert "alpha" in status["creatures"]
            assert "beta" in status["creatures"]
            assert "channels" in status
            # Each creature status has expected keys
            alpha_status = status["creatures"]["alpha"]
            assert "running" in alpha_status
            assert "listen_channels" in alpha_status
            assert "send_channels" in alpha_status
            assert alpha_status["listen_channels"] == ["ch_alpha"]
            assert alpha_status["send_channels"] == ["ch_beta"]
        finally:
            await runtime.stop()

    async def test_get_status_before_start(self, terrarium_config: TerrariumConfig):
        """get_status() works before start() with empty creatures."""
        runtime = TerrariumRuntime(terrarium_config)
        status = runtime.get_status()
        assert status["name"] == "test_terrarium"
        assert status["running"] is False
        assert status["creatures"] == {}
