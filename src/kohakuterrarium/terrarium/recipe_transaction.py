"""Cancellation-safe transaction state for applying one recipe."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from kohakuterrarium.terrarium.channels import remove_channel_trigger
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.terrarium.engine import Terrarium

logger = get_logger(__name__)


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

    async def _remove_from_existing_graph(self) -> None:
        """Remove owned members without invoking split/merge topology logic."""
        graph = self.engine._topology.graphs.get(self.existing_graph_id)
        if graph is None:
            return
        for creature_id in reversed(self.created_creature_ids):
            creature = self.engine._creatures.pop(creature_id, None)
            if creature is None:
                continue
            try:
                await creature.stop()
            except BaseException:
                logger.exception(
                    "recipe rollback failed to stop creature",
                    extra={"creature_id": creature_id},
                )
            for channel_name in list(creature.listen_channels):
                try:
                    remove_channel_trigger(creature.agent, channel_name)
                except BaseException:
                    logger.exception(
                        "recipe rollback failed to remove trigger",
                        extra={"creature_id": creature_id},
                    )
            graph.creature_ids.discard(creature_id)
            graph.listen_edges.pop(creature_id, None)
            graph.send_edges.pop(creature_id, None)
            self.engine._topology.creature_to_graph.pop(creature_id, None)

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


async def rollback_shielded(transaction: RecipeApplyTransaction) -> None:
    """Finish rollback even when the calling task is already cancelled."""
    cleanup_task = asyncio.create_task(transaction.rollback())
    try:
        await asyncio.shield(cleanup_task)
    except asyncio.CancelledError:
        await cleanup_task
        raise
