"""Remote recipe operations for :class:`RemoteTerrariumService`."""

from typing import Any

from kohakuterrarium.terrarium.service import CreatureInfo
from kohakuterrarium.terrarium.topology import GraphTopology
from kohakuterrarium.terrarium.wire import (
    unpack_creature_info,
    unpack_graph_topology,
)


class RemoteRecipeServiceMixin:
    """Proxy whole-recipe application to one remote worker node."""

    async def _checked_req(
        self, type_: str, body: dict[str, Any]
    ) -> dict[str, Any]: ...

    async def apply_recipe(
        self,
        recipe_path: str,
        *,
        pwd: str | None = None,
        llm: Any = None,
        strict: bool = True,
        start: bool = True,
        persist: bool = False,
    ) -> tuple[GraphTopology, list[CreatureInfo]]:
        """Apply one complete recipe on this worker without cross-node splitting.

        Persistence is worker-owned so display names and controller-provided
        paths can never select the session file.
        """
        body = await self._checked_req(
            "apply_recipe",
            {
                "recipe_path": recipe_path,
                "pwd": pwd,
                "llm": llm,
                "strict": strict,
                "start": start,
                "persist": persist,
            },
        )
        graph = unpack_graph_topology(body["graph"])
        creatures = [unpack_creature_info(item) for item in body["creatures"]]
        return graph, creatures

    async def discard_recipe(self, graph_id: str) -> None:
        """Discard a just-applied recipe and its worker-owned persistence."""
        await self._checked_req("discard_recipe", {"graph_id": graph_id})
