"""Recipe cleanup guards and close protocol compatibility."""

from contextlib import closing

import pytest

from kohakuterrarium.session.occupancy.gate import GATE
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.recipe.discard import _close_store, discard_recipe_graph
from kohakuterrarium.testing.terrarium import TestTerrariumBuilder


async def test_missing_graph_and_unowned_store_leave_files(tmp_path):
    engine = await TestTerrariumBuilder().with_creature("alice").build()
    path = tmp_path / "unowned.kohakutr"
    store = SessionStore(path)
    gid = engine.list_graphs()[0].graph_id
    try:
        with pytest.raises(KeyError, match="not in engine"):
            await discard_recipe_graph(engine, "missing")
        with pytest.raises(ValueError, match="no worker-owned"):
            await discard_recipe_graph(engine, gid)
        engine._session_stores[gid] = store
        with pytest.raises(ValueError, match="no worker-owned"):
            await discard_recipe_graph(engine, gid)
        assert path.exists()
    finally:
        await engine.shutdown()
        store.close(update_status=False)


async def test_missing_creature_does_not_prevent_owned_cleanup(tmp_path):
    engine = await TestTerrariumBuilder().with_creature("alice").build()
    gid = engine.list_graphs()[0].graph_id
    path = tmp_path / "orphan.kohakutr"
    store = SessionStore(path)
    engine._session_stores[gid] = store
    engine._owned_sessions.add(gid)
    engine._creatures.pop("alice")
    try:
        await discard_recipe_graph(engine, gid)
        assert gid not in engine._topology.graphs
        assert not path.exists()
        assert not GATE.snapshot(path).registered
    finally:
        await engine.shutdown()
        store.close(update_status=False)


async def test_stop_io_failure_still_disposes_owned_store(tmp_path, monkeypatch):
    engine = await TestTerrariumBuilder().with_creature("alice").build()
    creature = engine.get_creature("alice")
    gid = creature.graph_id
    path = tmp_path / "stop-error.kohakutr"
    store = SessionStore(path)
    engine._session_stores[gid] = store
    engine._owned_sessions.add(gid)
    await creature.start()

    async def fail_stop():
        raise OSError("stop failed")

    monkeypatch.setattr(creature.agent, "stop", fail_stop)
    try:
        await discard_recipe_graph(engine, gid)
        assert not path.exists()
        assert not GATE.snapshot(path).registered
    finally:
        creature.agent._running = False
        await engine.shutdown()
        store.close(update_status=False)


@pytest.mark.parametrize("async_close", [False, True])
async def test_close_protocol_releases_native_store(tmp_path, async_close):
    path = tmp_path / "protocol.kohakutr"
    store = SessionStore(path)
    store.init_meta("sid", "agent", "config", str(tmp_path), ["alice"])

    class Legacy:
        def close(self):
            store.close(update_status=False)

    class Async:
        async def close(self, update_status=False):
            store.close(update_status=update_status)

    try:
        await _close_store(Async() if async_close else Legacy())
        assert not GATE.snapshot(path).registered
        with closing(SessionStore.open_readonly(path)) as reopened:
            assert reopened.meta["session_id"] == "sid"
    finally:
        store.close(update_status=False)
