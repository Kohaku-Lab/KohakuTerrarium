"""Starters: the catalog, and that every module starter runs as written."""

import ast
import asyncio
from pathlib import Path

import pytest

from kohakuterrarium.core.loader import ModuleLoader
from kohakuterrarium.modules.output.base import BaseOutputModule
from kohakuterrarium.modules.plugin.base import BasePlugin, PluginBlockError
from kohakuterrarium.modules.subagent.config import SubAgentConfig
from kohakuterrarium.modules.tool.base import BaseTool, ToolContext
from kohakuterrarium.modules.trigger.base import BaseTrigger
from kohakuterrarium.studio.editors.modules_crud import render_module
from kohakuterrarium.studio.editors.starters import (
    STARTER_KINDS,
    UnknownStarterError,
    get_starter,
    list_starters,
    starter_form,
)
from kohakuterrarium.studio.editors.wiring import config_var_of, first_class_name


def _load(tmp_path: Path, kind: str, starter: str, **options):
    """Write a starter's module and instantiate it the way bootstrap does."""
    path = tmp_path / f"{kind}_{starter}.py"
    source = render_module(kind, f"{kind}_{starter}", starter)
    path.write_text(source, encoding="utf-8")
    loader = ModuleLoader()
    if kind == "subagents":
        return loader.load_config_object(str(path), config_var_of(source))
    return loader.load_instance(str(path), first_class_name(source), options=options)


class TestCatalog:
    def test_every_kind_has_a_blank_default_listed_first(self):
        for kind in STARTER_KINDS:
            starters = list_starters(kind)
            assert starters and starters[0].id == "blank", kind
            assert all(s.kind == kind and s.label and s.summary for s in starters)
        assert get_starter("tools", None).id == "blank"
        assert len(list_starters()) == sum(len(list_starters(k)) for k in STARTER_KINDS)

    def test_unknown_kinds_and_ids_are_refused(self):
        with pytest.raises(UnknownStarterError, match="unknown kind"):
            list_starters("widgets")
        with pytest.raises(UnknownStarterError, match="no tools starter 'nope'"):
            get_starter("tools", "nope")
        assert isinstance(UnknownStarterError("x"), ValueError)

    def test_forms_are_private_copies(self):
        form = starter_form("tools", "blank")
        form["parameters"]["properties"].clear()
        assert starter_form("tools", "blank")["parameters"]["properties"]

    def test_every_module_starter_compiles_with_its_imports_at_the_top(self):
        for starter in list_starters():
            if starter.kind == "creatures":
                continue
            source = render_module(starter.kind, "demo", starter.id)
            tree = ast.parse(source)
            nested = [
                inner
                for node in tree.body
                if isinstance(node, (ast.ClassDef, ast.FunctionDef))
                for inner in ast.walk(node)
                if isinstance(inner, (ast.Import, ast.ImportFrom))
            ]
            assert nested == [], f"{starter.kind}/{starter.id}"


class TestToolsRun:
    async def test_blank_echoes_and_declares_its_arguments(self, tmp_path):
        tool = _load(tmp_path, "tools", "blank")
        assert isinstance(tool, BaseTool) and tool.tool_name == "tools_blank"
        assert tool.get_parameters_schema()["required"] == ["text"]
        result = await tool.execute({"text": "hello"})
        assert result.success and result.output == "hello"

    async def test_web_api_refuses_a_non_http_url_without_any_request(self, tmp_path):
        result = await _load(tmp_path, "tools", "web_api").execute({"url": "file:///x"})
        assert not result.success and "http" in result.error

    async def test_workspace_file_resolves_against_the_working_dir(self, tmp_path):
        (tmp_path / "notes.txt").write_text("a\nb\nc\n", encoding="utf-8")
        tool = _load(tmp_path, "tools", "workspace_file")
        ctx = ToolContext(agent_name="t", session=None, working_dir=tmp_path)
        assert (
            await tool.execute({"path": "notes.txt"}, ctx)
        ).output == "notes.txt: 3 lines"
        assert not (await tool.execute({"path": "missing.txt"}, ctx)).success

    async def test_long_job_is_background_and_clamps_its_wait(self, tmp_path):
        tool = _load(tmp_path, "tools", "long_job")
        assert tool.execution_mode.value == "background"
        assert (await tool.execute({"seconds": -5})).output == "waited 0s"


