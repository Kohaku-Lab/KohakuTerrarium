"""UI ownership is explicit and never starts model work or revives stale bindings."""

import asyncio

import pytest
from fastapi import HTTPException

from examples.packages.live2d_pet.kt_live2d.api import (
    Binding,
    ControllerRequest,
    bind,
    update_controller,
)
from examples.packages.live2d_pet.kt_live2d.bindings import bindings
from examples.packages.live2d_pet.kt_live2d.ownership import (
    claim_controller,
    controller_view,
    controllers,
    release_controller,
)
from kohakuterrarium import Terrarium
from kohakuterrarium.core.config.types import AgentConfig
from kohakuterrarium.testing.llm import ScriptedLLM
from kohakuterrarium.terrarium.service.service import LocalTerrariumService


async def test_controller_transfer_expiry_and_stale_release_preserve_agent_work():
    async with Terrarium() as engine:
        creature = await engine.add_creature(
            AgentConfig(name="worker"), llm=ScriptedLLM([]), start=False
        )
        service = LocalTerrariumService(engine)
        first = claim_controller(engine, "first", "claim")
        assert creature.agent.registry.get_tool("l2d") is None
        request = Binding(
            controller=first["token"],
            lease="gesture-a",
            enabled=True,
            sessionId=creature.graph_id,
            creatureId=creature.creature_id,
        )
        await bind(request, service)
        original_tool = creature.agent.registry.get_tool("l2d")
        for operation in ("claim", "renew", "takeover"):
            with pytest.raises(HTTPException) as error:
                await update_controller(
                    ControllerRequest(client="second", operation=operation), service
                )
            assert error.value.status_code == 409
        second = claim_controller(engine, "second", "takeover", first["token"])
        assert engine not in bindings
        assert creature.agent.registry.get_tool("l2d") is None
        assert (
            await original_tool.execute({"operation": "perform", "action": "nod"})
        ).error
        with pytest.raises(HTTPException, match="Controller changed"):
            await bind(request, service)
        claim_controller(engine, "first", "release", first["token"])
        assert controller_view(engine) == second
        claim_controller(engine, "second", "renew", second["token"])
        timer = controllers[engine].expiry
        timer._callback(*timer._args)
        assert controller_view(engine) is None
        with pytest.raises(ValueError, match="expired"):
            claim_controller(engine, "second", "renew", second["token"])
        assert creature.agent.llm.call_count == 0


async def test_inflight_resolution_cannot_bind_after_controller_transfer():
    entered, resume = asyncio.Event(), asyncio.Event()

    class DelayedService(LocalTerrariumService):
        async def list_creatures(self):
            entered.set()
            await resume.wait()
            return await super().list_creatures()

    async with Terrarium() as engine:
        creature = await engine.add_creature(
            AgentConfig(name="worker"), llm=ScriptedLLM([]), start=False
        )
        first = claim_controller(engine, "first", "claim")
        task = asyncio.create_task(
            bind(
                Binding(
                    controller=first["token"],
                    lease="old",
                    enabled=True,
                    sessionId=creature.graph_id,
                    creatureId=creature.creature_id,
                ),
                DelayedService(engine),
            )
        )
        try:
            await entered.wait()
            second = claim_controller(engine, "second", "takeover", first["token"])
            resume.set()
            with pytest.raises(HTTPException, match="Controller changed"):
                await task
            assert engine not in bindings
            assert creature.agent.registry.get_tool("l2d") is None
        finally:
            resume.set()
            await asyncio.gather(task, return_exceptions=True)
            release_controller(engine, second["token"])
