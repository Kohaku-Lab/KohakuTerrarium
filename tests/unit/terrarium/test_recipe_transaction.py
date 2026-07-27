"""Unit tests for recipe apply transaction rollback."""

from __future__ import annotations

import asyncio

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
async def test_recipe_transaction_external_compensation_is_best_effort(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = _Engine([])
    transaction = RecipeApplyTransaction(engine)  # type: ignore[arg-type]
    calls: list[str] = []

    async def succeeds() -> None:
        calls.append("succeeds")

    async def fails() -> None:
        calls.append("fails")
        raise RuntimeError("external cleanup failed")

    transaction.record_external_compensation(succeeds)
    transaction.record_external_compensation(fails)

    await transaction.rollback()

    assert calls == ["fails", "succeeds"]
    assert "recipe external compensation failed" in caplog.text


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
