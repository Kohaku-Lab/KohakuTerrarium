"""Integration test: output-wiring resolver is installed by TerrariumRuntime.

This test verifies the end-to-end config → runtime path:

1. A creature config can declare ``output_wiring:``.
2. ``build_agent_config`` parses it into ``list[OutputWiringEntry]``.
3. ``TerrariumRuntime.start()`` builds a ``TerrariumOutputWiringResolver``
   and installs it on every creature's ``_wiring_resolver`` field.
4. The resolver correctly resolves creature names and the magic
   ``root`` target using the live terrarium state.

No LLM calls happen here — we only inspect runtime state after start().
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from kohakuterrarium.core.output_wiring import (
    OutputWiringEntry,
    ROOT_TARGET,
)
from kohakuterrarium.core.session import remove_session
from kohakuterrarium.terrarium.config import (
    ChannelConfig,
    CreatureConfig,
    RootConfig,
    TerrariumConfig,
)
from kohakuterrarium.terrarium.output_wiring import TerrariumOutputWiringResolver
from kohakuterrarium.terrarium.runtime import TerrariumRuntime

SWE_AGENT_DIR = (
    Path(__file__).resolve().parents[2] / "examples" / "agent-apps" / "swe_agent"
)


def _terrarium_config_with_wiring(*, include_root: bool) -> TerrariumConfig:
    """Build a tiny two-creature terrarium with output_wiring declared."""
    swe_path = str(SWE_AGENT_DIR.resolve())

    alpha_config_data: dict = {
        "base_config": swe_path,
        "output_wiring": [
            {"to": "beta"},
            {"to": "root", "with_content": False},
        ],
    }
    beta_config_data: dict = {
        "base_config": swe_path,
        "output_wiring": ["alpha"],  # shorthand
    }

    creatures = [
        CreatureConfig(
            name="alpha",
            config_data=alpha_config_data,
            base_dir=Path("."),
            listen_channels=[],
            send_channels=[],
        ),
        CreatureConfig(
            name="beta",
            config_data=beta_config_data,
            base_dir=Path("."),
            listen_channels=[],
            send_channels=[],
        ),
    ]

    root: RootConfig | None = None
    if include_root:
        root = RootConfig(
            config_data={"base_config": swe_path},
            base_dir=Path("."),
        )

    return TerrariumConfig(
        name="test_wiring_terrarium",
        creatures=creatures,
        channels=[
            ChannelConfig(name="noop_ch", channel_type="queue", description=""),
        ],
        root=root,
    )


@pytest.fixture(autouse=True)
def cleanup_sessions():
    yield
    remove_session("terrarium_test_wiring_terrarium")
    # Root agent session is under its own env_id; clean up defensively.
    remove_session("root_test_wiring_terrarium")


class TestOutputWiringConfigFlow:
    async def test_wiring_flows_from_config_to_agent(self):
        """Output wiring declared in the YAML reaches AgentConfig.output_wiring."""
        cfg = _terrarium_config_with_wiring(include_root=False)
        runtime = TerrariumRuntime(cfg)

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "fake-key-for-test"}):
            await runtime.start()

        try:
            alpha_agent = runtime._creatures["alpha"].agent
            beta_agent = runtime._creatures["beta"].agent

            alpha_wiring = alpha_agent.config.output_wiring
            assert isinstance(alpha_wiring, list)
            assert all(isinstance(e, OutputWiringEntry) for e in alpha_wiring)
            assert [e.to for e in alpha_wiring] == ["beta", "root"]
            assert alpha_wiring[0].with_content is True
            assert alpha_wiring[1].with_content is False

            beta_wiring = beta_agent.config.output_wiring
            assert [e.to for e in beta_wiring] == ["alpha"]
            assert beta_wiring[0].with_content is True
        finally:
            await runtime.stop()


class TestResolverInstallation:
    async def test_every_creature_gets_the_resolver(self):
        cfg = _terrarium_config_with_wiring(include_root=False)
        runtime = TerrariumRuntime(cfg)

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "fake-key-for-test"}):
            await runtime.start()

        try:
            for name, handle in runtime._creatures.items():
                resolver = handle.agent._wiring_resolver
                assert resolver is not None, f"{name} has no wiring resolver"
                assert isinstance(resolver, TerrariumOutputWiringResolver)
        finally:
            await runtime.stop()

    async def test_root_gets_the_resolver_too(self):
        cfg = _terrarium_config_with_wiring(include_root=True)
        runtime = TerrariumRuntime(cfg)

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "fake-key-for-test"}):
            await runtime.start()

        try:
            assert runtime._root_agent is not None
            assert isinstance(
                runtime._root_agent._wiring_resolver, TerrariumOutputWiringResolver
            )
        finally:
            await runtime.stop()

    async def test_resolver_resolves_creature_and_root_targets(self):
        cfg = _terrarium_config_with_wiring(include_root=True)
        runtime = TerrariumRuntime(cfg)

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "fake-key-for-test"}):
            await runtime.start()

        try:
            alpha_agent = runtime._creatures["alpha"].agent
            resolver = alpha_agent._wiring_resolver

            # Resolver correctly resolves another creature by name.
            beta_resolved = resolver._resolve_target("beta")
            assert beta_resolved is runtime._creatures["beta"].agent

            # Resolver correctly resolves the magic 'root' target.
            root_resolved = resolver._resolve_target(ROOT_TARGET)
            assert root_resolved is runtime._root_agent

            # Unknown targets resolve to None.
            assert resolver._resolve_target("unknown") is None
        finally:
            await runtime.stop()
