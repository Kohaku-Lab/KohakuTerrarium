"""Legacy KohakuManager compatibility facade."""

from uuid import uuid4

from kohakuterrarium.core.channel import ChannelMessage
from kohakuterrarium.serving.agent_session import AgentSession
from kohakuterrarium.terrarium.config import CreatureConfig, load_terrarium_config
from kohakuterrarium.terrarium.runtime import TerrariumRuntime


class KohakuManager:
    """Manage standalone agent sessions and terrarium runtimes."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentSession] = {}
        self._terrariums: dict[str, TerrariumRuntime] = {}

    async def shutdown(self) -> None:
        for agent_id in list(self._agents):
            await self.agent_stop(agent_id)
        for terrarium_id in list(self._terrariums):
            await self.terrarium_stop(terrarium_id)

    async def agent_create(self, config_path: str) -> str:
        session = await AgentSession.from_path(config_path)
        self._agents[session.agent_id] = session
        return session.agent_id

    async def agent_stop(self, agent_id: str) -> None:
        session = self._agents.pop(agent_id, None)
        if session is not None:
            await session.stop()

    def agent_list(self) -> list[dict]:
        return [session.get_status() for session in self._agents.values()]

    def agent_status(self, agent_id: str) -> dict | None:
        session = self._agents.get(agent_id)
        return session.get_status() if session is not None else None

    async def terrarium_create(self, config_path: str) -> str:
        config = load_terrarium_config(config_path)
        runtime = TerrariumRuntime(config)
        await runtime.start()
        terrarium_id = f"terrarium_{uuid4().hex[:8]}"
        self._terrariums[terrarium_id] = runtime
        return terrarium_id

    async def terrarium_stop(self, terrarium_id: str) -> None:
        runtime = self._terrariums.pop(terrarium_id, None)
        if runtime is not None:
            await self._stop_runtime_now(runtime)

    def terrarium_list(self) -> list[dict]:
        return [
            {"terrarium_id": tid, **runtime.get_status()}
            for tid, runtime in self._terrariums.items()
        ]

    def terrarium_status(self, terrarium_id: str) -> dict | None:
        runtime = self._terrariums.get(terrarium_id)
        if runtime is None:
            return None
        return {"terrarium_id": terrarium_id, **runtime.get_status()}

    async def terrarium_channel_add(
        self,
        terrarium_id: str,
        *,
        name: str,
        channel_type: str = "queue",
        description: str = "",
    ) -> None:
        runtime = self._require_terrarium(terrarium_id)
        await runtime.add_channel(
            name, channel_type=channel_type, description=description
        )

    async def creature_add(self, terrarium_id: str, *, config: CreatureConfig) -> str:
        runtime = self._require_terrarium(terrarium_id)
        handle = await runtime.add_creature(config)
        return handle.name

    async def terrarium_channel_send(
        self,
        terrarium_id: str,
        *,
        channel: str,
        content: str,
        sender: str = "human",
    ) -> str:
        runtime = self._require_terrarium(terrarium_id)
        session = runtime._session
        if session is None:
            raise ValueError("Terrarium is not running")
        target = session.channels.get(channel)
        if target is None:
            raise ValueError(f"Channel not found: {channel}")
        msg = ChannelMessage(sender=sender, content=content)
        await target.send(msg)
        return msg.message_id

    async def _stop_runtime_now(self, runtime: TerrariumRuntime) -> None:
        runtime._running = False
        for handle in runtime._creatures.values():
            agent = handle.agent
            agent.interrupt()
            agent._running = False
            for trigger in agent.trigger_manager._triggers.values():
                await trigger.stop()
            for task in agent.trigger_manager._tasks.values():
                task.cancel()
            agent.trigger_manager._tasks.clear()
            agent.trigger_manager._triggers.clear()
            agent.trigger_manager._created_at.clear()
        runtime._creature_tasks.clear()

    def _require_terrarium(self, terrarium_id: str) -> TerrariumRuntime:
        runtime = self._terrariums.get(terrarium_id)
        if runtime is None:
            raise KeyError(f"Terrarium not found: {terrarium_id}")
        return runtime


__all__ = ["KohakuManager"]
