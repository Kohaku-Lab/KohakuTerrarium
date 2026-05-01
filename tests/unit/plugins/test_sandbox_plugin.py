from pathlib import Path

import pytest

from kohakuterrarium.builtins.plugins.sandbox.plugin import SandboxPlugin
from kohakuterrarium.modules.plugin.base import PluginBlockError


class DummyContext:
    def __init__(self, working_dir: Path) -> None:
        self.working_dir = working_dir


def test_sandbox_plugin_exposes_runtime_options() -> None:
    schema = SandboxPlugin.option_schema()

    assert schema["enabled"]["type"] == "bool"
    assert schema["backend"]["values"] == ["auto", "audit", "off"]
    assert schema["profile"]["values"] == [
        "PURE",
        "READ_ONLY",
        "WORKSPACE",
        "NETWORK",
        "SHELL",
    ]


def test_sandbox_plugin_stores_defaults() -> None:
    plugin = SandboxPlugin()

    assert plugin.get_options()["enabled"] is True
    assert plugin.get_options()["profile"] == "WORKSPACE"


def test_sandbox_plugin_exposes_generic_subprocess_service(tmp_path) -> None:
    plugin = SandboxPlugin(profile="SHELL")
    context = DummyContext(tmp_path)

    assert plugin.runtime_services(context)["subprocess_runner"] is plugin

    plugin.set_options({"backend": "off"})

    assert plugin.runtime_services(context) == {}


@pytest.mark.asyncio
async def test_sandbox_plugin_blocks_workspace_write_escape(tmp_path) -> None:
    plugin = SandboxPlugin()
    context = DummyContext(tmp_path)

    with pytest.raises(PluginBlockError):
        await plugin.pre_tool_execute(
            {"path": str(tmp_path.parent / "outside.txt")},
            tool_name="write",
            context=context,
        )


@pytest.mark.asyncio
async def test_sandbox_plugin_can_be_fully_disabled(tmp_path) -> None:
    plugin = SandboxPlugin(enabled=False)
    context = DummyContext(tmp_path)

    result = await plugin.pre_tool_execute(
        {"path": str(tmp_path.parent / "outside.txt")},
        tool_name="write",
        context=context,
    )

    assert result is None


@pytest.mark.asyncio
async def test_sandbox_plugin_audit_does_not_block(tmp_path) -> None:
    plugin = SandboxPlugin(backend="audit")
    context = DummyContext(tmp_path)

    result = await plugin.pre_tool_execute(
        {"path": str(tmp_path.parent / "outside.txt")},
        tool_name="write",
        context=context,
    )

    assert result is None


@pytest.mark.asyncio
async def test_sandbox_plugin_blocks_network_when_denied(tmp_path) -> None:
    plugin = SandboxPlugin(network="deny")
    context = DummyContext(tmp_path)

    with pytest.raises(PluginBlockError):
        await plugin.pre_tool_execute(
            {"url": "https://example.com"},
            tool_name="web_fetch",
            context=context,
        )
