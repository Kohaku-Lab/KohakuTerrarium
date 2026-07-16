"""Per-creature runtime-option delegates for the multi-node service.

Pure home-node routing: every method resolves the creature's worker via
``_route_per_creature`` (provided by ``MultiNodeTerrariumService``) and
forwards the call verbatim.
"""

from typing import Any


class MultiNodeRuntimeOptionsMixin:
    """Route per-creature runtime-option reads and writes to the home worker."""

    async def get_scratchpad(self, creature_id: str) -> dict[str, str]:
        return await self._route_per_creature(
            creature_id, lambda svc: svc.get_scratchpad(creature_id)
        )

    async def patch_scratchpad(
        self,
        creature_id: str,
        updates: dict[str, str | None],
    ) -> dict[str, str]:
        return await self._route_per_creature(
            creature_id, lambda svc: svc.patch_scratchpad(creature_id, updates)
        )

    async def list_triggers(self, creature_id: str) -> list[dict[str, Any]]:
        return await self._route_per_creature(
            creature_id, lambda svc: svc.list_triggers(creature_id)
        )

    async def get_env(self, creature_id: str) -> dict[str, Any]:
        return await self._route_per_creature(
            creature_id, lambda svc: svc.get_env(creature_id)
        )

    async def get_system_prompt(self, creature_id: str) -> dict[str, str]:
        return await self._route_per_creature(
            creature_id, lambda svc: svc.get_system_prompt(creature_id)
        )

    async def get_working_dir(self, creature_id: str) -> str:
        return await self._route_per_creature(
            creature_id, lambda svc: svc.get_working_dir(creature_id)
        )

    async def set_working_dir(self, creature_id: str, new_path: str) -> str:
        return await self._route_per_creature(
            creature_id, lambda svc: svc.set_working_dir(creature_id, new_path)
        )

    async def native_tool_inventory(self, creature_id: str) -> list[dict[str, Any]]:
        return await self._route_per_creature(
            creature_id, lambda svc: svc.native_tool_inventory(creature_id)
        )

    async def get_native_tool_options(
        self, creature_id: str
    ) -> dict[str, dict[str, Any]]:
        return await self._route_per_creature(
            creature_id, lambda svc: svc.get_native_tool_options(creature_id)
        )

    async def set_native_tool_options(
        self,
        creature_id: str,
        tool: str,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._route_per_creature(
            creature_id,
            lambda svc: svc.set_native_tool_options(creature_id, tool, values),
        )

    async def switch_model(self, creature_id: str, model: str) -> str:
        return await self._route_per_creature(
            creature_id, lambda svc: svc.switch_model(creature_id, model)
        )

    async def list_plugins(self, creature_id: str) -> list[dict[str, Any]]:
        return await self._route_per_creature(
            creature_id, lambda svc: svc.list_plugins(creature_id)
        )

    async def toggle_plugin(
        self,
        creature_id: str,
        plugin_name: str,
        enabled: bool,
    ) -> dict[str, Any]:
        return await self._route_per_creature(
            creature_id,
            lambda svc: svc.toggle_plugin(creature_id, plugin_name, enabled),
        )

    async def list_modules(self, creature_id: str) -> list[dict[str, Any]]:
        return await self._route_per_creature(
            creature_id, lambda svc: svc.list_modules(creature_id)
        )

    async def get_module_options(
        self,
        creature_id: str,
        module_type: str,
        module_name: str,
    ) -> dict[str, Any]:
        return await self._route_per_creature(
            creature_id,
            lambda svc: svc.get_module_options(creature_id, module_type, module_name),
        )

    async def set_module_options(
        self,
        creature_id: str,
        module_type: str,
        module_name: str,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._route_per_creature(
            creature_id,
            lambda svc: svc.set_module_options(
                creature_id, module_type, module_name, values
            ),
        )

    async def toggle_module(
        self,
        creature_id: str,
        module_type: str,
        module_name: str,
    ) -> dict[str, Any]:
        return await self._route_per_creature(
            creature_id,
            lambda svc: svc.toggle_module(creature_id, module_type, module_name),
        )
