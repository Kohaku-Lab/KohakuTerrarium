"""Cancellation-safe transaction state for applying one recipe."""

from __future__ import annotations

import asyncio
import copy
import inspect
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from kohakuterrarium.terrarium.channels import remove_channel_trigger
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.terrarium.engine import Terrarium

logger = get_logger(__name__)
_NO_STORE = object()


@dataclass
class RecipeApplyTransaction:
    """Track engine resources owned by one recipe application and roll them back."""

    engine: Terrarium
    created_creature_ids: list[str] = field(default_factory=list)
    created_graph_ids: set[str] = field(default_factory=set)
    created_channels: list[tuple[str, str]] = field(default_factory=list)
    existing_member_edges: dict[str, tuple[list[str], list[str]]] = field(
        default_factory=dict
    )
    existing_graph_id: str | None = None
    existing_session_store: Any = field(default=_NO_STORE, init=False, repr=False)
    existing_session_owned: bool = field(default=False, init=False)
    existing_session_meta: dict[str, Any] = field(default_factory=dict, init=False)
    existing_agent_stores: dict[str, Any] = field(default_factory=dict, init=False)
    _session_replacement_staged: bool = field(default=False, init=False)
    _rolled_back: bool = False

    def record_creature(self, creature_id: str) -> None:
        """Record a creature after the engine has accepted it."""
        self.created_creature_ids.append(creature_id)

    def record_graph(self, graph_id: str) -> None:
        """Record an empty graph created before the first creature was added."""
        self.created_graph_ids.add(graph_id)

    def record_channel(self, graph_id: str, channel_name: str) -> None:
        """Record a channel created in a graph that may predate this apply."""
        self.created_channels.append((graph_id, channel_name))

    def snapshot_existing_members(self, graph_id: str) -> None:
        """Snapshot pre-apply edges for members the transaction does not own."""
        graph = self.engine._topology.graphs[graph_id]
        if graph_id not in self.created_graph_ids:
            self.existing_graph_id = graph_id
            self.existing_session_store = self.engine._session_stores.get(
                graph_id, _NO_STORE
            )
            self.existing_session_owned = graph_id in self.engine._owned_sessions
            if self.existing_session_store is not _NO_STORE and hasattr(
                self.existing_session_store, "meta"
            ):
                meta = self.existing_session_store.meta
                for key in ("agents", "config_type"):
                    self.existing_session_meta[key] = (
                        copy.deepcopy(meta[key]) if key in meta else _NO_STORE
                    )
        for creature_id in graph.creature_ids:
            if creature_id in self.created_creature_ids:
                continue
            creature = self.engine._creatures.get(creature_id)
            if creature is None:
                continue
            self.existing_member_edges[creature_id] = (
                list(creature.listen_channels),
                list(creature.send_channels),
            )
            agent = getattr(creature, "agent", None)
            if agent is not None:
                self.existing_agent_stores[creature_id] = getattr(
                    agent, "session_store", _NO_STORE
                )

    def stage_session_replacement(self, graph_id: str) -> None:
        """Keep the previous store alive until the recipe commits.

        ``Terrarium.attach_session`` normally closes a replaced store
        immediately. A recipe still has a fallible final checkpoint after
        attachment, so temporarily removing the old mapping lets rollback
        restore the same live writer if that checkpoint fails.
        """
        previous = self.existing_session_store
        if (
            self.existing_graph_id != graph_id
            or previous is _NO_STORE
            or self.engine._session_stores.get(graph_id) is not previous
        ):
            return
        self.engine._session_stores.pop(graph_id, None)
        self.engine._owned_sessions.discard(graph_id)
        self._session_replacement_staged = True

    async def commit(self) -> None:
        """Release a replaced store only after the recipe checkpoint succeeds."""
        if not self._session_replacement_staged:
            return
        self._session_replacement_staged = False
        await _close_store(self.existing_session_store, self.existing_graph_id or "")

    async def _remove_from_existing_graph(self) -> None:
        """Remove owned members without invoking split/merge topology logic."""
        graph = self.engine._topology.graphs.get(self.existing_graph_id)
        if graph is None:
            return
        surviving_ids = frozenset(graph.creature_ids).difference(
            self.created_creature_ids
        )
        for creature_id in reversed(self.created_creature_ids):
            creature = self.engine._creatures.get(creature_id)
            if creature is not None:
                try:
                    await creature.stop()
                except BaseException:
                    logger.exception(
                        "recipe rollback failed to stop creature",
                        extra={"creature_id": creature_id},
                    )
                drive_runtime = getattr(self.engine, "_drive_runtime", None)
                if drive_runtime is not None:
                    try:
                        await drive_runtime.on_creature_removed(
                            creature_id,
                            graph_id=self.existing_graph_id,
                            graph_member_ids=surviving_ids,
                        )
                    except BaseException:
                        logger.exception(
                            "recipe rollback failed to clean Drive assignments",
                            extra={"creature_id": creature_id},
                        )
                for channel_name in list(creature.listen_channels):
                    try:
                        remove_channel_trigger(
                            creature.agent,
                            subscriber_id=creature.name,
                            channel_name=channel_name,
                        )
                    except BaseException:
                        logger.exception(
                            "recipe rollback failed to remove trigger",
                            extra={"creature_id": creature_id},
                        )
                self.engine._creatures.pop(creature_id, None)
            graph.creature_ids.discard(creature_id)
            graph.listen_edges.pop(creature_id, None)
            graph.send_edges.pop(creature_id, None)
            self.engine._topology.creature_to_graph.pop(creature_id, None)

    async def _restore_existing_session(self) -> None:
        """Detach a store minted/replaced by the failed recipe application."""
        graph_id = self.existing_graph_id
        if graph_id is None:
            return
        previous = self.existing_session_store
        current = self.engine._session_stores.get(graph_id, _NO_STORE)
        if current is not previous:
            drive_runtime = getattr(self.engine, "_drive_runtime", None)
            if drive_runtime is not None:
                try:
                    await drive_runtime.detach_graph(graph_id)
                except BaseException:
                    logger.exception(
                        "recipe rollback failed to detach Drive store",
                        extra={"graph_id": graph_id},
                    )

            self.engine._session_stores.pop(graph_id, None)
            current_owned = graph_id in self.engine._owned_sessions
            self.engine._owned_sessions.discard(graph_id)
            if current is not _NO_STORE and current_owned:
                await _close_store(current, graph_id)

            if previous is not _NO_STORE:
                self.engine._session_stores[graph_id] = previous
            if self.existing_session_owned:
                self.engine._owned_sessions.add(graph_id)

            if drive_runtime is not None:
                try:
                    if previous is _NO_STORE:
                        drive_runtime.manager_for(graph_id)
                    else:
                        await drive_runtime.bind_graph_store(graph_id, previous)
                except BaseException:
                    logger.exception(
                        "recipe rollback failed to restore Drive store",
                        extra={"graph_id": graph_id},
                    )

        if previous is not _NO_STORE and self.existing_session_meta:
            try:
                meta = previous.meta
                for key, value in self.existing_session_meta.items():
                    if value is _NO_STORE:
                        if key in meta:
                            del meta[key]
                    else:
                        meta[key] = copy.deepcopy(value)
            except BaseException:
                logger.exception(
                    "recipe rollback failed to restore session metadata",
                    extra={"graph_id": graph_id},
                )

        for creature_id, previous_store in self.existing_agent_stores.items():
            creature = self.engine._creatures.get(creature_id)
            agent = getattr(creature, "agent", None) if creature is not None else None
            if agent is None or previous_store is _NO_STORE:
                continue
            try:
                if previous_store is None:
                    _detach_agent_store(agent)
                else:
                    attach = getattr(agent, "attach_session_store", None)
                    if callable(attach):
                        attach(previous_store)
                    else:
                        agent.session_store = previous_store
            except BaseException:
                logger.exception(
                    "recipe rollback failed to restore agent session store",
                    extra={"creature_id": creature_id},
                )

    async def rollback(self) -> None:
        """Remove only resources created by this application, exactly once."""
        if self._rolled_back:
            return
        self._rolled_back = True
        for creature_id, (
            listen_channels,
            send_channels,
        ) in self.existing_member_edges.items():
            creature = self.engine._creatures.get(creature_id)
            if creature is None:
                continue
            creature.listen_channels = list(listen_channels)
            creature.send_channels = list(send_channels)
            graph = self.engine._topology.graphs.get(creature.graph_id)
            if graph is not None:
                graph.listen_edges[creature_id] = set(listen_channels)
                graph.send_edges[creature_id] = set(send_channels)

        if self.existing_graph_id is not None:
            await self._remove_from_existing_graph()
            await self._restore_existing_session()
        else:
            for creature_id in reversed(self.created_creature_ids):
                if creature_id not in self.engine._creatures:
                    continue
                try:
                    await self.engine.remove_creature(creature_id)
                except BaseException:
                    logger.exception(
                        "recipe rollback failed to remove creature",
                        extra={"creature_id": creature_id},
                    )

        for graph_id, channel_name in reversed(self.created_channels):
            graph = self.engine._topology.graphs.get(graph_id)
            if graph is None or channel_name not in graph.channels:
                continue
            try:
                await self.engine.remove_channel(graph_id, channel_name)
            except BaseException:
                logger.exception(
                    "recipe rollback failed to remove channel",
                    extra={"graph_id": graph_id, "channel_name": channel_name},
                )

        for graph_id in self.created_graph_ids:
            graph = self.engine._topology.graphs.get(graph_id)
            if graph is not None and graph.creature_ids:
                continue
            self.engine._topology.graphs.pop(graph_id, None)
            self.engine._environments.pop(graph_id, None)
            store = self.engine._session_stores.pop(graph_id, None)
            if store is not None and graph_id in self.engine._owned_sessions:
                self.engine._owned_sessions.discard(graph_id)
                try:
                    result = store.close()
                    if inspect.isawaitable(result):
                        await result
                except BaseException:
                    logger.exception(
                        "recipe rollback failed to close session store",
                        extra={"graph_id": graph_id},
                    )


