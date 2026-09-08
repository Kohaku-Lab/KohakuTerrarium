"""Remote and multi-node history paging delegation."""

from kohakuterrarium.session.history_records import history_detail
from kohakuterrarium.terrarium.history_service import channel_list_page


class RemoteHistoryServiceMixin:
    async def chat_history_page(self, creature_id: str, **kwargs) -> dict:
        return await self._history_request(
            "chat_history_page", {"creature_id": creature_id, **kwargs}
        )

    async def chat_history_detail(self, creature_id: str, **kwargs) -> dict:
        return await self._history_request(
            "chat_history_detail", {"creature_id": creature_id, **kwargs}
        )

    async def channel_history_page(self, graph_id: str, name: str, **kwargs) -> dict:
        return await self._history_request(
            "channel_history_page", {"graph_id": graph_id, "name": name, **kwargs}
        )

    async def channel_history_detail(self, graph_id: str, name: str, **kwargs) -> dict:
        return await self._history_request(
            "channel_history_detail", {"graph_id": graph_id, "name": name, **kwargs}
        )


class MultiNodeHistoryServiceMixin:
    async def chat_history_detail(self, creature_id: str, **kwargs) -> dict:
        return await self._route_per_creature(
            creature_id, lambda svc: svc.chat_history_detail(creature_id, **kwargs)
        )

    async def channel_history_page(self, graph_id: str, name: str, **kwargs) -> dict:
        messages = await self.channel_history(graph_id, name)
        return channel_list_page(messages, graph_id, name, **kwargs)

    async def channel_history_detail(self, graph_id: str, name: str, **kwargs) -> dict:
        messages = await self.channel_history(graph_id, name)
        return history_detail(
            None, f"ch:{name}", session_id=graph_id, channel_messages=messages, **kwargs
        )
