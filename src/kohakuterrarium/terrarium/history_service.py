"""History paging service protocol and local channel reads."""

from typing import Protocol

from kohakuterrarium.session.history_paging import page_channels, physical_refs
from kohakuterrarium.session.history_records import history_detail, history_page
from kohakuterrarium.terrarium.creature_ops import agent_live_job_ids


class HistoryServiceProtocol(Protocol):
    async def chat_history_page(self, creature_id: str, **kwargs) -> dict: ...

    async def chat_history_detail(self, creature_id: str, **kwargs) -> dict: ...

    async def channel_history_page(
        self, graph_id: str, name: str, **kwargs
    ) -> dict: ...

    async def channel_history_detail(
        self, graph_id: str, name: str, **kwargs
    ) -> dict: ...


class LocalHistoryServiceMixin:
    async def chat_history_page(self, creature_id: str, **kwargs) -> dict:
        creature, store = self._history_source(creature_id)
        return history_page(
            store,
            creature.name,
            session_id=creature.graph_id,
            snapshot=getattr(creature.agent, "conversation_history", None),
            is_processing=bool(creature.agent.is_processing),
            live_job_ids=tuple(sorted(agent_live_job_ids(creature.agent))),
            envelope={"creature_id": creature_id, "session_id": creature.graph_id},
            **kwargs,
        )

    async def chat_history_detail(self, creature_id: str, **kwargs) -> dict:
        creature, store = self._history_source(creature_id)
        return history_detail(
            store,
            creature.name,
            session_id=creature.graph_id,
            snapshot=getattr(creature.agent, "conversation_history", None),
            **kwargs,
        )

    def _history_source(self, creature_id: str):
        creature = self._engine.get_creature(creature_id)
        store = getattr(
            creature.agent, "session_store", None
        ) or self._engine._session_stores.get(creature.graph_id)
        if store is None:
            raise KeyError(creature_id)
        return creature, store

    async def channel_history_page(self, graph_id: str, name: str, **kwargs) -> dict:
        envelope = {"session_id": graph_id, "creature_id": f"ch:{name}"}
        store = self._engine._session_stores.get(graph_id)
        if store is not None:
            envs = getattr(self._engine, "_environments", None) or {}
            env = envs.get(graph_id)
            live = env is not None and env.shared_channels.get(name) is not None
            if not live and not physical_refs(store.channels, name, "m"):
                raise KeyError(f"channel {name!r} not in graph {graph_id!r}")
            return history_page(
                store, f"ch:{name}", session_id=graph_id, envelope=envelope, **kwargs
            )
        messages = await self.channel_history(graph_id, name)
        return channel_list_page(messages, graph_id, name, **kwargs)

    async def channel_history_detail(self, graph_id: str, name: str, **kwargs) -> dict:
        store = self._engine._session_stores.get(graph_id)
        messages = (
            None if store is not None else await self.channel_history(graph_id, name)
        )
        return history_detail(
            store,
            f"ch:{name}",
            session_id=graph_id,
            channel_messages=messages,
            **kwargs,
        )


def channel_list_page(messages: list, graph_id: str, name: str, **kwargs) -> dict:
    """Build a bounded response from an already materialized channel list."""
    envelope = {"session_id": graph_id, "creature_id": f"ch:{name}"}
    page = page_channels(
        messages, session_id=graph_id, channel=name, envelope=envelope, **kwargs
    )
    return {**envelope, "events": [], "messages": page.pop("items"), **page}
