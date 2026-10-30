"""Unit tests for :func:`kohakuterrarium.terrarium.host_resources_allowed`."""

import gc
import weakref
from pathlib import Path
from types import SimpleNamespace

from kohakuterrarium.core.environment import Environment
from kohakuterrarium.core.events import TriggerEvent
from kohakuterrarium.modules.plugin.base import PluginContext
from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.modules.trigger.base import BaseTrigger
from kohakuterrarium.terrarium import Terrarium, host_resources_allowed
from kohakuterrarium.terrarium.channels.channels import register_engine_handle
from kohakuterrarium.terrarium.creature.host import Creature
from kohakuterrarium.testing.terrarium import _FakeAgent


class _Idle(BaseTrigger):
    async def wait_for_trigger(self) -> TriggerEvent | None:
        return None


class _Agent:
    def __init__(self, environment):
        self.environment = environment
        self.config = SimpleNamespace(name="a")


def _env_for(engine) -> Environment:
    env = Environment(env_id="env_test")
    register_engine_handle(env, engine)
    return env


def test_solo_sources_are_allowed():
    assert host_resources_allowed(None) is True
    assert host_resources_allowed(ToolContext("a", None, Path("."))) is True
    assert host_resources_allowed(PluginContext(agent_name="a")) is True
    assert host_resources_allowed(_Idle()) is True


def test_every_source_kind_reads_the_engine_flag():
    engine = Terrarium(host_resources=False)
    env = _env_for(engine)
    agent = _Agent(env)
    trigger = _Idle()
    trigger.bind_host("a_1", "a", agent)
    sources = [
        ToolContext("a", None, Path("."), environment=env),
        PluginContext(agent_name="a", _host_agent=agent),
        trigger,
        agent,
        SimpleNamespace(agent=agent),
    ]
    assert [host_resources_allowed(s) for s in sources] == [False] * 5
    engine.set_host_resources(True)
    assert [host_resources_allowed(s) for s in sources] == [True] * 5


def test_engine_default_is_allowed_and_flag_is_a_bool():
    engine = Terrarium()
    assert engine.host_resources is True
    engine.set_host_resources(0)
    assert engine.host_resources is False


def test_a_trigger_whose_agent_is_gone_counts_as_solo():
    engine = Terrarium(host_resources=False)
    agent = _Agent(_env_for(engine))
    trigger = _Idle()
    trigger.bind_host("a_1", "a", agent)
    assert host_resources_allowed(trigger) is False
    del agent
    gc.collect()
    assert trigger.host_agent is None
    assert host_resources_allowed(trigger) is True


def test_a_collected_engine_counts_as_solo():
    env = Environment(env_id="env_gone")
    engine = Terrarium(host_resources=False)
    env.register("terrarium_engine", weakref.ref(engine))
    del engine
    gc.collect()
    assert host_resources_allowed(ToolContext("a", None, Path("."), environment=env))


async def test_creatures_in_an_engine_see_its_flag():
    engine = Terrarium(host_resources=False)
    try:
        creature = await engine.add_creature(
            Creature(creature_id="c", name="c", agent=_FakeAgent(name="c"))
        )
        assert host_resources_allowed(creature.agent) is False
        assert host_resources_allowed(creature) is False
    finally:
        await engine.shutdown()
