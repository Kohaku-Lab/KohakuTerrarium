"""Builtin inspection contracts and retained service presets."""

from contextlib import closing

import pytest

from kohakuterrarium.builtins import subagents as subagent_exports
from kohakuterrarium.builtins.subagent_catalog import (
    get_builtin_subagent_config,
    list_builtin_subagents,
)
from kohakuterrarium.builtins.tools.files.read import ReadTool
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.modules.subagent.manager import SubAgentManager
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.testing.llm import ScriptedLLM

REMOVED = ("worker", "plan", "research", "coordinator")
SERVICES = {"response", "summarize", "memory_read", "memory_write"}


def test_catalog_contains_only_inspection_and_service_presets():
    assert set(list_builtin_subagents()) == {"explore", "critic", "fork"} | SERVICES
    for name in REMOVED:
        assert get_builtin_subagent_config(name) is None
    for name in SERVICES:
        assert get_builtin_subagent_config(name).name == name
    fork = get_builtin_subagent_config("fork")
    assert fork == subagent_exports.FORK_CONFIG
    assert "FORK_CONFIG" in subagent_exports.__all__
    assert fork.fork_context is True
    assert fork.model == "inherit"
    assert fork.budget_inherit is False
    assert fork.tools == ["read", "glob", "grep", "tree"]
    assert fork.can_modify is False
    assert fork.interactive is False
    assert fork.max_turns == 14
    assert fork.timeout == 600
    assert fork.default_plugins == []
    assert fork.compact is None
    assert fork.plugins == [
        {
            "name": "budget",
            "options": {"turn_budget": [8, 12], "tool_call_budget": [24, 40]},
        }
    ]
    for name in ("explore", "critic"):
        assert get_builtin_subagent_config(name).fork_context is False


@pytest.mark.parametrize("name", ["explore", "critic"])
def test_inspection_defaults_are_bounded_and_read_only(name):
    config = get_builtin_subagent_config(name)
    assert set(config.tools) == {"read", "glob", "grep", "tree"}
    assert config.can_modify is False
    assert config.stateless is True
    assert config.interactive is False
    assert config.max_turns == 14
    assert config.timeout == 600
    assert config.default_plugins == []
    assert config.compact is None
    assert config.plugins == [
        {
            "name": "budget",
            "options": {"turn_budget": [8, 12], "tool_call_budget": [24, 40]},
        }
    ]
    prompt = config.load_prompt()
    assert "parent conversation" in prompt
    assert "partial" in prompt.lower()
    assert "200 words" not in prompt
    assert "shared budget owned by the parent" not in prompt
    assert "scope" in config.description.lower()
    assert len(config.description) <= 160


def test_catalog_copies_do_not_share_mutable_defaults():
    first = get_builtin_subagent_config("explore")
    first.plugins[0]["options"]["turn_budget"][0] = 99
    first.tools.append("write")
    second = get_builtin_subagent_config("explore")
    assert second.plugins[0]["options"]["turn_budget"] == [8, 12]
    assert "write" not in second.tools
    assert get_builtin_subagent_config("critic").model == "inherit"


@pytest.mark.parametrize("name", ["explore", "critic"])
async def test_hard_investigation_budget_leaves_room_for_final_report(name, tmp_path):
    target = tmp_path / "evidence.txt"
    target.write_text("observed evidence", encoding="utf-8")
    read_call = f"[/read]\n@@path={target}\n[read/]"
    llm = ScriptedLLM(
        [read_call] * 12 + ["Partial: evidence found; remaining scope unchecked."]
    )
    registry = Registry()
    registry.register_tool(ReadTool())
    manager = SubAgentManager(registry, llm, agent_path=tmp_path, tool_format="bracket")
    config = get_builtin_subagent_config(name)
    manager.register(config)
    try:
        job_id = await manager.spawn(
            name, "Inspect the supplied file.", background=False
        )
        result = manager.get_result(job_id)
        child = manager._jobs[job_id].subagent
        messages = child.conversation.to_messages()
        assert result.turns == 13
        assert "Partial: evidence found" in result.output
        assert any(
            "Budget exhausted" in str(message["content"]) for message in messages
        )
        assert (
            sum("observed evidence" in str(message["content"]) for message in messages)
            == 11
        )
        assert child.plugins.get_plugin("budget").budgets.turn.used == 13
    finally:
        await manager.cancel_all()


@pytest.mark.parametrize("finishes", [True, False])
async def test_absolute_limit_preserves_output_and_persisted_status(tmp_path, finishes):
    target = tmp_path / "evidence.txt"
    target.write_text("fact", encoding="utf-8")
    call = f"[/read]\n@@path={target}\n[read/]"
    script = ["Observed a lead.\n" + call] + [call] * 12
    script.append("Partial: final report." if finishes else call)
    registry = Registry()
    registry.register_tool(ReadTool())
    manager = SubAgentManager(
        registry, ScriptedLLM(script), agent_path=tmp_path, tool_format="bracket"
    )
    manager.register(get_builtin_subagent_config("explore"))
    with closing(SessionStore(str(tmp_path / "run.kohakutr"))) as store:
        manager._session_store = store
        manager._parent_name = "parent"
        try:
            job_id = await manager.spawn(
                "explore", "Inspect evidence.", background=False
            )
            result = manager.get_result(job_id)
            assert result.turns == 14
            assert result.success is finishes
            assert "Observed a lead." in result.output
            assert manager.get_status(job_id).state.value == (
                "done" if finishes else "error"
            )
            meta = store.load_subagent_meta("parent", "explore", 0)
            assert meta["success"] is finishes
            assert store.load_subagent_conversation("parent", "explore", 0)
            if finishes:
                assert "Partial: final report." in result.output
                assert result.error is None
            else:
                assert result.metadata["limit_reached"] == "max_turns"
                assert meta["limit_reached"] == "max_turns"
                assert "turn limit" in result.error
        finally:
            await manager.cancel_all()
