"""Persistent graph unload preserves membership and Drive recovery state."""

import asyncio
import gc
import warnings
import time

import pytest

from kohakuterrarium.bootstrap import agent_init, llm
from kohakuterrarium.session.readonly import read_session_meta
from kohakuterrarium.terrarium import graph_checkpoint
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.graph_manifest import MANIFEST_KEY
from kohakuterrarium.terrarium.session_unload import unload_session_graph
from kohakuterrarium.terrarium.drive.config import (
    DriveRuntimeConfig,
    default_registrations,
)
from kohakuterrarium.terrarium.drive.models import ActorRef
from kohakuterrarium.terrarium.drive.requests import CreateDriveRequest
from kohakuterrarium.testing.llm import ScriptedLLM, ScriptEntry


@pytest.fixture
def config(tmp_path, monkeypatch):
    provider = ScriptedLLM(["ack"])
    monkeypatch.setattr(llm, "create_llm_provider", lambda *a, **k: provider)
    monkeypatch.setattr(agent_init, "create_llm_provider", lambda *a, **k: provider)
    path = tmp_path / "agent.yaml"
    path.write_text(
        "name: worker\nsystem_prompt: Test\ninput: {type: none}\noutput: {type: none}\n"
    )
    return path, provider


async def test_unload_preserves_whole_graph_and_other_sessions(config, tmp_path):
    path, _ = config
    engine = Terrarium(pwd=str(tmp_path), session_dir=str(tmp_path / "sessions"))
    try:
        left = await engine.add_creature(path, name="left", start=True)
        middle = await engine.add_creature(
            path, name="middle", graph=left.graph_id, start=True
        )
        right = await engine.add_creature(
            path, name="right", graph=left.graph_id, start=True
        )
        outsider = await engine.add_creature(path, name="outsider", start=True)
        await engine.connect(left, middle, channel="a")
        await engine.connect(middle, right, channel="b")
        gid = left.graph_id
        store = engine._session_stores[gid]
        before = read_session_meta(store.path)[MANIFEST_KEY]
        files = set((tmp_path / "sessions").glob("*.kohakutr"))
        removed = await unload_session_graph(engine, gid)
        assert set(removed) == {left.creature_id, middle.creature_id, right.creature_id}
        assert engine.list_creatures() == [outsider]
        assert outsider.is_running
        assert gid not in engine._session_stores
        after = read_session_meta(store.path)[MANIFEST_KEY]
        assert after["creatures"] == before["creatures"]
        assert after["channels"] == before["channels"]
        assert set((tmp_path / "sessions").glob("*.kohakutr")) == files
        store.close(update_status=False)
        with pytest.raises(KeyError):
            await unload_session_graph(engine, gid)
    finally:
        await engine.shutdown()


async def test_unload_stops_an_admitted_turn_without_waiting_for_llm(config, tmp_path):
    path, provider = config
    provider.script = [ScriptEntry("long response", delay_per_chunk=30, chunk_size=1)]
    engine = Terrarium(
        pwd=str(tmp_path),
        drive_config=DriveRuntimeConfig(enabled=True),
        drive_registrations=default_registrations(),
    )
    store = None
    try:
        creature = await engine.add_creature(
            path, session=tmp_path / "active.kohakutr", start=True
        )
        await creature.wait_restoration_ready()
        manager = engine.drives.manager_for(creature.graph_id)
        actor = ActorRef("creature", creature.creature_id)
        record = await manager.create_drive(
            CreateDriveRequest(
                kind="goal",
                title="active",
                scope_type="creature",
                scope_id=creature.creature_id,
                owner=actor,
                owner_scope="creature",
                created_by=actor,
                spec={"objective": "test", "autonomy": "manual"},
            ),
            actor=actor,
            graph_id=creature.graph_id,
        )
        await manager.wake_drive(
            record.drive_id, actor=actor, expected_revision=record.revision
        )
        for _ in range(100):
            if provider.call_count:
                break
            await asyncio.sleep(0.01)
        assert provider.call_count == 1
        store = engine._session_stores[creature.graph_id]
        started = time.monotonic()
        await asyncio.wait_for(
            unload_session_graph(engine, creature.graph_id), timeout=5
        )
        assert time.monotonic() - started < 2
        assert not creature.is_running
        assert read_session_meta(store.path)[MANIFEST_KEY] is not None
        assert (
            await manager.get_assignment(record.drive_id)
        ).assignment_state == "assigned"
        deliveries = await manager.list_deliveries(record.drive_id)
        assert any(d.admitted_at is not None for d in deliveries)
    finally:
        await engine.shutdown()
        if store is not None:
            store.close(update_status=False)


