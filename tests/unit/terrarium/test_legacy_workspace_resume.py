"""Atomic workspace publication for manifest-less legacy resumes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kohakuterrarium.core.config import AgentConfig
from kohakuterrarium.core.config_serde import pack_agent_config
from kohakuterrarium.errors import SessionNotResumableError
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium import resume as resume_mod
from kohakuterrarium.terrarium.config import TerrariumConfig
from kohakuterrarium.terrarium.creature_host import Creature
from kohakuterrarium.terrarium.graph_manifest import MANIFEST_KEY
from kohakuterrarium.terrarium.topology_snapshot import META_KEY as TOPOLOGY_KEY
from kohakuterrarium.testing.terrarium import TestTerrariumBuilder, _FakeAgent


def _topology(description: str) -> dict:
    return {
        "channels": [{"name": "saved", "description": description}],
        "listen_edges": {},
        "send_edges": {},
    }


class _InterceptMetaWrites:
    """Delegate to a real KVault while exposing its immediate-write seam."""

    def __init__(self, inner, before_set):
        self._inner = inner
        self._before_set = before_set

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def __getitem__(self, key):
        return self._inner[key]

    def __setitem__(self, key, value):
        self._before_set(key, value)
        self._inner[key] = value

    def __contains__(self, key):
        return key in self._inner


def _load_meta(path) -> dict:
    store = SessionStore(path)
    try:
        return store.load_meta()
    finally:
        store.close(update_status=False)


def _assert_requires_replacement(path) -> None:
    with pytest.raises(
        SessionNotResumableError,
        match="saved working directory is missing or invalid",
    ):
        resume_mod.prepare_resume_workspace(path)


def _agent_case(monkeypatch, tmp_path, filename):
    old_pwd = tmp_path / "deleted-workspace"
    replacement = tmp_path / "relocated"
    replacement.mkdir()
    path = tmp_path / filename
    store = SessionStore(path, writer_lock=True)
    store.init_meta("saved", "agent", "", str(old_pwd), ["alice"])
    original_topology = _topology("original")
    store.meta[TOPOLOGY_KEY] = original_topology

    fake_agent = _FakeAgent(name="alice")
    fake_agent.executor = SimpleNamespace(_working_dir=replacement.resolve())
    monkeypatch.setattr(
        resume_mod,
        "resume_agent",
        lambda *args, **kwargs: (fake_agent, store),
    )
    return path, store, old_pwd, replacement, original_topology


class TestLegacyTerrariumWorkspaceResume:
    @pytest.mark.parametrize("checkpoint_fails", [False, True])
    async def test_pwd_override_persistence_is_atomic(
        self, monkeypatch, tmp_path, checkpoint_fails
    ):
        old_pwd = tmp_path / "deleted-workspace"
        replacement = tmp_path / "relocated"
        replacement.mkdir()
        path = tmp_path / "terrarium.kohakutr.v2"
        original_topology = _topology("original")
        saved = SessionStore(path)
        saved.init_meta(
            "saved",
            "terrarium",
            "/tmp/recipe.yaml",
            str(old_pwd),
            ["alice"],
        )
        saved.meta[TOPOLOGY_KEY] = original_topology
        saved.close(update_status=False)

        monkeypatch.setattr(
            resume_mod,
            "load_terrarium_config",
            lambda _path: TerrariumConfig(name="t", creatures=[], channels=[]),
        )
        monkeypatch.setattr(resume_mod, "inject_saved_state", lambda *args: None)
        engine_holder = {}

        async def apply_recipe(
            _config, pwd=None, created_ids=None, start=False, **_kwargs
        ):
            assert pwd == str(replacement.resolve())
            engine = engine_holder["engine"]
            agent = _FakeAgent(name="alice")
            creature = Creature(
                creature_id="alice",
                name="alice",
                agent=agent,
                config=agent.config,
                config_snapshot=pack_agent_config(AgentConfig(name="alice")),
                build_pwd=str(replacement.resolve()),
            )
            added = await engine.add_creature(creature, start=start, session=False)
            if created_ids is not None:
                created_ids.append(added.creature_id)
            return engine.get_graph(added.graph_id)

        engine = await TestTerrariumBuilder().build()
        engine_holder["engine"] = engine
        engine.apply_recipe = apply_recipe
        final_checkpoint_seen = False
        if checkpoint_fails:

            async def fail_final_checkpoint(active_engine, graph_id):
                nonlocal final_checkpoint_seen
                live_store = active_engine._session_stores.get(graph_id)
                if live_store is not None and live_store.meta.get("pwd") == str(
                    replacement.resolve()
                ):
                    final_checkpoint_seen = True
                    live_store.meta[MANIFEST_KEY] = {"partial": True}
                    live_store.meta[TOPOLOGY_KEY] = _topology("failed")
                    return False
                return True

            monkeypatch.setattr(
                resume_mod._checkpoint, "checkpoint", fail_final_checkpoint
            )
        try:
            if checkpoint_fails:
                with pytest.raises(
                    SessionNotResumableError,
                    match="did not persist a valid graph manifest",
                ):
                    await resume_mod.resume_into_engine(
                        engine, path, pwd=str(replacement)
                    )
                assert final_checkpoint_seen
            else:
                sid = await resume_mod.resume_into_engine(
                    engine, path, pwd=str(replacement)
                )
                live_store = next(iter(engine._session_stores.values()))
                assert live_store.load_meta()["pwd"] == str(replacement.resolve())
                for creature_id in list(engine.get_graph(sid).creature_ids):
                    await engine.remove_creature(creature_id)
        finally:
            await engine.shutdown()

        meta = _load_meta(path)
        if checkpoint_fails:
            assert meta["pwd"] == str(old_pwd)
            assert meta.get(MANIFEST_KEY) is None
            assert meta[TOPOLOGY_KEY] == original_topology
            assert meta.get("workspace_resume_state") is None
            _assert_requires_replacement(path)
        else:
            assert meta[MANIFEST_KEY] is None
            assert meta["pwd"] == str(replacement.resolve())


class TestLegacyAgentWorkspaceResume:
    async def test_pwd_override_persists(self, monkeypatch, tmp_path):
        path, store, _old_pwd, replacement, _topology_value = _agent_case(
            monkeypatch, tmp_path, "agent.kohakutr.v2"
        )
        checkpoint = AsyncMock(return_value=True)
        monkeypatch.setattr(resume_mod._checkpoint, "checkpoint", checkpoint)

        engine = await TestTerrariumBuilder().build()
        engine.attach_session = AsyncMock()
        try:
            await resume_mod.resume_into_engine(engine, path, pwd=str(replacement))
            assert store.load_meta()["pwd"] == str(replacement.resolve())
            assert checkpoint.await_count >= 1
        finally:
            await engine.shutdown()
            store.close(update_status=False)

        assert _load_meta(path)["pwd"] == str(replacement.resolve())

    async def test_without_override_preserves_saved_pwd_provenance(
        self, monkeypatch, tmp_path
    ):
        path = tmp_path / "relative-pwd.kohakutr.v2"
        store = SessionStore(path, writer_lock=True)
        store.init_meta("saved", "agent", "", ".", ["alice"])
        fake_agent = _FakeAgent(name="alice")
        fake_agent.executor = SimpleNamespace(_working_dir=tmp_path.resolve())
        monkeypatch.setattr(
            resume_mod,
            "resume_agent",
            lambda *args, **kwargs: (fake_agent, store),
        )
        monkeypatch.setattr(
            resume_mod._checkpoint, "checkpoint", AsyncMock(return_value=True)
        )

        engine = await TestTerrariumBuilder().build()
        engine.attach_session = AsyncMock()
        try:
            await resume_mod.resume_into_engine(engine, path)
            creature = next(iter(engine.list_creatures()))
            assert creature.build_pwd == "."
            assert store.load_meta()["pwd"] == "."
        finally:
            await engine.shutdown()
            store.close(update_status=False)

    @pytest.mark.parametrize("failure_mode", ["false", "exception"])
    async def test_checkpoint_failure_restores_workspace_metadata(
        self, monkeypatch, tmp_path, failure_mode
    ):
        path, store, old_pwd, replacement, original_topology = _agent_case(
            monkeypatch, tmp_path, "checkpoint-failure.kohakutr.v2"
        )
        final_checkpoint_seen = False

        async def fail_final_checkpoint(*_args, **_kwargs):
            nonlocal final_checkpoint_seen
            if store.meta.get("pwd") == str(replacement.resolve()):
                final_checkpoint_seen = True
                store.meta[MANIFEST_KEY] = {"partial": True}
                store.meta[TOPOLOGY_KEY] = _topology("failed")
                if failure_mode == "exception":
                    raise RuntimeError("checkpoint failed")
                return False
            return True

        monkeypatch.setattr(resume_mod._checkpoint, "checkpoint", fail_final_checkpoint)
        engine = await TestTerrariumBuilder().build()
        engine.attach_session = AsyncMock()
        try:
            error_type = (
                RuntimeError
                if failure_mode == "exception"
                else SessionNotResumableError
            )
            error_match = (
                "checkpoint failed"
                if failure_mode == "exception"
                else "did not persist a valid graph manifest"
            )
            with pytest.raises(
                error_type,
                match=error_match,
            ):
                await resume_mod.resume_into_engine(engine, path, pwd=str(replacement))
            assert final_checkpoint_seen
        finally:
            await engine.shutdown()

        meta = _load_meta(path)
        assert meta["pwd"] == str(old_pwd)
        assert meta.get(MANIFEST_KEY) is None
        assert meta[TOPOLOGY_KEY] == original_topology
        assert meta.get("workspace_resume_state") is None
        _assert_requires_replacement(path)

    async def test_initial_pwd_write_failure_preserves_original_metadata(
        self, monkeypatch, tmp_path
    ):
        path, store, old_pwd, replacement, original_topology = _agent_case(
            monkeypatch, tmp_path, "writeback-failure.kohakutr.v2"
        )
        write_attempted = False

        def fail_replacement_write(key, value):
            nonlocal write_attempted
            if key == "pwd" and value == str(replacement.resolve()):
                write_attempted = True
                raise OSError("writeback failed")

        store.meta = _InterceptMetaWrites(store.meta, fail_replacement_write)
        monkeypatch.setattr(
            resume_mod._checkpoint, "checkpoint", AsyncMock(return_value=True)
        )
        engine = await TestTerrariumBuilder().build()
        engine.attach_session = AsyncMock()
        try:
            with pytest.raises(OSError, match="writeback failed"):
                await resume_mod.resume_into_engine(engine, path, pwd=str(replacement))
            assert write_attempted
        finally:
            await engine.shutdown()

        meta = _load_meta(path)
        assert meta["pwd"] == str(old_pwd)
        assert meta.get(MANIFEST_KEY) is None
        assert meta[TOPOLOGY_KEY] == original_topology
        assert meta.get("workspace_resume_state") is None

    async def test_pwd_rollback_write_failure_marks_session_dirty(
        self, monkeypatch, tmp_path
    ):
        path, store, old_pwd, replacement, original_topology = _agent_case(
            monkeypatch, tmp_path, "rollback-write-failure.kohakutr.v2"
        )
        replacement_pwd = str(replacement.resolve())
        published = False
        rollback_attempted = False

        def fail_rollback_write(key, value):
            nonlocal published, rollback_attempted
            if key == "pwd" and value == replacement_pwd:
                published = True
            elif key == "pwd" and value == str(old_pwd) and published:
                rollback_attempted = True
                raise OSError("rollback write failed")

        store.meta = _InterceptMetaWrites(store.meta, fail_rollback_write)
        final_checkpoint_seen = False

        async def fail_final_checkpoint(*_args, **_kwargs):
            nonlocal final_checkpoint_seen
            if published and not final_checkpoint_seen:
                final_checkpoint_seen = True
                store.meta[MANIFEST_KEY] = {"partial": True}
                store.meta[TOPOLOGY_KEY] = _topology("failed")
                return False
            return True

        monkeypatch.setattr(resume_mod._checkpoint, "checkpoint", fail_final_checkpoint)
        engine = await TestTerrariumBuilder().build()
        engine.attach_session = AsyncMock()
        try:
            with pytest.raises(
                SessionNotResumableError,
                match="did not persist a valid graph manifest",
            ):
                await resume_mod.resume_into_engine(engine, path, pwd=str(replacement))
            assert final_checkpoint_seen
            assert rollback_attempted
        finally:
            await engine.shutdown()

        meta = _load_meta(path)
        assert meta["pwd"] == replacement_pwd
        assert meta.get(MANIFEST_KEY) is None
        assert meta[TOPOLOGY_KEY] == original_topology
        assert meta["workspace_resume_state"]["status"] == "partial_dirty"
        assert (
            "rollback write failed" in meta["workspace_resume_state"]["rollback_error"]
        )
        with pytest.raises(
            SessionNotResumableError, match="incomplete workspace rollback"
        ):
            resume_mod.prepare_resume_workspace(path)