class TestPluginsRun:
    async def test_blank_observes_and_keeps_the_result(self, tmp_path):
        plugin = _load(tmp_path, "plugins", "blank")
        assert isinstance(plugin, BasePlugin) and plugin.name == "plugins_blank"
        assert await plugin.post_tool_execute("out", tool_name="read") is None

    async def test_tool_guard_blocks_only_the_listed_tools(self, tmp_path):
        plugin = _load(tmp_path, "plugins", "tool_guard", blocked_tools=["write"])
        with pytest.raises(PluginBlockError, match="write is blocked"):
            await plugin.pre_tool_execute({}, tool_name="write")
        assert await plugin.pre_tool_execute({}, tool_name="bash") is None

    async def test_llm_note_appends_its_note(self, tmp_path):
        plugin = _load(tmp_path, "plugins", "llm_note", note="Be kind.")
        out = await plugin.pre_llm_call([{"role": "user", "content": "hi"}])
        assert out[-1] == {"role": "system", "content": "Be kind."}
        assert len(out) == 2


class TestTriggersRun:
    async def test_blank_fires_after_its_interval_with_the_prompt(self, tmp_path):
        trigger = _load(tmp_path, "triggers", "blank", prompt="wake", interval=0)
        assert isinstance(trigger, BaseTrigger)
        await trigger.start()
        event = await trigger.wait_for_trigger()
        assert event.type == "timer" and event.content == "wake"
        await trigger.stop()
        assert await trigger.wait_for_trigger() is None

    async def test_file_change_fires_when_the_file_changes(self, tmp_path):
        watched = tmp_path / "watched.txt"
        watched.write_text("one", encoding="utf-8")
        trigger = _load(
            tmp_path,
            "triggers",
            "file_change",
            prompt=None,
            path=str(watched),
            poll=0.01,
        )
        await trigger.start()
        waiting = asyncio.create_task(trigger.wait_for_trigger())
        await asyncio.sleep(0.05)
        assert not waiting.done()
        watched.unlink()
        event = await asyncio.wait_for(waiting, 2)
        assert event.type == "file_change" and "watched.txt" in event.content


class TestIoRun:
    async def test_input_turns_each_appended_line_into_a_message(self, tmp_path):
        inbox = tmp_path / "inbox.txt"
        inbox.write_text("old line\n", encoding="utf-8")
        module = _load(tmp_path, "inputs", "blank", path=str(inbox), poll=0.01)
        await module.start()
        waiting = asyncio.create_task(module.get_input())
        await asyncio.sleep(0.05)
        assert not waiting.done()
        with inbox.open("a", encoding="utf-8") as f:
            f.write("\nfirst\nsecond\n")
        assert (await asyncio.wait_for(waiting, 2)).content == "first"
        assert (await asyncio.wait_for(module.get_input(), 2)).content == "second"
        await module.stop()

    async def test_output_appends_each_turn(self, tmp_path):
        target = tmp_path / "out" / "log.md"
        module = _load(tmp_path, "outputs", "blank", path=str(target))
        assert isinstance(module, BaseOutputModule)
        await module.write("one\n")
        await module.write("two")
        assert target.read_text(encoding="utf-8") == "one\n\ntwo\n\n"

    async def test_webhook_without_a_url_sends_nothing(self, tmp_path):
        await _load(tmp_path, "outputs", "webhook").write("hello")


class TestSubagents:
    def test_a_hyphenated_name_still_binds_a_python_identifier(self):
        source = render_module("subagents", "my-helper", None)
        assert config_var_of(source) == "MY_HELPER_CONFIG"
        compile(source, "my-helper.py", "exec")

    def test_each_starter_is_a_subagent_config_with_its_tools(self, tmp_path):
        explore = _load(tmp_path, "subagents", "blank")
        assert isinstance(explore, SubAgentConfig)
        assert explore.can_modify is False and "grep" in explore.tools
        editor = _load(tmp_path, "subagents", "editor")
        assert editor.can_modify is True and "edit" in editor.tools
