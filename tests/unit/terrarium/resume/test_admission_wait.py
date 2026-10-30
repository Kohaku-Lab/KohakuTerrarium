"""Engine resume admission waits keep the loop live and cancel owned opens safely."""

import asyncio
import json
import threading
from contextlib import closing

import pytest

from kohakuterrarium.core.config.load import build_agent_config
from kohakuterrarium.core.config.serde import pack_agent_config
from kohakuterrarium.session.occupancy.gate import GATE, try_fast
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.resume import resume as resume_mod
from kohakuterrarium.terrarium.resume import manifest as manifest_mod
from kohakuterrarium.terrarium.engine.engine import Terrarium
from kohakuterrarium.terrarium.graph.manifest import MANIFEST_KEY
from kohakuterrarium.testing.llm import ScriptedLLM


@pytest.fixture
def saved(tmp_path, monkeypatch):
    for module in ("bootstrap.llm", "bootstrap.agent_init"):
        monkeypatch.setattr(
            f"kohakuterrarium.{module}.create_llm_provider",
            lambda *a, **kw: ScriptedLLM(["resumed reply"]),
        )
    definition = {
        "name": "worker",
        "tools": [],
        "input": {"type": "none"},
        "output": {"type": "none"},
        "compact": {"enabled": False},
    }
    recipe = tmp_path / "recipe.json"
    recipe.write_text(
        json.dumps({"terrarium": {"name": "team", "creatures": [definition]}}),
        encoding="utf-8",
    )
    path = tmp_path / "saved.kohakutr"
    with closing(SessionStore(path)) as store:
        store.init_meta("saved", "terrarium", str(recipe), str(tmp_path), ["worker"])
    manifest = {
        "kind": "kohakuterrarium.live_graph",
        "version": 1,
        "revision": 1,
        "graph_id": "saved",
        "creatures": [
            {
                "creature_id": "worker-id",
                "name": "worker",
                "config_snapshot": pack_agent_config(
                    build_agent_config(definition, tmp_path)
                ),
                "source_ref": None,
                "pwd": str(tmp_path),
                "is_privileged": False,
                "parent_creature_id": None,
            }
        ],
        "channels": [],
        "listen": [],
        "send": [],
    }
    return path, manifest


@pytest.mark.parametrize("phase", ["type", "legacy_writer", "manifest_writer"])
@pytest.mark.parametrize("cancel", [False, True])
async def test_real_adoption_wait_and_cancel(saved, monkeypatch, phase, cancel):
    path, manifest = saved
    if phase == "manifest_writer":
        with closing(SessionStore(path)) as store:
            store.meta[MANIFEST_KEY] = manifest
    module = manifest_mod if phase == "manifest_writer" else resume_mod
    name = "detect_session_type" if phase == "type" else "_open_store_with_migration"
    original = getattr(module, name)
    entered, released, finished, watchdog_fired = (threading.Event() for _ in range(4))
    leases, opened = [], []
    loop_thread = threading.get_ident()
    opener_threads = []

    def wrapped(*args, **kwargs):
        opener_threads.append(threading.get_ident())
        admission = try_fast(path, eligible=True)
        assert admission.lease is not None
        leases.append(admission.lease)
        if released.is_set():
            admission.lease.finish(source_released=True)
        timer.start()
        entered.set()
        try:
            value = original(*args, **kwargs)
            if isinstance(value, SessionStore):
                opened.append(value)
            return value
        finally:
            finished.set()

    def release():
        for lease in leases:
            lease.finish(source_released=True)
        released.set()

    def watchdog():
        watchdog_fired.set()
        release()

    monkeypatch.setattr(module, name, wrapped)
    async with Terrarium() as engine:
        task = asyncio.create_task(engine.adopt_session(path, io="headless"))
        timer = threading.Timer(8, watchdog)
        try:
            assert await asyncio.to_thread(entered.wait, 15)
            while not GATE.snapshot(path).legacy_pending:
                assert (
                    not watchdog_fired.is_set()
                ), "resume admission blocked event loop"
                assert not task.done(), "resume exited before reaching admission"
                await asyncio.sleep(0.005)
            for _ in range(5):
                await asyncio.sleep(0.005)
            assert not watchdog_fired.is_set(), "resume admission blocked event loop"
            assert GATE.snapshot(path).legacy_pending >= 1
            if cancel:
                task.cancel()
                await asyncio.sleep(0.01)
                task.cancel()
                await asyncio.sleep(0.01)
                assert not released.is_set()
                if phase != "type":
                    assert (
                        not task.done()
                    ), "owned open must drain before cancellation exits"
            release()
            if cancel:
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert await asyncio.to_thread(finished.wait, 5)
                assert not engine.list_creatures()
                assert not GATE.snapshot(path).registered
                if phase != "type":
                    assert len(opened) == 1 and opened[0]._closed
                monkeypatch.setattr(module, name, original)
                await engine.adopt_session(path, io="headless")
                worker = engine.list_creatures()[0]
                assert (await worker.run("retry", timeout=5)).text == "resumed reply"
            else:
                await task
                worker = engine.list_creatures()[0]
                assert (await worker.run("hello", timeout=5)).text == "resumed reply"
            assert opener_threads == [opener_threads[0]]
            assert opener_threads[0] != loop_thread
        finally:
            release()
            timer.cancel()
            if timer.ident is not None:
                timer.join()
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.to_thread(finished.wait, 5)
    assert not GATE.snapshot(path).registered
    if cancel and phase != "type":
        assert len(opened) == 1
        assert opened[0]._closed
        with closing(SessionStore(path, writer_lock=True)) as reopened:
            assert reopened.meta["session_id"] == "saved"
