"""Unit tests for :mod:`kohakuterrarium.terrarium.resume`.

The two real branches load actual Agents from a saved store, which we
short-circuit by patching :func:`resume_agent` and
:func:`detect_session_type`. Engine + Creature integration stays real.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kohakuterrarium.builtins.inputs.none import NoneInput
from kohakuterrarium.session.readonly import read_session_meta as read_meta_strict
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.graph_manifest import (
    GraphManifest,
    ManifestCreature,
    save_manifest,
)
from kohakuterrarium.terrarium import resume as resume_mod
from kohakuterrarium.testing.terrarium import TestTerrariumBuilder, _FakeAgent
from kohakuterrarium.terrarium.config import TerrariumConfig
from kohakuterrarium.terrarium.creature_host import Creature

# ── _resolve_store_path ───────────────────────────────────────


class TestResolveStorePath:
    def test_session_store_object_uses_path(self):
        ss = SimpleNamespace(path="/some/p.kohakutr")
        out = resume_mod._resolve_store_path(ss)
        assert isinstance(out, Path)

    def test_session_store_object_fallback_to_str(self):
        class _Bare:
            def __str__(self):
                return "/fallback.kohakutr"

        out = resume_mod._resolve_store_path(_Bare())
        # No ``path`` attr → falls back to str(store).
        assert isinstance(out, Path)

    def test_string_path(self):
        out = resume_mod._resolve_store_path("/some/file.kohakutr")
        assert out == Path("/some/file.kohakutr")

    def test_path_object(self):
        out = resume_mod._resolve_store_path(Path("/some/file.kohakutr"))
        assert out == Path("/some/file.kohakutr")


# ── resume_into_engine dispatch ───────────────────────────────


class TestResumeIntoEngine:
    async def test_pwd_and_workspace_overrides_are_mutually_exclusive(self, tmp_path):
        t = await TestTerrariumBuilder().build()
        try:
            with pytest.raises(ValueError, match="mutually exclusive"):
                await resume_mod.resume_into_engine(
                    t,
                    tmp_path / "saved.kohakutr",
                    pwd=str(tmp_path),
                    workspace_overrides={"creature:a": str(tmp_path)},
                )
        finally:
            await t.shutdown()

    @pytest.fixture(autouse=True)
    def _stub_legacy_workspace_preflight(self, monkeypatch):
        monkeypatch.setattr(
            resume_mod,
            "preflight_legacy_workspace",
            lambda path, pwd=None: str(pwd or "."),
        )
        monkeypatch.setattr(resume_mod, "read_session_meta", lambda path: {})

    async def test_resume_selects_latest_readable_version_before_preflight(
        self, monkeypatch, tmp_path
    ):
        base = tmp_path / "saved.kohakutr"
        old = SessionStore(base)
        old.meta["format_version"] = 1
        old.meta["pwd"] = str(tmp_path)
        old.close(update_status=False)
        latest_path = tmp_path / "saved.kohakutr.v2"
        latest = SessionStore(latest_path)
        latest.meta["format_version"] = 2
        manifest = GraphManifest(
            graph_id="new-graph",
            creatures=(
                ManifestCreature(
                    creature_id="c1",
                    name="one",
                    config_snapshot={"name": "one"},
                    source_ref=None,
                    pwd=str(tmp_path),
                    is_privileged=False,
                    parent_creature_id=None,
                ),
            ),
            channels=(),
            listen=(),
            send=(),
            revision=1,
        )
        save_manifest(latest, manifest)
        latest.close(update_status=False)
        seen = []
        real_read = read_meta_strict

        def read_meta(path):
            seen.append(Path(path))
            return real_read(path)

        async def manifest_resume(*args, **kwargs):
            return "new-graph"

        monkeypatch.setattr(resume_mod, "read_session_meta", read_meta)
        monkeypatch.setattr(resume_mod, "_resume_manifest_into_engine", manifest_resume)
        monkeypatch.setattr(
            resume_mod,
            "detect_session_type",
            lambda store: pytest.fail("legacy branch must not run"),
        )
        t = await TestTerrariumBuilder().build()
        try:
            graph_id = await resume_mod.resume_into_engine(t, base)
        finally:
            await t.shutdown()

        assert graph_id == "new-graph"
        assert seen == [latest_path]

    async def test_unknown_session_type_raises(self, monkeypatch, tmp_path):
        monkeypatch.setattr(resume_mod, "detect_session_type", lambda p: "bogus")
        t = await TestTerrariumBuilder().build()
        try:
            with pytest.raises(ValueError, match="Unknown saved-session"):
                await resume_mod.resume_into_engine(t, tmp_path / "x.kohakutr")
        finally:
            await t.shutdown()

    async def test_agent_path_dispatches(self, monkeypatch, tmp_path):
        monkeypatch.setattr(resume_mod, "detect_session_type", lambda p: "agent")

        fake_agent = _FakeAgent(name="alice")
        fake_agent.config = SimpleNamespace(name="alice")
        captured: dict = {}
        fake_store = SimpleNamespace(
            set_conversation_open=lambda value: captured.setdefault(
                "final_open", value
            ),
            update_status=lambda value: captured.setdefault("final_status", value),
            checkpoint=lambda: None,
        )

        def _resume_agent(
            path,
            pwd_override=None,
            io_mode=None,
            llm=None,
            *,
            input_module=None,
            output_module=None,
            mark_conversation_open=True,
        ):
            captured["input_module"] = input_module
            captured["mark_conversation_open"] = mark_conversation_open
            return fake_agent, fake_store

        monkeypatch.setattr(resume_mod, "resume_agent", _resume_agent)
        monkeypatch.setattr(
            resume_mod._checkpoint, "checkpoint", AsyncMock(return_value=True)
        )

        t = await TestTerrariumBuilder().build()
        try:
            # Stub attach_session so it doesn't need a real store.
            t.attach_session = AsyncMock()
            gid = await resume_mod.resume_into_engine(t, tmp_path / "saved.kohakutr")
            assert gid
            t.attach_session.assert_awaited()
            # Engine-hosted resume MUST suppress the config's own IO loop
            # — the creature is driven by the engine / attach WebSocket,
            # never a stdin reader. Without this a worker-side resume
            # boots ``input: cli`` with no TTY and wedges the worker.
            assert isinstance(captured["input_module"], NoneInput)
            assert captured["mark_conversation_open"] is False
            assert captured["final_open"] is True
            assert captured["final_status"] == "running"
        finally:
            await t.shutdown()

    async def test_terrarium_path_dispatches(self, monkeypatch, tmp_path):
        monkeypatch.setattr(resume_mod, "detect_session_type", lambda p: "terrarium")

        fake_store = SimpleNamespace(
            load_meta=lambda: {
                "config_path": "/tmp/recipe.yaml",
                "pwd": ".",
                "agents": ["alice"],
            },
            update_status=lambda s: None,
        )

        monkeypatch.setattr(
            resume_mod, "_open_store_with_migration", lambda p, **_kw: fake_store
        )

        fake_config = TerrariumConfig(name="t", creatures=[], channels=[])
        monkeypatch.setattr(resume_mod, "load_terrarium_config", lambda p: fake_config)

        injects = []

        def _inject(agent, store, name):
            injects.append(name)

        monkeypatch.setattr(resume_mod, "inject_saved_state", _inject)
        monkeypatch.setattr(
            resume_mod._checkpoint, "checkpoint", AsyncMock(return_value=True)
        )

        t = await TestTerrariumBuilder().build()
        try:
            t.attach_session = AsyncMock()
            gid = await resume_mod.resume_into_engine(t, tmp_path / "saved.kohakutr")
            assert gid
            t.attach_session.assert_awaited()
        finally:
            await t.shutdown()

    async def test_terrarium_resume_missing_config_path(self, monkeypatch, tmp_path):
        monkeypatch.setattr(resume_mod, "detect_session_type", lambda p: "terrarium")

        fake_store = SimpleNamespace(
            load_meta=lambda: {"config_path": ""},
            update_status=lambda s: None,
        )
        monkeypatch.setattr(
            resume_mod, "_open_store_with_migration", lambda p, **_kw: fake_store
        )

        t = await TestTerrariumBuilder().build()
        try:
            with pytest.raises(ValueError, match="no config_path"):
                await resume_mod.resume_into_engine(t, tmp_path / "saved.kohakutr")
        finally:
            await t.shutdown()

    async def test_terrarium_resume_with_saved_agents_alignment(
        self, monkeypatch, tmp_path
    ):
        # Saved agents list is ["bob"] but the rebuild produces "alice".
        # Positional consumption should rename the rebuilt creature to "bob".
        monkeypatch.setattr(resume_mod, "detect_session_type", lambda p: "terrarium")
        fake_store = SimpleNamespace(
            load_meta=lambda: {
                "config_path": "/tmp/recipe.yaml",
                "pwd": ".",
                "agents": ["bob"],
            },
            update_status=lambda s: None,
        )
        monkeypatch.setattr(
            resume_mod, "_open_store_with_migration", lambda p, **_kw: fake_store
        )

        from kohakuterrarium.terrarium.config import (
            CreatureConfig,
            TerrariumConfig,
        )

        fake_config = TerrariumConfig(
            name="t",
            creatures=[
                CreatureConfig(
                    name="alice",
                    config_data={"name": "alice"},
                    base_dir=Path("."),
                )
            ],
            channels=[],
        )
        monkeypatch.setattr(resume_mod, "load_terrarium_config", lambda p: fake_config)

        # Stub apply_recipe to build a fake creature directly.

        async def _fake_apply_recipe(config, pwd=None, **_):
            assert pwd == "."
            t = engine_holder["t"]
            agent = _FakeAgent(name="alice")
            agent.config = SimpleNamespace(name="alice")
            agent.attach_session_store = lambda s: None
            c = Creature(
                creature_id="alice",
                name="alice",
                agent=agent,
                config=agent.config,
            )
            await t.add_creature(c, start=False)
            return t._topology.graphs[c.graph_id]

        monkeypatch.setattr(resume_mod, "inject_saved_state", lambda *a, **kw: None)
        monkeypatch.setattr(
            resume_mod._checkpoint, "checkpoint", AsyncMock(return_value=True)
        )

        engine_holder = {}
        t = await TestTerrariumBuilder().build()
        engine_holder["t"] = t
        t.apply_recipe = _fake_apply_recipe
        t.attach_session = AsyncMock()
        try:
            await resume_mod.resume_into_engine(t, tmp_path / "saved.kohakutr")
            # The creature got renamed positionally to "bob".
            c = t.get_creature("alice")
            assert c.name == "bob"
        finally:
            await t.shutdown()

    async def test_saved_state_injected_before_creatures_start(
        self, monkeypatch, tmp_path
    ):
        # A started creature schedules its input drive + startup
        # triggers immediately — injection after start races them
        # against an empty conversation.
        monkeypatch.setattr(resume_mod, "detect_session_type", lambda p: "terrarium")
        fake_store = SimpleNamespace(
            load_meta=lambda: {
                "config_path": "/tmp/recipe.yaml",
                "pwd": ".",
                "agents": ["alice"],
            },
            update_status=lambda s: None,
        )
        monkeypatch.setattr(
            resume_mod, "_open_store_with_migration", lambda p, **_kw: fake_store
        )

        fake_config = TerrariumConfig(name="t", creatures=[], channels=[])
        monkeypatch.setattr(resume_mod, "load_terrarium_config", lambda p: fake_config)

        engine_holder = {}
        running_at_inject = []

        async def _fake_apply_recipe(config, pwd=None, start=True, **_):
            t = engine_holder["t"]
            agent = _FakeAgent(name="alice")
            agent.attach_session_store = lambda s: None
            c = Creature(
                creature_id="alice",
                name="alice",
                agent=agent,
                config=agent.config,
            )
            await t.add_creature(c, start=start)
            return t._topology.graphs[c.graph_id]

        def _inject(agent, store, name):
            running_at_inject.append(agent.is_running)

        monkeypatch.setattr(resume_mod, "inject_saved_state", _inject)

        running_at_replay = []

        async def _fake_replay(engine, sid):
            for cid in engine.get_graph(sid).creature_ids:
                running_at_replay.append(engine.get_creature(cid).agent.is_running)

        monkeypatch.setattr(resume_mod._topo_snap, "replay", _fake_replay)
        monkeypatch.setattr(
            resume_mod._checkpoint, "checkpoint", AsyncMock(return_value=True)
        )

        t = await TestTerrariumBuilder().build()
        engine_holder["t"] = t
        t.apply_recipe = _fake_apply_recipe
        t.attach_session = AsyncMock()
        try:
            await resume_mod.resume_into_engine(t, tmp_path / "saved.kohakutr")
            assert running_at_inject == [
                False
            ], "state must be injected BEFORE the creature starts"
            # Startup triggers must see restored channels/wires — the
            # topology replay has to land before any creature starts.
            assert running_at_replay == [
                False
            ], "topology must be replayed BEFORE the creature starts"
            assert t.get_creature("alice").agent.is_running is True
        finally:
            await t.shutdown()

    async def test_terrarium_resume_no_autosession_ghost(self, monkeypatch, tmp_path):
        # REGRESSION PIN: resuming a terrarium into an engine that has
        # autosession configured (``Terrarium(session_dir=...)`` — every
        # API-server engine) must NOT let ``apply_recipe`` mint a fresh
        # ``<new_gid>.kohakutr``.  The saved store attaches right after,
        # so the minted file would be an instantly-orphaned ghost stuck
        # at ``status="running"`` in the saved-session list, plus a
        # leaked open SQLite handle.
        monkeypatch.setattr(resume_mod, "detect_session_type", lambda p: "terrarium")
        fake_store = SimpleNamespace(
            load_meta=lambda: {
                "config_path": "/tmp/recipe.yaml",
                "pwd": ".",
                "agents": [],
            },
            update_status=lambda s: None,
        )
        monkeypatch.setattr(
            resume_mod, "_open_store_with_migration", lambda p, **_kw: fake_store
        )

        from kohakuterrarium.terrarium.config import TerrariumConfig
        from kohakuterrarium.terrarium.engine import Terrarium

        fake_config = TerrariumConfig(name="t", creatures=[], channels=[])
        monkeypatch.setattr(resume_mod, "load_terrarium_config", lambda p: fake_config)
        monkeypatch.setattr(resume_mod, "inject_saved_state", lambda *a, **kw: None)
        monkeypatch.setattr(
            resume_mod._checkpoint, "checkpoint", AsyncMock(return_value=True)
        )

        session_dir = tmp_path / "sessions"
        session_dir.mkdir()
        t = Terrarium(session_dir=str(session_dir))
        try:
            # Real ``apply_recipe`` (empty recipe) + real autosession
            # machinery — only ``attach_session`` is stubbed so the
            # SimpleNamespace store does not have to behave like a
            # SessionStore.
            t.attach_session = AsyncMock()
            await resume_mod.resume_into_engine(t, tmp_path / "saved.kohakutr")
            # No ghost store file was minted next to the saved session.
            assert list(session_dir.glob("*.kohakutr")) == []
            # The engine owns exactly the RESUMED store (it opened the
            # file, so shutdown must close it — a leaked writer lock
            # blocks any later adopt) and nothing else.
            assert len(t._owned_sessions) == 1
        finally:
            await t.shutdown()

    def _write_terrarium_session(self, tmp_path) -> "Path":
        # A real terrarium-typed session file so the resume path opens it
        # under a writer lock (the leak surfaces as an un-reopenable file).
        path = tmp_path / "saved.kohakutr.v2"
        s = SessionStore(str(path))
        try:
            s.meta["format_version"] = 2
            s.init_meta(
                "sess", "terrarium", "/tmp/recipe.yaml", str(tmp_path), ["alice"]
            )
            s.flush()
        finally:
            s.close()
        return path

    async def test_terrarium_resume_config_load_failure_closes_store(
        self, monkeypatch, tmp_path
    ):
        # A failure AFTER the store opens (here: the recipe fails to load)
        # must release the writer lock the resume held, or the .kohakutr
        # can never be re-opened by a fresh writer.
        path = self._write_terrarium_session(tmp_path)

        # Hold a reference to the opened store so a leaked handle can't be
        # silently GC-collected before the assertion (that would release
        # the OS lock and mask the bug).
        opened = {}
        real_open = resume_mod._open_store_with_migration

        def _capture_open(p, **kw):
            store = real_open(p, **kw)
            opened["store"] = store
            return store

        monkeypatch.setattr(resume_mod, "_open_store_with_migration", _capture_open)

        def _boom(_cfg_path):
            raise RuntimeError("recipe load failed")

        monkeypatch.setattr(resume_mod, "load_terrarium_config", _boom)

        t = await TestTerrariumBuilder().build()
        try:
            with pytest.raises(RuntimeError, match="recipe load failed"):
                await resume_mod.resume_into_engine(t, path)
            # The store the resume opened was closed (writer lock released).
            assert getattr(opened["store"], "_closed", False) is True
            # A fresh writer can reopen the file.
            reopened = SessionStore(str(path), writer_lock=True)
            reopened.close()
        finally:
            await t.shutdown()

    async def test_terrarium_resume_inject_failure_rolls_back(
        self, monkeypatch, tmp_path
    ):
        # A failure DURING per-creature injection must both release the
        # store's writer lock AND remove the creatures this adoption
        # already added — a half-resumed graph must not survive.
        path = self._write_terrarium_session(tmp_path)

        fake_config = TerrariumConfig(name="t", creatures=[], channels=[])
        monkeypatch.setattr(resume_mod, "load_terrarium_config", lambda p: fake_config)

        opened = {}
        real_open = resume_mod._open_store_with_migration

        def _capture_open(p, **kw):
            store = real_open(p, **kw)
            opened["store"] = store
            return store

        monkeypatch.setattr(resume_mod, "_open_store_with_migration", _capture_open)

        engine_holder = {}

        async def _fake_apply_recipe(config, pwd=None, created_ids=None, **_):
            t = engine_holder["t"]
            agent = _FakeAgent(name="alice")
            agent.attach_session_store = lambda s: None
            c = Creature(
                creature_id="alice",
                name="alice",
                agent=agent,
                config=agent.config,
            )
            added = await t.add_creature(c, start=False)
            if created_ids is not None:
                created_ids.append(added.creature_id)
            return t._topology.graphs[c.graph_id]

        def _boom(agent, store, name):
            raise RuntimeError("inject failed")

        monkeypatch.setattr(resume_mod, "inject_saved_state", _boom)

        t = await TestTerrariumBuilder().build()
        engine_holder["t"] = t
        t.apply_recipe = _fake_apply_recipe
        try:
            with pytest.raises(RuntimeError, match="inject failed"):
                await resume_mod.resume_into_engine(t, path)
            # The creature the failed adoption added was rolled back.
            assert "alice" not in t._creatures
            # The store the resume opened was closed (writer lock released).
            assert getattr(opened["store"], "_closed", False) is True
            reopened = SessionStore(str(path), writer_lock=True)
            reopened.close()
        finally:
            await t.shutdown()

    async def test_terrarium_resume_rollback_spares_concurrent_creature(
        self, monkeypatch, tmp_path
    ):
        # Reviewer repro: a creature a CONCURRENT task adds mid-adoption
        # must NOT be swept by the failed adoption's rollback. Only the
        # exact ids this adoption created are removed — never a global
        # before/after diff of the engine's creatures.
        import kohakuterrarium.terrarium.topology as _topo

        path = self._write_terrarium_session(tmp_path)

        fake_config = TerrariumConfig(name="t", creatures=[], channels=[])
        monkeypatch.setattr(resume_mod, "load_terrarium_config", lambda p: fake_config)

        opened = {}
        real_open = resume_mod._open_store_with_migration

        def _capture_open(p, **kw):
            store = real_open(p, **kw)
            opened["store"] = store
            return store

        monkeypatch.setattr(resume_mod, "_open_store_with_migration", _capture_open)

        engine_holder = {}

        async def _fake_apply_recipe(config, pwd=None, created_ids=None, **_):
            t = engine_holder["t"]
            agent = _FakeAgent(name="alice")
            agent.attach_session_store = lambda s: None
            c = Creature(
                creature_id="alice",
                name="alice",
                agent=agent,
                config=agent.config,
            )
            added = await t.add_creature(c, start=False)
            if created_ids is not None:
                created_ids.append(added.creature_id)
            return t._topology.graphs[c.graph_id]

        def _boom_inject(agent, store, name):
            # Stand in for a concurrent task landing an unrelated creature
            # after this adoption's own creature was added.
            t = engine_holder["t"]
            cc_agent = _FakeAgent(name="concurrent")
            cc = Creature(
                creature_id="concurrent",
                name="concurrent",
                agent=cc_agent,
                config=cc_agent.config,
            )
            cc.graph_id = _topo.add_creature(t._topology, "concurrent")
            t._creatures["concurrent"] = cc
            raise RuntimeError("inject failed")

        monkeypatch.setattr(resume_mod, "inject_saved_state", _boom_inject)

        t = await TestTerrariumBuilder().with_creature("preexisting").build()
        engine_holder["t"] = t
        t.apply_recipe = _fake_apply_recipe
        try:
            with pytest.raises(RuntimeError, match="inject failed"):
                await resume_mod.resume_into_engine(t, path)
            # (a) the adoption's own creature is rolled back;
            assert "alice" not in t._creatures
            # (b) the concurrent creature SURVIVES;
            assert "concurrent" in t._creatures
            # (c) the pre-existing creature survives;
            assert "preexisting" in t._creatures
            # (d) the store's writer lock was released.
            assert getattr(opened["store"], "_closed", False) is True
        finally:
            await t.shutdown()

    # ── Drive reconcile wiring (Phase F, design §6.5) ─────────────

    def test_schedule_drive_reconcile_calls_runtime(self):
        calls = []
        engine = SimpleNamespace(
            _drive_runtime=SimpleNamespace(schedule_reconcile=calls.append)
        )
        creature = object()
        resume_mod._schedule_drive_reconcile(engine, creature)
        assert calls == [creature]

    def test_schedule_drive_reconcile_noop_when_disabled(self):
        # A Drive-disabled engine must not raise.
        resume_mod._schedule_drive_reconcile(
            SimpleNamespace(_drive_runtime=None), object()
        )

    async def test_agent_resume_schedules_drive_reconcile(self, monkeypatch, tmp_path):
        # Resume starts creatures directly (not via add_creature), so it must
        # itself arm the restoration-barrier-gated Drive reconcile (§6.5).
        from kohakuterrarium.terrarium.drive.config import (
            DriveRuntimeConfig,
            default_registrations,
        )
        from kohakuterrarium.terrarium.engine import Terrarium

        monkeypatch.setattr(resume_mod, "detect_session_type", lambda p: "agent")
        fake_agent = _FakeAgent(name="alice")
        fake_agent.config = SimpleNamespace(name="alice")
        fake_store = SimpleNamespace(
            path=str(tmp_path / "a.kohakutr"),
            set_conversation_open=lambda _value: None,
            update_status=lambda _value: None,
            checkpoint=lambda: None,
        )
        monkeypatch.setattr(
            resume_mod, "resume_agent", lambda *a, **k: (fake_agent, fake_store)
        )
        monkeypatch.setattr(
            resume_mod._checkpoint, "checkpoint", AsyncMock(return_value=True)
        )

        t = Terrarium(
            drive_config=DriveRuntimeConfig(enabled=True),
            drive_registrations=default_registrations(),
        )
        await t.__aenter__()
        calls: list = []
        t._drive_runtime.schedule_reconcile = calls.append
        t.attach_session = AsyncMock()  # fake_store has no real Drive tables
        try:
            await resume_mod.resume_into_engine(t, tmp_path / "a.kohakutr")
            # Exactly one creature resumed -> one reconcile armed.
            assert len(calls) == 1
        finally:
            await t.shutdown()

    async def test_agent_resume_rollback_when_start_fails(self, monkeypatch, tmp_path):
        # Standalone-agent resume: ``add_creature`` inserts the creature
        # into the topology + ``_creatures`` BEFORE awaiting startup. If
        # ``start()`` fails, the creature must still be rolled back — the
        # id is recorded at insertion, not after ``add_creature`` returns.
        monkeypatch.setattr(resume_mod, "detect_session_type", lambda p: "agent")
        # Deterministic id so the leak is observable — the default mints a
        # random suffix, which would make an ``"alice" in`` check vacuous.
        monkeypatch.setattr(resume_mod, "_safe_creature_id", lambda name: name)

        path = tmp_path / "agent.kohakutr.v2"
        store = SessionStore(str(path), writer_lock=True)
        store.init_meta("agent", "agent", "/cfg", str(tmp_path), ["alice"])
        store.set_conversation_open(False)
        store.update_status("completed")

        fake_agent = _FakeAgent(name="alice")
        fake_agent.config = SimpleNamespace(name="alice")

        async def _boom_start():
            raise RuntimeError("start failed")

        fake_agent.start = _boom_start

        def _resume_agent(
            path,
            pwd_override=None,
            io_mode=None,
            llm=None,
            *,
            input_module=None,
            output_module=None,
            mark_conversation_open=True,
        ):
            captured["mark_conversation_open"] = mark_conversation_open
            if mark_conversation_open:
                store.set_conversation_open(True)
                store.update_status("running")
            return fake_agent, store

        monkeypatch.setattr(resume_mod, "resume_agent", _resume_agent)
        captured = {}

        t = await TestTerrariumBuilder().with_creature("preexisting").build()
        try:
            with pytest.raises(RuntimeError, match="start failed"):
                await resume_mod.resume_into_engine(t, tmp_path / "agent.kohakutr.v2")
            # The half-adopted creature was rolled back; only the
            # pre-existing creature remains.
            assert "alice" not in t._creatures
            assert set(t._creatures) == {"preexisting"}
            # The store's writer lock was released.
            assert getattr(store, "_closed", False) is True
            assert captured["mark_conversation_open"] is False
            reopened = SessionStore.open_readonly(path)
            try:
                meta = reopened.load_meta()
                assert bool(meta["conversation_open"]) is False
                assert meta["status"] == "completed"
            finally:
                reopened.close()
        finally:
            await t.shutdown()

    async def test_terrarium_resume_failure_preserves_closed_lifecycle(
        self, monkeypatch, tmp_path
    ):
        path = tmp_path / "team.kohakutr"
        store = SessionStore(path)
        store.init_meta("team", "terrarium", "/cfg/team.yaml", str(tmp_path), ["root"])
        store.set_conversation_open(False)
        store.update_status("completed")
        store.close(update_status=False)

        monkeypatch.setattr(
            resume_mod,
            "load_terrarium_config",
            lambda _path: TerrariumConfig(name="team", creatures=[], channels=[]),
        )

        async def _fail_replay(engine, graph_id):  # noqa: ARG001
            raise RuntimeError("topology replay failed")

        monkeypatch.setattr(resume_mod._topo_snap, "replay", _fail_replay)

        engine = await TestTerrariumBuilder().build()
        try:
            with pytest.raises(RuntimeError, match="topology replay failed"):
                await resume_mod.resume_into_engine(engine, path)
            reopened = SessionStore.open_readonly(path)
            try:
                meta = reopened.load_meta()
                assert bool(meta["conversation_open"]) is False
                assert meta["status"] == "completed"
            finally:
                reopened.close()
        finally:
            await engine.shutdown()
