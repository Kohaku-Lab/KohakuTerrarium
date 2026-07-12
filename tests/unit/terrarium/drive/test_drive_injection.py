"""Unit tests for :mod:`terrarium.drive.injection`.

Pins: install adds exactly the five self-service tools + the prompt plugin;
it is idempotent (a second run duplicates neither); refresh swaps the snapshot;
a fresh agent reports no injection until installed.
"""

import kohakuterrarium as kt
from kohakuterrarium.core.config_types import AgentConfig, InputConfig, OutputConfig
from kohakuterrarium.terrarium.drive.injection import (
    has_drive_injection,
    install_drive_runtime,
    refresh_drive_prompt,
)
from kohakuterrarium.terrarium.drive.registration import (
    DriveRegistrationDescriptor,
    GenericDriveRegistration,
)
from kohakuterrarium.terrarium.drive.snapshot import EnabledRegistrySnapshot
from kohakuterrarium.terrarium.drive.tools import SELF_SERVICE_TOOL_NAMES
from kohakuterrarium.testing.llm import ScriptedLLM


class _AltRegistration:
    name = "zzz_alt"
    kind = "alt"
    schema_version = 1

    def descriptor(self):
        return DriveRegistrationDescriptor(
            name=self.name,
            kind=self.kind,
            schema_version=1,
            required_roles=frozenset({"spec"}),
            prompt_contribution="ALT-KIND-PROSE",
        )

    def validate_spec(self, spec):
        return None

    def prompt_contribution(self):
        return "ALT-KIND-PROSE"


class _FakeRuntime:
    """Minimal runtime handle — injection only reads ``.snapshot``."""

    def __init__(self, snapshot):
        self.snapshot = snapshot


def _cfg(tmp_path, name="injc"):
    return AgentConfig(
        name=name,
        system_prompt="You are a test agent.",
        include_hints_in_prompt=False,
        agent_path=tmp_path,
        input=InputConfig(type="none"),
        output=OutputConfig(type="none"),
    )


async def _agent(tmp_path):
    agent = await kt.Agent.build(_cfg(tmp_path), llm=ScriptedLLM(["x"]))
    await agent.start()
    return agent


async def test_fresh_agent_has_no_injection(tmp_path):
    agent = await _agent(tmp_path)
    try:
        assert has_drive_injection(agent) is False
    finally:
        await agent.stop()


async def test_install_adds_tools_and_prompt(tmp_path):
    agent = await _agent(tmp_path)
    try:
        rt = _FakeRuntime(EnabledRegistrySnapshot.build([GenericDriveRegistration()]))
        await install_drive_runtime(agent, rt)
        for name in SELF_SERVICE_TOOL_NAMES:
            assert name in agent.registry.list_tools()
        assert has_drive_injection(agent) is True
        assert agent.plugins.get_plugin("drive_runtime") is not None
        prompt = agent.get_system_prompt()
        assert "Drive runtime" in prompt
        assert "Drive kind: generic" in prompt
    finally:
        await agent.stop()


async def test_install_is_idempotent(tmp_path):
    agent = await _agent(tmp_path)
    try:
        rt = _FakeRuntime(EnabledRegistrySnapshot.build([GenericDriveRegistration()]))
        await install_drive_runtime(agent, rt)
        await install_drive_runtime(agent, rt)
        # Each tool present exactly once (registry keys by name).
        tools = agent.registry.list_tools()
        for name in SELF_SERVICE_TOOL_NAMES:
            assert tools.count(name) == 1
        # Exactly one prompt plugin, one contract block.
        drive_plugins = [
            p for p in agent.plugins.list_plugins() if p["name"] == "drive_runtime"
        ]
        assert len(drive_plugins) == 1
        assert agent.get_system_prompt().count("## Drive runtime") == 1
    finally:
        await agent.stop()


async def test_refresh_swaps_snapshot_prose(tmp_path):
    agent = await _agent(tmp_path)
    try:
        rt = _FakeRuntime(EnabledRegistrySnapshot.build([GenericDriveRegistration()]))
        await install_drive_runtime(agent, rt)
        assert "ALT-KIND-PROSE" not in agent.get_system_prompt()
        new_snapshot = EnabledRegistrySnapshot.build(
            [GenericDriveRegistration(), _AltRegistration()]
        )
        refresh_drive_prompt(agent, new_snapshot)
        prompt = agent.get_system_prompt()
        assert prompt.count("ALT-KIND-PROSE") == 1
        assert prompt.count("## Drive runtime") == 1
    finally:
        await agent.stop()


async def test_refresh_noop_without_prior_install(tmp_path):
    agent = await _agent(tmp_path)
    try:
        # No plugin installed — refresh must be a clean no-op.
        refresh_drive_prompt(
            agent, EnabledRegistrySnapshot.build([GenericDriveRegistration()])
        )
        assert "Drive kind: generic" not in agent.get_system_prompt()
    finally:
        await agent.stop()


async def test_non_privileged_agent_gets_no_group_drive(tmp_path):
    # A creature without the privileged group tools must NOT receive group_drive
    # (design §9.3 — it is a privileged-only surface).
    agent = await _agent(tmp_path)
    try:
        rt = _FakeRuntime(EnabledRegistrySnapshot.build([GenericDriveRegistration()]))
        await install_drive_runtime(agent, rt)
        assert "group_drive" not in agent.registry.list_tools()
    finally:
        await agent.stop()


async def test_privileged_agent_gets_group_drive(tmp_path):
    # Once the privileged group tools are registered (add_creature / assign_root),
    # injection detects privilege and adds group_drive — idempotently.
    from kohakuterrarium.terrarium.tools_group import force_register_privileged_tools

    agent = await _agent(tmp_path)
    try:
        rt = _FakeRuntime(EnabledRegistrySnapshot.build([GenericDriveRegistration()]))
        force_register_privileged_tools(agent)
        await install_drive_runtime(agent, rt)
        await install_drive_runtime(agent, rt)  # idempotent
        assert agent.registry.list_tools().count("group_drive") == 1
    finally:
        await agent.stop()
