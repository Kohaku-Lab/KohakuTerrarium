"""Additional coverage tests for :mod:`kohakuterrarium.studio.sessions.lifecycle`.

Targets the branches the existing tests miss — package-ref resolution,
agent-list meta updates, channel-persistence retro-install, remote
stop_session swallow paths, find_creature root disambiguation.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kohakuterrarium.studio.sessions import lifecycle
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.service import LocalTerrariumService, CreatureInfo
from kohakuterrarium.testing.terrarium import TestTerrariumBuilder, _FakeAgent
from kohakuterrarium.terrarium.creature_host import Creature


@pytest.fixture(autouse=True)
def _reset_module_state():
    lifecycle._meta.clear()
    lifecycle._session_stores.clear()
    yield
    lifecycle._meta.clear()
    lifecycle._session_stores.clear()


# ── start_creature: package_ref + name application (96-98, 114) ──


class TestStartCreaturePackageRef:
    async def test_local_package_ref_resolution(self, monkeypatch):
        engine = Terrarium()
        svc = LocalTerrariumService(engine)
        agent = _FakeAgent(name="alice")
        agent.config = SimpleNamespace(name="alice")
        agent.session_store = None
        agent.executor = None
        agent.attach_session_store = lambda s: None
        fake_creature = Creature(
            creature_id="cid-x",
            name="alice",
            agent=agent,
            graph_id="g1",
            config=agent.config,
            is_privileged=True,
        )
        captured = {}

        async def _fake_add(cfg, *args, **kw):
            captured["cfg"] = cfg
            return fake_creature

        engine.add_creature = _fake_add
        engine._session_stores = {}
        engine._environments = {}
        engine.list_graphs = lambda: [
            SimpleNamespace(graph_id="g1", creature_ids={"cid-x"})
        ]
        engine.get_creature = lambda cid: fake_creature
        engine._creatures = {"cid-x": fake_creature}

        monkeypatch.setattr(lifecycle, "is_package_ref", lambda p: True)
        monkeypatch.setattr(lifecycle, "resolve_package_path", lambda p: "/resolved")
        monkeypatch.setattr(
            lifecycle,
            "SessionStore",
            lambda p: SimpleNamespace(
                meta={},
                init_meta=lambda **kw: None,
                close=lambda: None,
            ),
        )
        try:
            sess = await lifecycle.start_creature(
                svc, config_path="@pkg/x", name="renamed"
            )
            # Verified that the package ref got resolved.
            assert captured["cfg"] == "/resolved"
            # Name was applied.
            assert sess.name == "renamed"
        finally:
            await engine.shutdown()

    async def test_remote_package_ref_resolution(self, monkeypatch):
        engine = Terrarium()
        svc = LocalTerrariumService(engine)
        captured = {}

        async def _fake_add(cfg, **kw):
            captured["cfg"] = cfg
            captured["on_node"] = kw.get("on_node")
            return CreatureInfo(
                creature_id="cid-r",
                name="r",
                graph_id="g-r",
                is_running=True,
                is_privileged=False,
                parent_creature_id=None,
                listen_channels=(),
                send_channels=(),
            )

        svc.add_creature = _fake_add
        monkeypatch.setattr(lifecycle, "is_package_ref", lambda p: True)
        monkeypatch.setattr(
            lifecycle, "resolve_package_path", lambda p: "/resolved-remote"
        )
        try:
            await lifecycle.start_creature(
                svc, config_path="@pkg/x", on_node="worker-1"
            )
            assert captured["cfg"] == "/resolved-remote"
            assert captured["on_node"] == "worker-1"
        finally:
            await engine.shutdown()

    async def test_remote_missing_config_raises(self):
        engine = Terrarium()
        svc = LocalTerrariumService(engine)
        try:
            with pytest.raises(ValueError):
                await lifecycle.start_creature(svc, on_node="worker-1")
        finally:
            await engine.shutdown()


# ── attach_session_store_for_creature meta updates (214-219) ──


class TestAttachMetaUpdates:
    async def test_appends_new_agent_to_meta(self, tmp_path):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        svc = LocalTerrariumService(t)
        try:
            from kohakuterrarium.session.store import SessionStore

            creature = t.get_creature("alice")
            creature.agent.config = SimpleNamespace(name="alice")
            creature.agent.attach_session_store = lambda s: None
            sid = creature.graph_id
            store = SessionStore(str(tmp_path / "p.kohakutr"))
            store.init_meta(
                session_id=sid,
                config_type="agent",
                config_path="",
                pwd="",
                agents=["bob"],
            )
            t._session_stores[sid] = store
            try:
                lifecycle.attach_session_store_for_creature(svc, creature)
                agents = store.meta["agents"]
                assert "alice" in agents
                assert "bob" in agents
                # Two agents → promote to terrarium.
                assert store.meta["config_type"] == "terrarium"
            finally:
                store.close()
        finally:
            await t.shutdown()

    async def test_skips_when_agent_already_listed(self, tmp_path):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        svc = LocalTerrariumService(t)
        try:
            from kohakuterrarium.session.store import SessionStore

            creature = t.get_creature("alice")
            creature.agent.config = SimpleNamespace(name="alice")
            creature.agent.attach_session_store = lambda s: None
            sid = creature.graph_id
            store = SessionStore(str(tmp_path / "p.kohakutr"))
            store.init_meta(
                session_id=sid,
                config_type="agent",
                config_path="",
                pwd="",
                agents=["alice"],
            )
            t._session_stores[sid] = store
            try:
                lifecycle.attach_session_store_for_creature(svc, creature)
                # Agent list unchanged.
                assert store.meta["agents"] == ["alice"]
            finally:
                store.close()
        finally:
            await t.shutdown()


# ── _retro_install_channel_persistence with live channels (251) ──


class TestRetroInstall:
    async def test_walks_registered_channels(self):
        t = await (
            TestTerrariumBuilder().with_creature("alice").with_channel("chat").build()
        )
        try:
            sid = t.get_creature("alice").graph_id
            # Should not raise — exercises the loop body.
            lifecycle._retro_install_channel_persistence(t, sid)
        finally:
            await t.shutdown()

    def test_no_env_returns(self):
        engine = SimpleNamespace(_environments={})
        # No env for sid → early return.
        lifecycle._retro_install_channel_persistence(engine, "ghost")


# ── start_terrarium package ref + config (276-278) ───────────


class TestStartTerrariumPackageRef:
    async def test_package_ref_resolves(self, monkeypatch, tmp_path):
        engine = Terrarium()
        svc = LocalTerrariumService(engine)
        from kohakuterrarium.terrarium.config import TerrariumConfig

        cfg = TerrariumConfig(name="t", creatures=[], channels=[])
        captured = {}

        monkeypatch.setattr(lifecycle, "is_package_ref", lambda p: True)
        monkeypatch.setattr(
            lifecycle, "resolve_package_path", lambda p: "/resolved/recipe.yaml"
        )
        monkeypatch.setattr(
            lifecycle,
            "load_terrarium_config",
            lambda p: (captured.setdefault("path", p), cfg)[1],
        )
        monkeypatch.setattr(lifecycle, "_session_dir", lambda: str(tmp_path))

        async def _apply(c, pwd=None, llm_override=None):
            return SimpleNamespace(graph_id="g-new", creature_ids=set())

        engine.apply_recipe = _apply
        engine.attach_session = AsyncMock()
        engine.list_graphs = lambda: [
            SimpleNamespace(graph_id="g-new", creature_ids=set())
        ]
        engine._environments = {}
        try:
            await lifecycle.start_terrarium(svc, config_path="@pkg/t")
            assert captured["path"] == "/resolved/recipe.yaml"
        finally:
            await engine.shutdown()


# ── list_sessions filter: on_node None (368) ──────────────────


class TestListSessionsFilter:
    async def test_meta_without_on_node_is_skipped(self):
        t = await TestTerrariumBuilder().build()
        svc = LocalTerrariumService(t)
        try:
            # A meta entry with no on_node should NOT appear in list.
            lifecycle._meta["sid-x"] = {"name": "n"}
            out = lifecycle.list_sessions(svc)
            assert not any(s.session_id == "sid-x" for s in out)
        finally:
            await t.shutdown()


# ── rename_session creature lookup error (463-464) ────────────


class TestRenameSessionCreatureMissing:
    async def test_creature_key_error_skipped(self, monkeypatch):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        svc = LocalTerrariumService(t)
        try:
            gid = t.get_creature("alice").graph_id

            def _boom(cid):
                raise KeyError(cid)

            t.get_creature = _boom
            # Should swallow KeyError and continue.
            sess = lifecycle.rename_session(svc, gid, "new-name")
            assert sess.name == "new-name"
        finally:
            await t.shutdown()


# ── stop_session swallow paths (514-515, 527-530) ─────────────


class TestStopSessionSwallow:
    async def test_local_remove_creature_key_error_swallowed(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        svc = LocalTerrariumService(t)
        try:
            gid = t.get_creature("alice").graph_id

            async def _boom(cid):
                raise KeyError(cid)

            t.remove_creature = _boom
            # Should not raise — swallows internal KeyError.
            await lifecycle.stop_session(svc, gid)
            assert gid not in lifecycle._meta
        finally:
            await t.shutdown()

    async def test_remote_remove_creature_key_error_swallowed(self):
        t = await TestTerrariumBuilder().build()
        svc = LocalTerrariumService(t)

        async def _boom(cid):
            raise KeyError(cid)

        svc.remove_creature = _boom
        try:
            lifecycle._meta["sid-r"] = {
                "on_node": "worker-1",
                "creature_id": "cid-r",
            }
            # KeyError on worker is swallowed; meta is still cleaned.
            await lifecycle.stop_session(svc, "sid-r")
            assert "sid-r" not in lifecycle._meta
        finally:
            await t.shutdown()


# ── add_creature returns id (549-550) ─────────────────────────


class TestAddCreatureSuccess:
    async def test_returns_creature_id(self):
        from kohakuterrarium.terrarium.config import CreatureConfig

        t = await TestTerrariumBuilder().with_creature("alice").build()
        svc = LocalTerrariumService(t)
        try:
            gid = t.get_creature("alice").graph_id

            async def _add(cfg, graph=None, **kw):
                return Creature(
                    creature_id="cid-new",
                    name="new",
                    agent=_FakeAgent(name="new"),
                )

            t.add_creature = _add
            cfg = CreatureConfig(
                name="new", config_data={"name": "new"}, base_dir=Path(".")
            )
            out = await lifecycle.add_creature(svc, gid, cfg)
            assert out == "cid-new"
        finally:
            await t.shutdown()


# ── list_creatures KeyError continue (583-584, 587) ───────────


class TestListCreaturesKeyError:
    async def test_skips_missing_creature(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        svc = LocalTerrariumService(t)
        try:
            gid = t.get_creature("alice").graph_id
            # Make get_creature raise so the for-loop skips.
            original = t.get_creature

            def _missing(cid):
                raise KeyError(cid)

            t.get_creature = _missing
            out = lifecycle.list_creatures(svc, gid)
            # All entries skipped → empty.
            assert out == []
            t.get_creature = original
        finally:
            await t.shutdown()

    async def test_host_node_fallback_via_node_id(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        svc = LocalTerrariumService(t)
        try:
            gid = t.get_creature("alice").graph_id
            out = lifecycle.list_creatures(svc, gid)
            # default_home falls back to node_id ("_host").
            assert out[0]["home_node"] == "_host"
        finally:
            await t.shutdown()


# ── _build_session_handle KeyError continue (647-648) ─────────


class TestBuildSessionHandleKeyError:
    async def test_skips_missing_creature(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            gid = t.get_creature("alice").graph_id

            def _missing(cid):
                raise KeyError(cid)

            t.get_creature = _missing
            sess = lifecycle._build_session_handle(t, gid)
            assert sess.creatures == []
        finally:
            await t.shutdown()


# ── find_creature: list_all not callable (728-729) ────────────


class TestFindCreatureNoListAll:
    async def test_session_underscore_no_list_creatures(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        svc = LocalTerrariumService(t)
        try:
            expected = t.get_creature("alice")
            # Replace list_creatures with a non-callable so the
            # ``callable(list_all)`` check flips to False.
            t.list_creatures = "not-callable"
            # Falls back to direct get_creature (id match).
            c = lifecycle.find_creature(svc, "_", "alice")
            assert c is expected
        finally:
            await t.shutdown()


# ── find_creature root disambiguation (758-773) ───────────────


class TestFindCreatureRoot:
    async def test_root_by_creature_id(self):
        # Build a terrarium where the privileged creature has the literal
        # creature_id "root".
        from kohakuterrarium.terrarium.creature_host import Creature

        t = await TestTerrariumBuilder().with_creature("alice").build()
        svc = LocalTerrariumService(t)
        try:
            gid = t.get_creature("alice").graph_id
            root_creature = Creature(
                creature_id="root",
                name="not-root-name",
                agent=_FakeAgent(name="not-root-name"),
                is_privileged=True,
            )
            await t.add_creature(root_creature, graph=gid)
            # Lookup "root" must return the one with id "root".
            c = lifecycle.find_creature(svc, gid, "root")
            assert c.creature_id == "root"
        finally:
            await t.shutdown()

    async def test_root_by_name(self):
        from kohakuterrarium.terrarium.creature_host import Creature

        t = await TestTerrariumBuilder().with_creature("alice").build()
        svc = LocalTerrariumService(t)
        try:
            gid = t.get_creature("alice").graph_id
            # Privilege bob and name him root.
            root_creature = Creature(
                creature_id="bob-cid",
                name="root",
                agent=_FakeAgent(name="root"),
                is_privileged=True,
            )
            await t.add_creature(root_creature, graph=gid)
            c = lifecycle.find_creature(svc, gid, "root")
            assert c.name == "root"
        finally:
            await t.shutdown()

    async def test_root_falls_back_to_first_privileged(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        svc = LocalTerrariumService(t)
        try:
            gid = t.get_creature("alice").graph_id
            alice = t.get_creature("alice")
            alice.is_privileged = True
            # alice doesn't have creature_id="root" or name="root", so
            # the resolver should fall through to the sorted-first
            # privileged creature.
            c = lifecycle.find_creature(svc, gid, "root")
            assert c is alice
        finally:
            await t.shutdown()

    async def test_root_unknown_when_no_privileged(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        svc = LocalTerrariumService(t)
        try:
            gid = t.get_creature("alice").graph_id
            with pytest.raises(KeyError):
                lifecycle.find_creature(svc, gid, "root")
        finally:
            await t.shutdown()
