"""Discard a worker-owned recipe graph and its persistent session family."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import kohakuterrarium.terrarium.wiring as wiring
from kohakuterrarium.terrarium import graph_checkpoint
from kohakuterrarium.terrarium.channels import remove_channel_trigger
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.terrarium.engine import Terrarium

logger = get_logger(__name__)


async def discard_recipe_graph(
    engine: Terrarium,
    graph_id: str,
    *,
    before_store_close: Callable[[], None] | None = None,
) -> None:
    """Remove one recipe graph and delete the session files minted for it."""
    graph = engine._topology.graphs.get(graph_id)
    if graph is None:
        raise KeyError(f"graph {graph_id!r} not in engine")
    store = engine._session_stores.get(graph_id)
    if store is None or graph_id not in engine._owned_sessions:
        raise ValueError(f"graph {graph_id!r} has no worker-owned session")

    session_path = Path(store.path)
    creature_ids = sorted(graph.creature_ids)
    remaining_ids = set(creature_ids)
    for creature_id in creature_ids:
        remaining_ids.discard(creature_id)
        creature = engine._creatures.get(creature_id)
        if creature is None:
            continue
        if creature.is_running:
            try:
                await creature.stop(requested=False)
            except BaseException:
                logger.exception(
                    "discard recipe failed to stop creature",
                    extra={"creature_id": creature_id},
                )
        if engine._drive_runtime is not None:
            try:
                await engine._drive_runtime.on_creature_removed(
                    creature_id,
                    graph_id=graph_id,
                    graph_member_ids=frozenset(remaining_ids),
                )
            except BaseException:
                logger.exception(
                    "discard recipe failed to clean Drive assignments",
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
                    "discard recipe failed to remove channel trigger",
                    extra={"creature_id": creature_id},
                )
        engine._creatures.pop(creature_id, None)
        engine._topology.creature_to_graph.pop(creature_id, None)

    engine._topology.graphs.pop(graph_id, None)
    engine._environments.pop(graph_id, None)
    graph_checkpoint.discard(engine, graph_id)
    if engine._drive_runtime is not None:
        engine._drive_runtime.registry.drop_graph(graph_id)
        await engine._drain_drive_topology()
    wiring.install_output_wiring_resolver(engine)

    if before_store_close is not None:
        before_store_close()
    await _close_store(store)
    engine._session_stores.pop(graph_id, None)
    engine._owned_sessions.discard(graph_id)
    engine._recipe_graph_locks.pop(graph_id, None)
    _discard_session_family(session_path)


async def discard_recipe_graph_shielded(
    engine: Terrarium,
    graph_id: str,
    *,
    before_store_close: Callable[[], None] | None = None,
) -> None:
    """Finish worker cleanup even if the request task is cancelled."""
    cleanup = asyncio.create_task(
        discard_recipe_graph(
            engine,
            graph_id,
            before_store_close=before_store_close,
        )
    )
    try:
        await asyncio.shield(cleanup)
    except asyncio.CancelledError:
        await cleanup
        raise


async def _close_store(store) -> None:
    try:
        result = store.close(update_status=False)
    except TypeError:
        result = store.close()
    if inspect.isawaitable(result):
        await result


def _discard_session_family(path: Path) -> None:
    family = [
        path,
        path.with_name(path.name + "-wal"),
        path.with_name(path.name + "-shm"),
        path.with_name(path.name + ".drives"),
        path.with_name(path.name + ".drives-wal"),
        path.with_name(path.name + ".drives-shm"),
        *path.parent.glob(f"{path.name}.drives.split-intent.json*"),
    ]
    detached: list[tuple[Path, Path]] = []
    try:
        for source in family:
            if not source.exists():
                continue
            quarantine = source.with_name(f"{source.name}.discard-{uuid4().hex}")
            source.replace(quarantine)
            detached.append((source, quarantine))
    except OSError:
        for source, quarantine in reversed(detached):
            if quarantine.exists():
                quarantine.replace(source)
        raise

    for original, quarantine in detached:
        try:
            quarantine.unlink()
        except OSError:
            logger.warning(
                "Discarded recipe file remains quarantined",
                original=str(original),
                quarantine=str(quarantine),
            )


__all__ = ["discard_recipe_graph", "discard_recipe_graph_shielded"]
