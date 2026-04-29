"""Legacy AgentSession compatibility wrapper."""

from collections.abc import AsyncIterator
from uuid import uuid4

from kohakuterrarium.builtins.inputs.none import NoneInput
from kohakuterrarium.core.agent import Agent


class AgentSession:
    """Small compatibility facade around a standalone :class:`Agent`."""

    def __init__(self, agent: Agent, agent_id: str | None = None) -> None:
        self.agent = agent
        self.agent_id = agent_id or f"agent_{uuid4().hex[:8]}"
        self._running = False

    @classmethod
    async def from_path(cls, config_path: str) -> "AgentSession":
        session = cls(Agent.from_path(config_path, input_module=NoneInput()))
        await session.start()
        return session

    async def start(self) -> None:
        if self._running:
            return
        await self.agent.start()
        self._running = True

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        await self.agent.stop()

    async def chat(self, message: str | list[dict]) -> AsyncIterator[str]:
        chunks: list[str] = []
        self.agent.set_output_handler(chunks.append)
        await self.agent.inject_input(message, source="chat")
        for chunk in chunks:
            yield chunk

    def get_status(self) -> dict:
        model = getattr(self.agent.llm, "model", "") or getattr(
            getattr(self.agent.llm, "config", None), "model", ""
        )
        return {
            "agent_id": self.agent_id,
            "name": self.agent.config.name,
            "running": self._running and self.agent.is_running,
            "model": model,
            "tools": self.agent.tools,
            "subagents": self.agent.subagents,
            "pwd": str(getattr(self.agent.executor, "_working_dir", "")),
        }


__all__ = ["AgentSession"]
