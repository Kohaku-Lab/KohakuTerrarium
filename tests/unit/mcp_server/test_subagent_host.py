"""Independent subagent configuration and resource ownership."""

import json

import pytest

from kohakuterrarium.core.job import JobStore
from kohakuterrarium.llm.backends import save_yaml_store
from kohakuterrarium.mcp_server.delegation_history import DelegationHistory
from kohakuterrarium.mcp_server.subagent_host import SubagentHost
from kohakuterrarium.testing.llm import ScriptedLLM


@pytest.mark.parametrize(
    "extra",
    [
        {"interactive": True},
        {"unknown": True},
        {"tools": ["write", "write"]},
    ],
)
def test_unsupported_or_ambiguous_configuration_rejected(tmp_path, extra):
    path = tmp_path / "subagent.json"
    path.write_text(json.dumps({"name": "worker", **extra}), encoding="utf-8")
    with pytest.raises(ValueError):
        SubagentHost(path, tmp_path, JobStore(), DelegationHistory(), llm=ScriptedLLM())


async def test_independent_plugin_and_workspace_without_added_path_boundary(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    plugin = tmp_path / "guard.py"
    plugin.write_text(
        "from kohakuterrarium.modules.plugin.base import BasePlugin\n"
        "class Guard(BasePlugin):\n"
        "    name = 'guard'\n"
        "    async def on_load(self, context):\n"
        "        (context.working_dir / 'loaded.txt').write_text('loaded')\n"
        "    async def pre_tool_execute(self, args, **kwargs):\n"
        "        return {**args, 'content': 'from plugin'}\n",
        encoding="utf-8",
    )
    path = tmp_path / "subagent.json"
    path.write_text(
        json.dumps(
            {
                "name": "writer",
                "tools": ["write"],
                "can_modify": True,
                "plugins": [
                    {
                        "name": "guard",
                        "type": "custom",
                        "module": "guard.py",
                        "class": "Guard",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    model = ScriptedLLM(
        ["[/write]\n@@path=../outside.txt\n@@content=original\n[write/]", "done"]
    )
    host = SubagentHost(path, workspace, JobStore(), DelegationHistory(), llm=model)
    try:
        await host.start("job", "write")
        assert (workspace / "loaded.txt").read_text() == "loaded"
        assert (await host.wait()).success
        assert (tmp_path / "outside.txt").read_text() == "from plugin"
        assert "outside.txt" in json.dumps(host.conversation())
    finally:
        await host.close()


async def test_failed_plugin_load_unloads_partial_resources(tmp_path):
    plugin = tmp_path / "broken.py"
    plugin.write_text(
        "from kohakuterrarium.modules.plugin.base import BasePlugin\n"
        "class Broken(BasePlugin):\n"
        "    name = 'broken'\n"
        "    async def on_load(self, context):\n"
        "        self.folder = context.working_dir\n"
        "        raise RuntimeError('expected load failure')\n"
        "    async def on_unload(self):\n"
        "        (self.folder / 'unloaded.txt').write_text('unloaded')\n",
        encoding="utf-8",
    )
    path = tmp_path / "subagent.json"
    path.write_text(
        json.dumps(
            {
                "name": "worker",
                "plugins": [
                    {
                        "name": "broken",
                        "type": "custom",
                        "module": "broken.py",
                        "class": "Broken",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    host = SubagentHost(
        path, tmp_path, JobStore(), DelegationHistory(), llm=ScriptedLLM()
    )
    try:
        with pytest.raises(RuntimeError, match="load failure"):
            await host.start("job", "task")
        assert (tmp_path / "unloaded.txt").read_text() == "unloaded"
    finally:
        await host.close()


async def test_registered_provider_is_not_overridden_by_named_child_default(tmp_path):
    save_yaml_store({"subagent_models": {"worker": "@missing-profile"}})
    path = tmp_path / "worker.json"
    path.write_text(json.dumps({"name": "worker", "llm": "default"}), encoding="utf-8")
    model = ScriptedLLM(["configured provider reply"])
    host = SubagentHost(path, tmp_path, JobStore(), DelegationHistory(), llm=model)
    try:
        await host.start("job", "task")
        assert (await host.wait()).output == "configured provider reply"
        assert model.call_count == 1
    finally:
        await host.close()
