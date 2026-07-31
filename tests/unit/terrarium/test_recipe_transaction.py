"""Unit tests for recipe apply transaction rollback."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from kohakuterrarium.terrarium.recipe_transaction import (
    RecipeApplyTransaction,
    rollback_shielded,
)


class _Engine:
    def __init__(self, creature_ids: list[str]) -> None:
        self._creatures = {creature_id: object() for creature_id in creature_ids}
        self.removed: list[str] = []
        self.remove_started = asyncio.Event()
        self.allow_remove = asyncio.Event()
        self.block_remove = False

    async def remove_creature(self, creature_id: str) -> None:
        self.remove_started.set()
        if self.block_remove:
            await self.allow_remove.wait()
        self.removed.append(creature_id)
        self._creatures.pop(creature_id)


@pytest.mark.asyncio
async def test_recipe_transaction_removes_only_owned_resources_in_reverse_order() -> (
    None
):
    engine = _Engine(["existing", "new-a", "new-b"])
    transaction = RecipeApplyTransaction(engine)  # type: ignore[arg-type]
    transaction.record_creature("new-a")
    transaction.record_creature("new-b")

    await transaction.rollback()

    assert engine.removed == ["new-b", "new-a"]
    assert set(engine._creatures) == {"existing"}


@pytest.mark.asyncio
async def test_recipe_transaction_rollback_is_idempotent() -> None:
    engine = _Engine(["new-a"])
    transaction = RecipeApplyTransaction(engine)  # type: ignore[arg-type]
    transaction.record_creature("new-a")

    await transaction.rollback()
    await transaction.rollback()

    assert engine.removed == ["new-a"]


@pytest.mark.asyncio
async def test_recipe_transaction_skips_owned_resource_removed_elsewhere() -> None:
    engine = _Engine(["existing", "new-a"])
    transaction = RecipeApplyTransaction(engine)  # type: ignore[arg-type]
    transaction.record_creature("new-a")
    transaction.record_creature("missing")

    await transaction.rollback()

    assert engine.removed == ["new-a"]
    assert set(engine._creatures) == {"existing"}


@pytest.mark.asyncio
async def test_recipe_transaction_rollback_survives_caller_cancellation() -> None:
    engine = _Engine(["new-a"])
    engine.block_remove = True
    transaction = RecipeApplyTransaction(engine)  # type: ignore[arg-type]
    transaction.record_creature("new-a")

    rollback_task = asyncio.create_task(rollback_shielded(transaction))
    await engine.remove_started.wait()
    rollback_task.cancel()
    engine.allow_remove.set()

    with pytest.raises(asyncio.CancelledError):
        await rollback_task

    assert engine.removed == ["new-a"]
    assert engine._creatures == {}


@pytest.mark.asyncio
async def test_existing_graph_rollback_cleans_drive_assignments() -> None:
    class _Creature:
        def __init__(self, creature_id: str) -> None:
            self.creature_id = creature_id
            self.graph_id = "g"
            self.name = creature_id
            self.listen_channels = []
            self.send_channels = []
            self.agent = SimpleNamespace(
                trigger_manager=SimpleNamespace(_triggers={}, _created_at={})
            )

        async def stop(self) -> None:
            return None

    class _DriveRuntime:
        def __init__(self) -> None:
            self.removed = []

        async def on_creature_removed(
            self, creature_id, *, graph_id, graph_member_ids
        ) -> None:
            self.removed.append((creature_id, graph_id, graph_member_ids))

    graph = SimpleNamespace(
        creature_ids={"existing", "new"},
        listen_edges={},
        send_edges={},
        channels={},
    )
    drive = _DriveRuntime()
    engine = SimpleNamespace(
        _creatures={
            "existing": _Creature("existing"),
            "new": _Creature("new"),
        },
        _topology=SimpleNamespace(
            graphs={"g": graph},
            creature_to_graph={"existing": "g", "new": "g"},
        ),
        _drive_runtime=drive,
        _environments={},
        _session_stores={},
        _owned_sessions=set(),
    )
    transaction = RecipeApplyTransaction(engine)
    transaction.snapshot_existing_members("g")
    transaction.record_creature("new")

    await transaction.rollback()

    assert drive.removed == [("new", "g", frozenset({"existing"}))]
