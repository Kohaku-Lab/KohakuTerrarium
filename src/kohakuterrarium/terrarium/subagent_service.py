"""Sub-agent conversation operations across local and remote services."""

from typing import Any, Protocol

from kohakuterrarium.core.conversation.context_snapshot import ContextSnapshot

from kohakuterrarium.terrarium.subagent_conversations import (
    read_subagent_conversation,
    send_to_subagent,
)


class SubagentServiceProtocol(Protocol):
    async def capture_context(self, creature_id: str) -> ContextSnapshot: ...

    async def read_subagent_conversation(
        self,
        creature_id: str,
        *,
        job_id: str | None = None,
        name: str | None = None,
        run: int | None = None,
    ) -> dict[str, Any]: ...

    async def send_to_subagent(
        self,
        creature_id: str,
        name: str,
        content: str,
        *,
        job_id: str | None = None,
    ) -> dict[str, Any]: ...


class LocalSubagentServiceMixin:
    async def capture_context(self, creature_id: str) -> ContextSnapshot:
        return self._engine.capture_context(creature_id)

    async def read_subagent_conversation(
        self, creature_id: str, *, job_id=None, name=None, run=None
    ) -> dict[str, Any]:
        creature = self._engine.get_creature(creature_id)
        return read_subagent_conversation(
            creature.agent,
            creature.graph_id,
            creature.creature_id,
            job_id=job_id,
            name=name,
            run=run,
        )

    async def send_to_subagent(
        self, creature_id: str, name: str, content: str, *, job_id=None
    ) -> dict[str, Any]:
        return await send_to_subagent(
            self._engine.get_creature(creature_id).agent, name, content, job_id=job_id
        )


class RemoteSubagentServiceMixin:
    async def capture_context(self, creature_id: str) -> ContextSnapshot:
        body = await self._checked_req("capture_context", {"creature_id": creature_id})
        return ContextSnapshot.from_dict(body)

    async def read_subagent_conversation(
        self, creature_id: str, *, job_id=None, name=None, run=None
    ) -> dict[str, Any]:
        return await self._checked_req(
            "read_subagent_conversation",
            {"creature_id": creature_id, "job_id": job_id, "name": name, "run": run},
        )

    async def send_to_subagent(
        self, creature_id: str, name: str, content: str, *, job_id=None
    ) -> dict[str, Any]:
        return await self._checked_req(
            "send_to_subagent",
            {
                "creature_id": creature_id,
                "name": name,
                "content": content,
                "job_id": job_id,
            },
        )


class MultiNodeSubagentServiceMixin:
    async def capture_context(self, creature_id: str) -> ContextSnapshot:
        return await self._route_per_creature(
            creature_id, lambda service: service.capture_context(creature_id)
        )

    async def read_subagent_conversation(
        self, creature_id: str, *, job_id=None, name=None, run=None
    ) -> dict[str, Any]:
        return await self._route_per_creature(
            creature_id,
            lambda service: service.read_subagent_conversation(
                creature_id, job_id=job_id, name=name, run=run
            ),
        )

    async def send_to_subagent(
        self, creature_id: str, name: str, content: str, *, job_id=None
    ) -> dict[str, Any]:
        return await self._route_per_creature(
            creature_id,
            lambda service: service.send_to_subagent(
                creature_id, name, content, job_id=job_id
            ),
        )
