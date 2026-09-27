"""Execution adapters against real Creature and subagent owners."""

import json

import pytest

from kohakuterrarium.core.job import JobState
from kohakuterrarium.mcp_server.config import MCPToolsConfig
from kohakuterrarium.mcp_server.delegation_adapters import DelegationAdapters
from kohakuterrarium.mcp_server.delegation_history import DelegationHistory
from kohakuterrarium.testing.llm import ScriptedLLM


@pytest.mark.parametrize("kind", ["creature", "subagent"])
async def test_execution_results_history_and_resource_lifecycle(tmp_path, kind):
    target = tmp_path / "worker.json"
    data = {"name": "worker", "tools": []}
    if kind == "creature":
        data.update(
            input={"type": "none"}, output={"type": "none"}, compact={"enabled": False}
        )
    target.write_text(json.dumps(data), encoding="utf-8")
    config = MCPToolsConfig(
        workspace=tmp_path,
        tools=[],
        delegation={"worker": {"kind": kind, "config": str(target)}},
    )
    models = []

    class Provider(ScriptedLLM):
        released = False

        async def close(self):
            self.released = True

    def provider(_):
        model = Provider(["execution reply"])
        models.append(model)
        return model

    owner = DelegationAdapters(config, "test-adapters", llm_factory=provider)
    execution = owner.create("worker", DelegationHistory())
    try:
        result = await execution.run("first", "task")
        assert result.state == JobState.DONE and result.output == "execution reply"
        assert result.error is None
        assert "execution reply" in str(execution.conversation())
        assert not await execution.send("too late")
        if kind == "creature":
            assert result.metadata["status"] == "ok"
            assert execution.state == "idle" and not models[0].released
            engine = await owner.engine()
            creature = engine.list_creatures()[0]
            identity = creature.creature_id, creature.graph_id
            creature.pause()
            assert execution.state == "paused"
            creature.resume()
            assert execution.state == "idle"
            await creature.stop()
            assert execution.state == "stopped"
            await execution.stop()
            result = await execution.run("second", "continue")
            assert result.output == "execution reply" and result.state == JobState.DONE
            assert len(models) == 2
            assert "task" in str(models[1].call_log)
            restored = engine.list_creatures()[0]
            assert (restored.creature_id, restored.graph_id) == identity
        else:
            assert result.metadata["turns"] == 1
            assert execution.state == "completed" and models[0].released
        await execution.close()
        await execution.close()
        assert all(model.released for model in models)
    finally:
        await execution.close()
        await owner.close()
