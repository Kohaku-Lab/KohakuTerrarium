import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from kohakuterrarium.core.config import AgentConfig
from kohakuterrarium.core.config_serde import pack_agent_config
from kohakuterrarium.core.session import Session
from kohakuterrarium.errors import GraphManifestError, SessionNotResumableError
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium import resume as resume_mod
from kohakuterrarium.terrarium import resume_manifest as manifest_resume_mod
from kohakuterrarium.terrarium.creature_host import Creature
from kohakuterrarium.terrarium.graph_manifest import MANIFEST_KEY
from kohakuterrarium.testing.terrarium import TestTerrariumBuilder, _FakeAgent


def _manifest(pwd: str = "."):
    return {
        "kind": "kohakuterrarium.live_graph",
        "version": 1,
        "revision": 2,
        "graph_id": "graph_saved",
        "creatures": [
            {
                "creature_id": "alice_id",
                "name": "alice",
                "config_snapshot": pack_agent_config(AgentConfig(name="alice")),
                "source_ref": "@pack/alice",
                "pwd": pwd,
                "is_privileged": True,
                "parent_creature_id": None,
            }
        ],
        "channels": [{"name": "tasks", "description": "saved"}],
        "listen": [["alice_id", "tasks"]],
        "send": [["alice_id", "tasks"]],
    }


def _restore_creature(config, *, creature_id, name, graph, pwd, **kwargs):
    agent = _FakeAgent(name=name)
    agent.config = config
    agent.controller = SimpleNamespace(conversation=None)
    agent.session = Session(key=creature_id)
    agent._branch_id = "main"
    agent._parent_branch_path = []
    return Creature(
        creature_id=creature_id,
        name=name,
        agent=agent,
        config=config,
        graph_id=graph,
        config_snapshot=pack_agent_config(config),
        source_ref="@pack/alice",
        build_pwd=pwd,
    )


async def _engine_for_manifest_resume(monkeypatch):
    engine = await TestTerrariumBuilder().build()
    original_add = engine.add_creature

    async def add_creature(config, **kwargs):
        if isinstance(config, AgentConfig):
            creature = _restore_creature(config, **kwargs)
            return await original_add(
                creature,
                graph=kwargs["graph"],
                start=False,
                session=False,
                _identity_reserved=kwargs.get("_identity_reserved", False),
            )
        return await original_add(config, **kwargs)

    monkeypatch.setattr(engine, "add_creature", add_creature)
    monkeypatch.setattr(manifest_resume_mod, "inject_saved_state", lambda *args: None)
    return engine


def _write_manifest_store(path, manifest, *, pwd="legacy-original"):
    store = SessionStore(path)
    store.init_meta("saved", "agent", "", pwd, ["alice"])
    store.meta[MANIFEST_KEY] = copy.deepcopy(manifest)
    store.close(update_status=False)


async def _false_checkpoint(*args, **kwargs):
    return False


