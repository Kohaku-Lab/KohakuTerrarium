"""Engine-side identity binding: creature ids reach tools and triggers."""

import pytest

import kohakuterrarium.bootstrap.agent_init as agent_init
import kohakuterrarium.bootstrap.llm as bootstrap_llm
from kohakuterrarium.modules.trigger.timer import TimerTrigger
from kohakuterrarium.terrarium.engine.engine import Terrarium
from kohakuterrarium.testing.llm import ScriptedLLM

_CONFIG = """\
name: watcher
input:
  type: none
output:
  type: stdout
triggers:
  - type: timer
    name: tick
    prompt: tick
    options:
      interval: 3600
"""


@pytest.fixture
def creature_dir(tmp_path, monkeypatch):
    def scripted(config, llm=None):
        return ScriptedLLM(["ok"])

    monkeypatch.setattr(agent_init, "create_llm_provider", scripted)
    monkeypatch.setattr(bootstrap_llm, "create_llm_provider", scripted)
    (tmp_path / "config.yaml").write_text(_CONFIG, encoding="utf-8")
    return str(tmp_path)


async def test_triggers_learn_the_engine_creature_id(creature_dir):
    engine = Terrarium()
    try:
        first = await engine.add_creature(creature_dir)
        second = await engine.add_creature(creature_dir)
        assert first.creature_id != second.creature_id
        for creature in (first, second):
            tick = creature.agent.trigger_manager.get_trigger("tick")
            assert tick.host_creature_id == creature.creature_id
            assert tick.host_agent_name == "watcher"
            assert tick.host_agent is creature.agent

        runtime = TimerTrigger(interval=3600, prompt="late")
        await first.agent.add_trigger(runtime, trigger_id="late")
        assert runtime.host_creature_id == first.creature_id

        await first.agent._restore_triggers(
            [
                {
                    "trigger_id": "restored",
                    "type": "TimerTrigger",
                    "module": "kohakuterrarium.modules.trigger.timer",
                    "data": {"interval": 3600, "prompt": "again"},
                }
            ]
        )
        restored = first.agent.trigger_manager.get_trigger("restored")
        assert restored.host_creature_id == first.creature_id
        assert first.agent.executor._creature_id == first.creature_id
    finally:
        await engine.shutdown()