async def _close_store(store: Any, graph_id: str) -> None:
    try:
        try:
            result = store.close(update_status=False)
        except TypeError:
            result = store.close()
        if inspect.isawaitable(result):
            await result
    except BaseException:
        logger.exception(
            "recipe rollback failed to close session store",
            extra={"graph_id": graph_id},
        )


def _detach_agent_store(agent: Any) -> None:
    """Undo Agent.attach_session_store without constructing a sink for None."""
    agent.session_store = None
    controller = getattr(agent, "controller", None)
    if controller is not None:
        controller.session_store = None
    session_output = getattr(agent, "_session_output", None)
    if session_output is not None:
        router = getattr(agent, "output_router", None)
        remove = getattr(router, "remove_secondary", None)
        if callable(remove):
            remove(session_output)
        agent._session_output = None
    subagent_manager = getattr(agent, "subagent_manager", None)
    if subagent_manager is not None:
        subagent_manager._session_store = None
    trigger_manager = getattr(agent, "trigger_manager", None)
    if trigger_manager is not None:
        trigger_manager._session_store = None
    compact_manager = getattr(agent, "compact_manager", None)
    if compact_manager is not None:
        compact_manager._session_store = None


async def rollback_shielded(transaction: RecipeApplyTransaction) -> None:
    """Finish rollback even when the calling task is already cancelled."""
    cleanup_task = asyncio.create_task(transaction.rollback())
    try:
        await asyncio.shield(cleanup_task)
    except asyncio.CancelledError:
        await cleanup_task
        raise