class TestManifestResume:
    async def test_manifest_path_bypasses_legacy_and_restores_exact_topology(
        self, monkeypatch, tmp_path
    ):
        path = Path(tmp_path / "saved.kohakutr")
        _write_manifest_store(path, _manifest(), pwd=".")
        monkeypatch.setattr(
            resume_mod,
            "detect_session_type",
            lambda _path: pytest.fail("legacy dispatch must not run"),
        )
        engine = await _engine_for_manifest_resume(monkeypatch)
        try:
            graph_id = await resume_mod.resume_into_engine(engine, path)
            assert graph_id == "graph_saved"
            graph = engine.get_graph(graph_id)
            assert graph.creature_ids == {"alice_id"}
            assert set(graph.channels) == {"tasks"}
            assert graph.listen_edges["alice_id"] == {"tasks"}
            assert graph.send_edges["alice_id"] == {"tasks"}
        finally:
            await engine.shutdown()

    async def test_manifest_ids_are_reserved_before_restore_io(
        self, monkeypatch, tmp_path
    ):
        manifest_data = _manifest()
        manifest = resume_mod._manifest.parse_manifest(manifest_data)
        path = tmp_path / "run.kohakutr"
        _write_manifest_store(path, manifest_data, pwd=".")
        plan = resume_mod._workspace.plan_workspace_resume(manifest)
        engine = await TestTerrariumBuilder().build()
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        calls = 0

        async def blocked_restore(*args, **kwargs):
            nonlocal calls
            calls += 1
            first_started.set()
            await release_first.wait()
            kwargs["store"].close(update_status=False)
            return "graph_saved"

        monkeypatch.setattr(
            manifest_resume_mod, "_resume_reserved_manifest", blocked_restore
        )
        first = asyncio.create_task(
            manifest_resume_mod.resume_manifest_into_engine(
                engine,
                path,
                plan,
            )
        )
        await first_started.wait()
        with pytest.raises(ValueError, match="already exists"):
            await manifest_resume_mod.resume_manifest_into_engine(
                engine,
                path,
                plan,
            )
        release_first.set()
        assert await first == "graph_saved"
        assert calls == 1
        await engine.shutdown()

    async def test_checkpoint_failure_restores_original_workspace_metadata(
        self, monkeypatch, tmp_path
    ):
        old_pwd = tmp_path / "old"
        replacement = tmp_path / "replacement"
        replacement.mkdir()
        path = tmp_path / "checkpoint.kohakutr"
        original = _manifest(str(old_pwd))
        _write_manifest_store(path, original)
        engine = await _engine_for_manifest_resume(monkeypatch)
        monkeypatch.setattr(
            manifest_resume_mod._checkpoint, "checkpoint", _false_checkpoint
        )

        with pytest.raises(SessionNotResumableError, match="checkpoint"):
            await resume_mod.resume_into_engine(
                engine,
                path,
                workspace_overrides={"alice_id": str(replacement)},
            )

        verify = SessionStore.open_readonly(path)
        try:
            assert verify.meta[MANIFEST_KEY] == original
            assert verify.meta["pwd"] == "legacy-original"
            assert "workspace_resume_state" not in verify.meta
        finally:
            verify.close(update_status=False)
        assert "graph_saved" not in engine._topology.graphs

    async def test_rollback_failure_marks_partial_dirty(self, monkeypatch, tmp_path):
        old_pwd = tmp_path / "old"
        replacement = tmp_path / "replacement"
        replacement.mkdir()
        path = tmp_path / "dirty.kohakutr"
        original = _manifest(str(old_pwd))
        _write_manifest_store(path, original)
        engine = await _engine_for_manifest_resume(monkeypatch)

        def fail_rollback(target, manifest):
            raise OSError("rollback failed")

        monkeypatch.setattr(
            manifest_resume_mod._manifest, "save_manifest", fail_rollback
        )
        monkeypatch.setattr(
            manifest_resume_mod._checkpoint, "checkpoint", _false_checkpoint
        )

        with pytest.raises(SessionNotResumableError, match="checkpoint"):
            await resume_mod.resume_into_engine(
                engine,
                path,
                workspace_overrides={"alice_id": str(replacement)},
            )

        verify = SessionStore.open_readonly(path)
        try:
            state = verify.meta["workspace_resume_state"]
            assert state["status"] == "partial_dirty"
            assert "rollback failed" in state["rollback_error"]
        finally:
            verify.close(update_status=False)

    async def test_status_failure_restores_all_checkpoint_metadata(
        self, monkeypatch, tmp_path
    ):
        old_pwd = tmp_path / "old"
        replacement = tmp_path / "replacement"
        replacement.mkdir()
        path = tmp_path / "status-failure.kohakutr"
        original = _manifest(str(old_pwd))
        _write_manifest_store(path, original)
        seed = SessionStore(path)
        seed.meta["runtime_topology"] = {"channels": [{"name": "old"}]}
        seed.meta["status"] = "completed"
        seed.meta["last_active"] = "before-resume"
        seed.close(update_status=False)
        engine = await _engine_for_manifest_resume(monkeypatch)

        def fail_status(store, status):
            store.meta["status"] = status
            store.meta["last_active"] = "partially-updated"
            raise OSError("status write failed")

        monkeypatch.setattr(SessionStore, "update_status", fail_status)

        with pytest.raises(OSError, match="status write failed"):
            await resume_mod.resume_into_engine(
                engine,
                path,
                workspace_overrides={"alice_id": str(replacement)},
            )

        verify = SessionStore.open_readonly(path)
        try:
            assert verify.meta[MANIFEST_KEY] == original
            assert verify.meta["pwd"] == "legacy-original"
            assert verify.meta["runtime_topology"] == {"channels": [{"name": "old"}]}
            assert verify.meta["status"] == "completed"
            assert verify.meta["last_active"] == "before-resume"
        finally:
            verify.close(update_status=False)
        assert "graph_saved" not in engine._topology.graphs

    async def test_start_failure_does_not_pollute_authoritative_store(
        self, monkeypatch, tmp_path
    ):
        path = tmp_path / "start-failure.kohakutr"
        original = _manifest(str(tmp_path))
        _write_manifest_store(path, original)
        seed = SessionStore(path)
        seed.append_event("alice", "seed", {"content": "original"})
        seed.close(update_status=False)
        engine = await _engine_for_manifest_resume(monkeypatch)

        async def fail_start(self, *, requested=True):
            if self.agent.session_store is not None:
                self.agent.session_store.append_event(
                    self.name, "startup", {"content": "pollution"}
                )
            raise RuntimeError("start failed")

        monkeypatch.setattr(Creature, "start", fail_start)

        with pytest.raises(RuntimeError, match="start failed"):
            await resume_mod.resume_into_engine(engine, path)

        verify = SessionStore.open_readonly(path)
        try:
            assert [event["type"] for event in verify.get_events("alice")] == ["seed"]
            assert verify.meta[MANIFEST_KEY] == original
            assert verify.meta["pwd"] == "legacy-original"
        finally:
            verify.close(update_status=False)
        assert "graph_saved" not in engine._topology.graphs

    async def test_attach_failure_removes_partially_attached_store(
        self, monkeypatch, tmp_path
    ):
        path = tmp_path / "attach-failure.kohakutr"
        original = _manifest(str(tmp_path))
        _write_manifest_store(path, original)
        engine = await _engine_for_manifest_resume(monkeypatch)

        async def fail_after_attach(graph_id, store):
            engine._session_stores[graph_id] = store
            raise RuntimeError("drive bind failed")

        monkeypatch.setattr(engine, "attach_session", fail_after_attach)

        with pytest.raises(RuntimeError, match="drive bind failed"):
            await resume_mod.resume_into_engine(engine, path)

        assert "graph_saved" not in engine._session_stores
        assert "graph_saved" not in engine._owned_sessions
        assert "graph_saved" not in engine._topology.graphs
        verify = SessionStore.open_readonly(path)
        try:
            assert verify.meta[MANIFEST_KEY] == original
            assert verify.meta["pwd"] == "legacy-original"
        finally:
            verify.close(update_status=False)

    async def test_manifest_requires_stable_build_provenance(self, tmp_path):
        raw = _manifest()
        raw["creatures"][0]["source_ref"] = None
        raw["creatures"][0]["config_snapshot"] = None
        path = Path(tmp_path / "bad.kohakutr")
        _write_manifest_store(path, raw, pwd=".")
        engine = await TestTerrariumBuilder().build()
        with pytest.raises(GraphManifestError):
            await resume_mod.resume_into_engine(engine, path)
