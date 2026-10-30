"""Recipe discard reattachment uses normalized primary path identity."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from kohakuterrarium.terrarium.recipe import discard as discard_module
from tests.unit.laboratory.file_mutation_enrollment_helpers import assert_idle
from tests.unit.terrarium.recipe.test_discard_enrollment import (
    _OwnedEngine,
    _closed_store,
)


def _symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink unavailable")


async def _discard_after_reattach(monkeypatch, engine, graph_id: str, attached):
    entered, release = asyncio.Event(), asyncio.Event()
    original = discard_module.run_admitted_mutation

    async def paused(admit, mutate):
        entered.set()
        await release.wait()
        return await original(admit, mutate)

    monkeypatch.setattr(discard_module, "run_admitted_mutation", paused)
    task = asyncio.create_task(discard_module.discard_recipe_graph(engine, graph_id))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        engine._session_stores["reattached"] = attached
        release.set()
        await task
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


class TestRecipeDiscardSameFileGuard:
    @pytest.mark.parametrize("alias_name", ["alias.kohakutr", "alias.kohakutr-wal"])
    async def test_alias_reattach_refuses_without_deleting(
        self, tmp_path, monkeypatch, alias_name
    ):
        path = tmp_path / "recipe.kohakutr"
        store = _closed_store(path)
        alias = tmp_path / alias_name
        _symlink_or_skip(alias, path)
        engine = _OwnedEngine("g", store)
        try:
            with pytest.raises(ValueError, match="active session store"):
                await _discard_after_reattach(
                    monkeypatch, engine, "g", SimpleNamespace(path=alias)
                )
            assert path.exists()
            assert alias.is_symlink()
            assert alias.exists()
            assert "reattached" in engine._session_stores
            assert_idle(path)
        finally:
            store.close(update_status=False)

    async def test_versioned_session_stays_distinct(self, tmp_path, monkeypatch):
        path = tmp_path / "recipe.kohakutr"
        store = _closed_store(path)
        version = tmp_path / "recipe.kohakutr.v2"
        version.write_bytes(b"version")
        engine = _OwnedEngine("g", store)
        try:
            await _discard_after_reattach(
                monkeypatch, engine, "g", SimpleNamespace(path=version)
            )
            assert not path.exists()
            assert version.read_bytes() == b"version"
            assert "reattached" in engine._session_stores
            assert_idle(path)
        finally:
            store.close(update_status=False)
