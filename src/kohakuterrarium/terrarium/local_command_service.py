"""Local command-service methods kept outside the main service facade."""

from typing import Any

from kohakuterrarium.terrarium.creature_ops import (
    agent_command_inventory,
    agent_execute_command,
    agent_invoke_skill,
    normalize_command_args,
)


class LocalCommandServiceMixin:
    """Command operations for the in-process Terrarium service."""

    async def command_inventory(self, creature_id: str) -> dict[str, Any]:
        return agent_command_inventory(self._agent(creature_id))

    async def invoke_skill(
        self,
        creature_id: str,
        skill: str,
        args: str | dict[str, Any] | None = None,
        *,
        source: str = "web:skill",
    ) -> dict[str, Any]:
        return await agent_invoke_skill(
            self._agent(creature_id),
            skill,
            args,
            source=source,
        )

    async def execute_command(
        self,
        creature_id: str,
        command: str,
        args: str | dict[str, Any] | None = None,
        *,
        principal: str = "user:local",
        is_operator: bool = False,
    ) -> dict[str, Any]:
        return await agent_execute_command(
            self._agent(creature_id),
            command,
            normalize_command_args(args),
            service=self,
            engine=self._engine,
            creature_id=creature_id,
            principal=principal,
            is_operator=is_operator,
        )
