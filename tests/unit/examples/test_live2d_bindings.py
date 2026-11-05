"""Bindings preserve configured tools and skill state across UI ownership."""

import asyncio

import pytest
from fastapi import HTTPException

from examples.packages.live2d_pet.kt_live2d.api import Binding, bind, events
from examples.packages.live2d_pet.kt_live2d.bindings import (
    SKILL_NAME,
    SKILL_PATH,
    bindings,
    remove_binding,
)
from examples.packages.live2d_pet.kt_live2d.control import Live2DControl
from kohakuterrarium import Terrarium
from kohakuterrarium.core.config.types import AgentConfig
from kohakuterrarium.skills.discovery import load_skill_from_path
from kohakuterrarium.skills.registry import SCRATCHPAD_ENABLED_KEY
from kohakuterrarium.testing.llm import ScriptedLLM
from kohakuterrarium.terrarium.service.service import LocalTerrariumService


async def test_configured_tool_and_skill_survive_binding_and_expiry():
    engine = Terrarium()
    old_poll = None
    try:
        creature = await engine.add_creature(
            AgentConfig(name="companion"),
            llm=ScriptedLLM(["ok"]),
            start=False,
            io="headless",
        )
        agent = creature.agent
        tool = Live2DControl(purpose="companion")
        agent.add_tool(tool)
        skill = load_skill_from_path(SKILL_PATH, origin="creature")
        agent.skills.add(skill)
        skill.enabled = False
        original = agent.scratchpad.get(SCRATCHPAD_ENABLED_KEY)
        service = LocalTerrariumService(engine)
        req = Binding(
            lease="first",
            enabled=True,
            mode="agent",
            sessionId=creature.graph_id,
            creatureId=creature.creature_id,
        )
        await bind(req, service)
        assert agent.skills.get(SKILL_NAME) is skill and skill.enabled
        assert agent.scratchpad.get(SCRATCHPAD_ENABLED_KEY) == original
        old_poll = asyncio.create_task(events("first", service))
        await asyncio.sleep(0)
        old_queue = tool.events
        remove_binding(engine)
        await bind(req.model_copy(update={"lease": "second"}), service)
        assert old_queue is not tool.events
        assert agent.registry.get_tool("l2d") is tool
        assert not (await tool.execute({"operation": "perform", "action": "nod"})).error
        assert (await asyncio.wait_for(old_poll, 1))["command"] is None
        assert (await events("second", service))["command"]["action"] == "nod"
        timer = bindings[engine].expiry
        remove_binding(engine)
        assert timer.cancelled()
        assert agent.registry.get_tool("l2d") is tool
        assert agent.skills.get(SKILL_NAME) is skill and not skill.enabled
        assert agent.scratchpad.get(SCRATCHPAD_ENABLED_KEY) == original
        assert (await tool.execute({"operation": "actions"})).error
        skill.enabled = True
        await bind(req, service)
        remove_binding(engine)
        assert skill.enabled
        assert agent.llm.call_count == 0
    finally:
        if old_poll and not old_poll.done():
            old_poll.cancel()
            with pytest.raises(asyncio.CancelledError):
                await old_poll
        remove_binding(engine)
        await engine.shutdown()


async def test_binding_cycle_preserves_graph_context_and_rejects_foreign_renewals():
    engine = Terrarium()
    try:
        a = await engine.add_creature(
            AgentConfig(name="a"), llm=ScriptedLLM([]), start=False, is_privileged=True
        )
        b = await engine.add_creature(
            AgentConfig(name="b"), llm=ScriptedLLM([]), start=False
        )
        await engine.connect(a, b, channel="tasks")
        await engine.wire_output(a, b.creature_id)
        await engine._runtime_prompt.refresh_creature(a)
        before = a.agent.controller.conversation.get_system_message().content
        graph = before[before.index("<!-- runtime-graph -->") :]
        service = LocalTerrariumService(engine)
        request = Binding(
            lease="a", enabled=True, sessionId=a.graph_id, creatureId=a.creature_id
        )
        await bind(request, service)
        assert graph in a.agent.controller.conversation.get_system_message().content
        other = Binding(
            lease="b", enabled=True, sessionId=b.graph_id, creatureId=b.creature_id
        )
        with pytest.raises(HTTPException, match="controlled"):
            await bind(other, service)
        assert b.agent.registry.get_tool("l2d") is None
        tool = a.agent.registry.get_tool("l2d")
        await bind(request.model_copy(update={"renew": True}), service)
        assert a.agent.registry.get_tool("l2d") is tool
        remove_binding(engine)
        assert graph in a.agent.controller.conversation.get_system_message().content
        await bind(other, service)
        with pytest.raises(HTTPException, match="controlled"):
            await bind(request, service)
        assert a.agent.registry.get_tool("l2d") is None
        remove_binding(engine)
        await bind(request, service)
        timer = bindings[engine].expiry
        timer._callback(*timer._args)
        assert engine not in bindings
        with pytest.raises(HTTPException, match="expired"):
            await bind(request.model_copy(update={"renew": True}), service)
        assert graph in a.agent.controller.conversation.get_system_message().content
    finally:
        remove_binding(engine)
        await engine.shutdown()