@pytest.mark.parametrize("failure", ["checkpoint", "stop", "partial_stop", "cancelled"])
async def test_checkpoint_failure_keeps_graph_registered_for_retry(
    config, tmp_path, monkeypatch, failure
):
    path, _ = config
    engine = Terrarium(pwd=str(tmp_path))
    try:
        creature = await engine.add_creature(
            path, session=tmp_path / "retry.kohakutr", start=True
        )
        gid = creature.graph_id
        store = engine._session_stores[gid]
        original = graph_checkpoint.checkpoint
        await creature.wait_restoration_ready()
        await engine.drives._registry.ensure_started(gid)
        actor = ActorRef("creature", creature.creature_id)

        manager = engine.drives.manager_for(gid)
        record = await manager.create_drive(
            CreateDriveRequest(
                kind="goal",
                title="after failed unload",
                scope_type="creature",
                scope_id=creature.creature_id,
                owner=actor,
                owner_scope="creature",
                created_by=actor,
                spec={"objective": "verify recovery", "autonomy": "manual"},
            ),
            actor=actor,
            graph_id=gid,
        )
        error_type = asyncio.CancelledError if failure == "cancelled" else OSError
        original_stop = creature.stop

        async def fail(*args, **kwargs):
            if failure == "partial_stop":
                await original_stop(*args, **kwargs)
            raise error_type("checkpoint failed")

        with monkeypatch.context() as patch:
            if failure in {"checkpoint", "cancelled"}:
                patch.setattr(graph_checkpoint, "checkpoint", fail)
            else:
                patch.setattr(creature, "stop", fail)
            with pytest.raises(error_type, match="checkpoint failed"):
                await unload_session_graph(engine, gid)
        assert engine.get_creature(creature.creature_id) is creature
        assert engine._session_stores[gid] is store
        assert read_session_meta(store.path)[MANIFEST_KEY] is not None
        assert graph_checkpoint.checkpoint is original
        assert creature.is_running
        await creature.wait_restoration_ready()
        manager = engine.drives.manager_for(gid)
        await manager.wake_drive(
            record.drive_id, actor=actor, expected_revision=record.revision
        )
        for _ in range(200):
            if any(
                d.state == "acknowledged"
                for d in await manager.list_deliveries(record.drive_id)
            ):
                break
            await asyncio.sleep(0.01)
        assert any(
            d.state == "acknowledged"
            for d in await manager.list_deliveries(record.drive_id)
        )
        await unload_session_graph(engine, gid)
        store.close(update_status=False)
    finally:
        await engine.shutdown()


async def test_suppressed_checkpoint_refuses_to_unload(config, tmp_path):
    path, _ = config
    engine = Terrarium(pwd=str(tmp_path))
    try:
        creature = await engine.add_creature(path, session=tmp_path / "saved.kohakutr")
        gid = creature.graph_id
        with graph_checkpoint.suppress(engine):
            with pytest.raises(RuntimeError, match="manifest"):
                await unload_session_graph(engine, gid)
        assert engine.get_creature(creature.creature_id) is creature
        assert gid in engine._session_stores
    finally:
        await engine.shutdown()


async def test_missing_creature_is_rejected_before_creating_coroutines(
    config, tmp_path
):
    path, _ = config
    engine = Terrarium(pwd=str(tmp_path))
    try:
        creature = await engine.add_creature(path)
        graph = engine._topology.graphs[creature.graph_id]
        graph.creature_ids.add("missing")
        try:
            with warnings.catch_warnings(record=True) as captured:
                warnings.simplefilter("always", RuntimeWarning)
                with pytest.raises(KeyError, match="missing"):
                    await unload_session_graph(engine, graph.graph_id)
                gc.collect()
            assert not any("never awaited" in str(w.message) for w in captured)
        finally:
            graph.creature_ids.remove("missing")
    finally:
        await engine.shutdown()


async def test_failed_unload_does_not_start_explicitly_stopped_member(config, tmp_path):
    path, _ = config
    engine = Terrarium(pwd=str(tmp_path))
    try:
        active = await engine.add_creature(path, session=tmp_path / "saved.kohakutr")
        stopped = await engine.add_creature(path, name="stopped", graph=active.graph_id)
        await engine.stop(stopped)
        with graph_checkpoint.suppress(engine):
            with pytest.raises(RuntimeError, match="manifest"):
                await unload_session_graph(engine, active.graph_id)
        assert active.is_running
        assert stopped.stop_requested and not stopped.is_running
    finally:
        await engine.shutdown()


async def test_failed_runtime_recovery_reports_both_failures(
    config, tmp_path, monkeypatch
):
    path, _ = config
    engine = Terrarium(pwd=str(tmp_path))
    try:
        creature = await engine.add_creature(path, session=tmp_path / "saved.kohakutr")

        async def fail_start(*args):
            raise OSError("restart unavailable")

        with monkeypatch.context() as patch:
            patch.setattr(engine, "start", fail_start)
            with graph_checkpoint.suppress(engine):
                with pytest.raises(
                    RuntimeError, match="runtime recovery failed.*restart unavailable"
                ) as caught:
                    await unload_session_graph(engine, creature.graph_id)
        assert "manifest" in str(caught.value.__cause__)
        assert engine.get_creature(creature.creature_id) is creature
    finally:
        await engine.shutdown()
