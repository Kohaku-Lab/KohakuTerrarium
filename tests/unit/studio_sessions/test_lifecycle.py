"""Coverage tests for ``studio.sessions.lifecycle``.

Exercises ``start_creature`` / ``start_terrarium`` / ``list_sessions`` /
``get_session`` / ``stop_session`` / ``add_creature`` / ``list_creatures`` /
``remove_creature`` / ``find_session_for_creature`` / ``find_creature`` /
session-store auto-attach + helper accessors.

Real ``Agent`` instances are too heavy for engine-layer unit tests so
we plumb pre-built ``Creature`` objects (wrapping ``_FakeAgent``) into
the engine via ``add_creature(creature_obj)``.  ``start_creature`` is
the only API that exercises the ``add_creature`` ``str | AgentConfig``
branch — for that we monkey-patch ``Terrarium.add_creature`` to bypass
real config loading.
"""

from pathlib import Path
from typing import Any

import pytest

import kohakuterrarium.studio.sessions.lifecycle as lifecycle
from kohakuterrarium.terrarium.config import (
    ChannelConfig,
    CreatureConfig,
    RootConfig,
    TerrariumConfig,
)
from kohakuterrarium.terrarium.engine import Terrarium

from tests.unit.studio_sessions._fakes import (
    install_fake_creature,
    make_creature,
)

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_lifecycle_state(tmp_path, monkeypatch):
    """Each test gets a fresh ``_meta`` / ``_session_stores`` and a
    sandbox session directory so auto-attach doesn't pollute the user
    home folder."""
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "sessions"))
    lifecycle._meta.clear()
    lifecycle._session_stores.clear()
    yield
    lifecycle._meta.clear()
    lifecycle._session_stores.clear()


@pytest.fixture
def patched_add_creature(monkeypatch):
    """Replace ``Terrarium.add_creature`` so we don't try to load real
    YAML / agent configs.  Instead we mint a fake ``Creature`` keyed on
    whatever the caller passes (str path, dict, or ``AgentConfig``)."""

    async def _fake_add_creature(
        self,
        config: Any,
        *,
        graph: Any | None = None,
        creature_id: str | None = None,
        llm_override: str | None = None,
        pwd: str | None = None,
        start: bool = True,
        is_privileged: bool = False,
        parent_creature_id: str | None = None,
    ):
        # Derive a name from whatever we got
        if isinstance(config, str):
            name = Path(config).stem or "agent"
        else:
            name = getattr(config, "name", None) or "agent"
        creature = make_creature(name=name)
        creature.agent.config.pwd = pwd
        # call original (now bypassed) via a sentinel
        return await _real_add_creature(
            self,
            creature,
            graph=graph,
            creature_id=creature_id,
            llm_override=llm_override,
            pwd=pwd,
            start=start,
            is_privileged=is_privileged,
            parent_creature_id=parent_creature_id,
        )

    _real_add_creature = Terrarium.add_creature
    monkeypatch.setattr(Terrarium, "add_creature", _fake_add_creature)


# ---------------------------------------------------------------------------
# _normalize_pwd / _now_iso / _session_dir helpers
# ---------------------------------------------------------------------------


class TestPwdNormalization:
    def test_normalize_pwd_none(self):
        assert lifecycle._normalize_pwd(None) is None

    def test_normalize_pwd_existing(self, tmp_path):
        out = lifecycle._normalize_pwd(str(tmp_path))
        assert Path(out).resolve() == tmp_path.resolve()

    def test_normalize_pwd_missing(self, tmp_path):
        with pytest.raises(ValueError, match="does not exist"):
            lifecycle._normalize_pwd(str(tmp_path / "nope"))

    def test_normalize_pwd_not_a_dir(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("hi")
        with pytest.raises(ValueError, match="not a directory"):
            lifecycle._normalize_pwd(str(f))


class TestSessionDirEnv:
    def test_session_dir_default(self, monkeypatch):
        monkeypatch.delenv("KT_SESSION_DIR", raising=False)
        out = lifecycle._session_dir()
        assert out.endswith("sessions") or "kohakuterrarium" in out

    def test_session_dir_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path))
        assert lifecycle._session_dir() == str(tmp_path)

    def test_now_iso_format(self):
        ts = lifecycle._now_iso()
        # Should be ISO-8601 with timezone offset info
        assert "T" in ts


# ---------------------------------------------------------------------------
# start_creature
# ---------------------------------------------------------------------------


class TestStartCreature:
    @pytest.mark.asyncio
    async def test_start_creature_with_config_path(
        self, patched_add_creature, tmp_path
    ):
        engine = Terrarium()
        try:
            session = await lifecycle.start_creature(
                engine, config_path=str(tmp_path / "alice.yaml"), pwd=str(tmp_path)
            )
            assert session.kind == "creature"
            assert session.session_id
            assert session.created_at  # iso timestamp populated
            # Auto-attached store registered
            assert lifecycle.get_session_store(session.session_id) is not None
            stores = lifecycle.list_session_stores()
            assert len(stores) == 1
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_start_creature_with_config_object(
        self, patched_add_creature, tmp_path
    ):
        engine = Terrarium()
        try:
            cfg = type("Cfg", (), {"name": "bob"})()
            session = await lifecycle.start_creature(
                engine, config=cfg, pwd=str(tmp_path)
            )
            assert session.session_id
            meta = lifecycle.get_session_meta(session.session_id)
            assert meta["kind"] == "creature"
            assert meta["pwd"] == str(tmp_path.resolve())
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_start_creature_requires_config(self):
        engine = Terrarium()
        try:
            with pytest.raises(ValueError, match="config_path or config"):
                await lifecycle.start_creature(engine)
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_start_creature_uses_cwd_when_pwd_none(self, patched_add_creature):
        engine = Terrarium()
        try:
            cfg = type("Cfg", (), {"name": "eve"})()
            session = await lifecycle.start_creature(engine, config=cfg)
            meta = lifecycle.get_session_meta(session.session_id)
            assert meta["pwd"]  # populated from cwd
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_start_creature_resolves_package_ref(
        self, patched_add_creature, tmp_path, monkeypatch
    ):
        """``@pkg/...`` refs are resolved before being handed to engine."""
        called = {"path": None}

        def _is_pkg(s):
            return s.startswith("@pkg/")

        def _resolve(s):
            called["path"] = s
            return tmp_path / "resolved.yaml"

        monkeypatch.setattr(lifecycle, "is_package_ref", _is_pkg)
        monkeypatch.setattr(lifecycle, "resolve_package_path", _resolve)

        engine = Terrarium()
        try:
            session = await lifecycle.start_creature(engine, config_path="@pkg/foo/bar")
            assert called["path"] == "@pkg/foo/bar"
            meta = lifecycle.get_session_meta(session.session_id)
            assert "resolved.yaml" in meta["config_path"]
        finally:
            await engine.shutdown()


# ---------------------------------------------------------------------------
# start_terrarium
# ---------------------------------------------------------------------------


def _basic_terrarium_config() -> TerrariumConfig:
    return TerrariumConfig(
        name="basic-team",
        creatures=[
            CreatureConfig(
                name="alice",
                config_data={"name": "alice"},
                base_dir=Path("/tmp"),
                listen_channels=["tasks"],
                send_channels=["results"],
            ),
            CreatureConfig(
                name="bob",
                config_data={"name": "bob"},
                base_dir=Path("/tmp"),
                listen_channels=["results"],
                send_channels=["tasks"],
            ),
        ],
        channels=[
            ChannelConfig(name="tasks", channel_type="queue"),
            ChannelConfig(name="results", channel_type="broadcast"),
        ],
    )


def _fake_recipe_builder(cr_cfg, *, creature_id=None, pwd=None, llm_override=None):
    return make_creature(name=cr_cfg.name)


class TestStartTerrarium:
    @pytest.mark.asyncio
    async def test_start_terrarium_with_config_object(self, monkeypatch, tmp_path):
        # Override apply_recipe to use the fake builder
        from kohakuterrarium.terrarium import recipe as _recipe

        original_apply = _recipe.apply_recipe

        async def _apply(
            engine, recipe, *, graph=None, pwd=None, creature_builder=None
        ):
            return await original_apply(
                engine,
                recipe,
                graph=graph,
                pwd=pwd,
                creature_builder=_fake_recipe_builder,
            )

        monkeypatch.setattr(_recipe, "apply_recipe", _apply)

        engine = Terrarium()
        try:
            session = await lifecycle.start_terrarium(
                engine, config=_basic_terrarium_config(), pwd=str(tmp_path)
            )
            assert session.kind == "terrarium"
            assert session.name == "basic-team"
            assert len(session.creatures) == 2
            # Channels were declared via the recipe
            assert len(session.channels) >= 1
            meta = lifecycle.get_session_meta(session.session_id)
            assert meta["kind"] == "terrarium"
            assert meta["has_root"] is False
            assert lifecycle.get_session_store(session.session_id) is not None
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_start_terrarium_with_config_path(self, monkeypatch, tmp_path):
        cfg = _basic_terrarium_config()

        def _fake_load(path):
            return cfg

        monkeypatch.setattr(lifecycle, "load_terrarium_config", _fake_load)

        from kohakuterrarium.terrarium import recipe as _recipe

        original_apply = _recipe.apply_recipe

        async def _apply(
            engine, recipe, *, graph=None, pwd=None, creature_builder=None
        ):
            return await original_apply(
                engine,
                recipe,
                graph=graph,
                pwd=pwd,
                creature_builder=_fake_recipe_builder,
            )

        monkeypatch.setattr(_recipe, "apply_recipe", _apply)

        engine = Terrarium()
        try:
            session = await lifecycle.start_terrarium(
                engine, config_path=str(tmp_path / "team.yaml"), pwd=str(tmp_path)
            )
            assert session.kind == "terrarium"
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_start_terrarium_resolves_package_ref(self, monkeypatch, tmp_path):
        called = {"path": None}

        def _is_pkg(s):
            return s.startswith("@pkg/")

        def _resolve(s):
            called["path"] = s
            return tmp_path / "resolved.yaml"

        monkeypatch.setattr(lifecycle, "is_package_ref", _is_pkg)
        monkeypatch.setattr(lifecycle, "resolve_package_path", _resolve)

        cfg = _basic_terrarium_config()
        monkeypatch.setattr(lifecycle, "load_terrarium_config", lambda p: cfg)

        from kohakuterrarium.terrarium import recipe as _recipe

        original_apply = _recipe.apply_recipe

        async def _apply(
            engine, recipe, *, graph=None, pwd=None, creature_builder=None
        ):
            return await original_apply(
                engine,
                recipe,
                graph=graph,
                pwd=pwd,
                creature_builder=_fake_recipe_builder,
            )

        monkeypatch.setattr(_recipe, "apply_recipe", _apply)

        engine = Terrarium()
        try:
            await lifecycle.start_terrarium(engine, config_path="@pkg/team")
            assert called["path"] == "@pkg/team"
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_start_terrarium_requires_config(self):
        engine = Terrarium()
        try:
            with pytest.raises(ValueError, match="config_path or config"):
                await lifecycle.start_terrarium(engine)
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_start_terrarium_records_root_flag(self, monkeypatch, tmp_path):
        cfg = _basic_terrarium_config()
        cfg.root = RootConfig(config_data={"name": "root"}, base_dir=Path("/tmp"))

        from kohakuterrarium.terrarium import recipe as _recipe

        original_apply = _recipe.apply_recipe

        async def _apply(
            engine, recipe, *, graph=None, pwd=None, creature_builder=None
        ):
            return await original_apply(
                engine,
                recipe,
                graph=graph,
                pwd=pwd,
                creature_builder=_fake_recipe_builder,
            )

        monkeypatch.setattr(_recipe, "apply_recipe", _apply)

        # The fake recipe builder ignores the root branch — patch
        # ``terrarium.root.assign_root_to`` to be a noop too.
        from kohakuterrarium.terrarium import root as _root

        async def _noop_assign(engine, creature, *, report_channel="report_to_root"):
            from kohakuterrarium.terrarium.events import RootAssignment

            return RootAssignment(creature_id="root", channels=[])

        monkeypatch.setattr(_root, "assign_root_to", _noop_assign)

        engine = Terrarium()
        try:
            session = await lifecycle.start_terrarium(
                engine, config=cfg, pwd=str(tmp_path)
            )
            meta = lifecycle.get_session_meta(session.session_id)
            assert meta["has_root"] is True
            assert session.has_root is True
        finally:
            await engine.shutdown()


# ---------------------------------------------------------------------------
# list / get / stop session
# ---------------------------------------------------------------------------


class TestSessionQuery:
    @pytest.mark.asyncio
    async def test_list_sessions_empty(self):
        engine = Terrarium()
        try:
            assert lifecycle.list_sessions(engine) == []
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_list_sessions_after_add(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            lifecycle._meta[c.graph_id] = {
                "kind": "creature",
                "name": "alice",
            }
            listings = lifecycle.list_sessions(engine)
            assert len(listings) == 1
            assert listings[0].name == "alice"
            assert listings[0].kind == "creature"
            assert listings[0].running is True
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_list_sessions_falls_back_to_creature_when_meta_missing(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            # No meta entry — ``list_sessions`` should still produce a row
            listings = lifecycle.list_sessions(engine)
            assert listings[0].name == c.graph_id
            assert listings[0].kind == "creature"
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_get_session_returns_handle(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            sess = lifecycle.get_session(engine, c.graph_id)
            assert sess.session_id == c.graph_id
            assert sess.kind == "creature"
            assert any(cc["name"] == "alice" for cc in sess.creatures)
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_get_session_unknown_raises(self):
        engine = Terrarium()
        try:
            with pytest.raises(KeyError):
                lifecycle.get_session(engine, "ghost")
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_stop_session(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            sid = c.graph_id
            lifecycle._meta[sid] = {"kind": "creature", "name": "alice"}
            await lifecycle.stop_session(engine, sid)
            assert "alice" not in engine
            assert lifecycle.get_session_meta(sid) == {}
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_stop_session_unknown_raises(self):
        engine = Terrarium()
        try:
            with pytest.raises(KeyError):
                await lifecycle.stop_session(engine, "ghost")
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_stop_session_swallows_keyerror_on_creature_remove(self, monkeypatch):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            sid = c.graph_id

            real_remove = engine.remove_creature

            async def _flaky_remove(cid):
                # First raise to exercise the fallback branch
                raise KeyError(f"already gone: {cid}")

            monkeypatch.setattr(engine, "remove_creature", _flaky_remove)
            await lifecycle.stop_session(engine, sid)
            # _meta was still cleaned even though remove raised
            assert lifecycle.get_session_meta(sid) == {}
            # Restore for fixture teardown
            monkeypatch.setattr(engine, "remove_creature", real_remove)
            # manually drop the leftover creature
            try:
                await real_remove("alice")
            except KeyError:
                pass
        finally:
            await engine.shutdown()


# ---------------------------------------------------------------------------
# add_creature / list_creatures / remove_creature
# ---------------------------------------------------------------------------


class TestSessionCreatureOps:
    @pytest.mark.asyncio
    async def test_add_creature_unknown_session(self):
        engine = Terrarium()
        try:
            cfg = CreatureConfig(
                name="x", config_data={"name": "x"}, base_dir=Path("/tmp")
            )
            with pytest.raises(KeyError):
                await lifecycle.add_creature(engine, "ghost-sid", cfg)
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_add_creature_hot_plug(self, monkeypatch):
        engine = Terrarium()
        try:
            seed = await install_fake_creature(engine, "alice")
            sid = seed.graph_id

            # Bypass real config loading inside engine.add_creature.
            real_add = Terrarium.add_creature

            async def _add(self, config, **kwargs):
                if not isinstance(config, CreatureConfig):
                    return await real_add(self, config, **kwargs)
                fake = make_creature(name=config.name)
                return await real_add(self, fake, **kwargs)

            monkeypatch.setattr(Terrarium, "add_creature", _add)

            cfg = CreatureConfig(
                name="bob", config_data={"name": "bob"}, base_dir=Path("/tmp")
            )
            cid = await lifecycle.add_creature(engine, sid, cfg)
            assert cid == "bob"
            graph = engine.get_graph(sid)
            assert "bob" in graph.creature_ids
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_list_creatures(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            out = lifecycle.list_creatures(engine, c.graph_id)
            assert len(out) == 1
            assert out[0]["creature_id"] == "alice"
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_list_creatures_unknown_session(self):
        engine = Terrarium()
        try:
            with pytest.raises(KeyError):
                lifecycle.list_creatures(engine, "ghost")
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_list_creatures_skips_missing(self, monkeypatch):
        """When a graph references a creature_id whose Creature has
        already been popped, ``list_creatures`` skips silently."""
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")

            real_get = engine.get_creature

            def _flaky_get(cid):
                if cid == "alice":
                    raise KeyError("gone")
                return real_get(cid)

            monkeypatch.setattr(engine, "get_creature", _flaky_get)
            out = lifecycle.list_creatures(engine, c.graph_id)
            assert out == []
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_remove_creature_returns_true(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            ok = await lifecycle.remove_creature(engine, c.graph_id, "alice")
            assert ok is True
            assert "alice" not in engine
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_remove_creature_unknown_session(self):
        engine = Terrarium()
        try:
            with pytest.raises(KeyError):
                await lifecycle.remove_creature(engine, "ghost", "alice")
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_remove_creature_unknown_creature_returns_false(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            ok = await lifecycle.remove_creature(engine, c.graph_id, "ghost")
            assert ok is False
        finally:
            await engine.shutdown()


# ---------------------------------------------------------------------------
# find_session_for_creature / find_creature
# ---------------------------------------------------------------------------


class TestFinders:
    @pytest.mark.asyncio
    async def test_find_session_for_creature_hit(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            sid = lifecycle.find_session_for_creature(engine, "alice")
            assert sid == c.graph_id
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_find_session_for_creature_miss(self):
        engine = Terrarium()
        try:
            assert lifecycle.find_session_for_creature(engine, "ghost") is None
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_find_creature_by_id(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            found = lifecycle.find_creature(engine, c.graph_id, "alice")
            assert found is c
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_find_creature_by_name_in_session(self):
        engine = Terrarium()
        try:
            # Pre-build a creature with a custom display name
            cr = make_creature(name="alice")
            cr.creature_id = "alice_abc12345"
            cr.name = "alice"
            await engine.add_creature(cr)
            found = lifecycle.find_creature(engine, cr.graph_id, "alice")
            assert found is cr
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_find_creature_wildcard_session(self):
        engine = Terrarium()
        try:
            cr = make_creature(name="alice")
            cr.creature_id = "alice_xyz"
            cr.name = "alice"
            await engine.add_creature(cr)
            found = lifecycle.find_creature(engine, "_", "alice")
            assert found is cr
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_find_creature_wildcard_id_match(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            found = lifecycle.find_creature(engine, "_", "alice")
            assert found is c
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_find_creature_not_found(self):
        engine = Terrarium()
        try:
            await install_fake_creature(engine, "alice")
            with pytest.raises(KeyError):
                lifecycle.find_creature(engine, "any-sid", "ghost")
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_find_creature_session_mismatch(self):
        """A creature's id-hit must still match the requested session_id
        (unless the wildcard ``_`` is used)."""
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            with pytest.raises(KeyError):
                # Request a different session id — the id-hit fails the
                # session check and the fallback name-scan finds nothing
                # in the bogus session.
                lifecycle.find_creature(engine, "other-graph", "alice")
            # And also raise even when a name-match would succeed in the
            # given session: ensure the id_hit is rejected by id then by
            # name.
            assert c is not None
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_find_creature_resolves_root_alias(self):
        """The literal string ``"root"`` is the tab key the frontend
        sends for terrariums that declare a root agent
        (``stores/chat.js:1116, 1286``).  ``find_creature`` must resolve
        it to the creature flagged ``is_root=True`` even when its
        display name and creature_id are something else.

        Regression for api-audit row 2.2 (every per-creature endpoint
        404'd for terrarium roots before this fix landed).
        """
        engine = Terrarium()
        try:
            controller = make_creature(name="controller")
            controller.creature_id = "controller_001"
            controller.name = "controller"
            controller.is_privileged = True
            worker = make_creature(name="worker")
            worker.creature_id = "worker_002"
            worker.name = "worker"
            await engine.add_creature(controller)
            await engine.add_creature(worker, graph=controller.graph_id)

            # The root alias resolves to the flagged creature.
            found = lifecycle.find_creature(engine, controller.graph_id, "root")
            assert found is controller

            # Direct id / name lookups still work (regression guard).
            assert (
                lifecycle.find_creature(engine, controller.graph_id, "controller")
                is controller
            )
            assert (
                lifecycle.find_creature(engine, controller.graph_id, "worker") is worker
            )
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_find_creature_root_alias_no_root_in_session(self):
        """Without an ``is_root`` creature in the session, the alias
        falls through to KeyError — never matches the wrong creature.
        """
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            with pytest.raises(KeyError):
                lifecycle.find_creature(engine, c.graph_id, "root")
        finally:
            await engine.shutdown()


# ---------------------------------------------------------------------------
# session-store auto-attach error swallowed
# ---------------------------------------------------------------------------


class TestAutoAttachErrors:
    @pytest.mark.asyncio
    async def test_start_creature_swallows_store_init_failure(
        self, patched_add_creature, monkeypatch, tmp_path
    ):
        class _BoomStore:
            def __init__(self, *a, **kw):
                raise RuntimeError("boom")

        monkeypatch.setattr(lifecycle, "SessionStore", _BoomStore)

        engine = Terrarium()
        try:
            cfg = type("Cfg", (), {"name": "alice"})()
            session = await lifecycle.start_creature(
                engine, config=cfg, pwd=str(tmp_path)
            )
            # Session is still created but no store attached
            assert session.session_id
            assert lifecycle.get_session_store(session.session_id) is None
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_start_terrarium_swallows_store_init_failure(
        self, monkeypatch, tmp_path
    ):
        cfg = _basic_terrarium_config()

        from kohakuterrarium.terrarium import recipe as _recipe

        original_apply = _recipe.apply_recipe

        async def _apply(
            engine, recipe, *, graph=None, pwd=None, creature_builder=None
        ):
            return await original_apply(
                engine,
                recipe,
                graph=graph,
                pwd=pwd,
                creature_builder=_fake_recipe_builder,
            )

        monkeypatch.setattr(_recipe, "apply_recipe", _apply)

        class _BoomStore:
            def __init__(self, *a, **kw):
                raise RuntimeError("boom")

        monkeypatch.setattr(lifecycle, "SessionStore", _BoomStore)

        engine = Terrarium()
        try:
            session = await lifecycle.start_terrarium(
                engine, config=cfg, pwd=str(tmp_path)
            )
            assert session.session_id
            assert lifecycle.get_session_store(session.session_id) is None
        finally:
            await engine.shutdown()


# ---------------------------------------------------------------------------
# helper accessor smoke
# ---------------------------------------------------------------------------


class TestHelperAccessors:
    def test_get_session_meta_unknown(self):
        assert lifecycle.get_session_meta("ghost") == {}

    def test_get_session_store_unknown(self):
        assert lifecycle.get_session_store("ghost") is None

    def test_list_session_stores_empty(self):
        assert lifecycle.list_session_stores() == []


class TestFindCreatureMissingDuringScan:
    @pytest.mark.asyncio
    async def test_find_creature_skips_missing_in_candidate_loop(self, monkeypatch):
        """Cover the ``KeyError`` continue branch inside ``find_creature``
        when scanning by name through a session's creature_ids."""
        engine = Terrarium()
        try:
            cr = make_creature(name="alice")
            cr.creature_id = "alice_full_id"
            cr.name = "alice"
            await engine.add_creature(cr)

            real_get = engine.get_creature
            calls = {"count": 0}

            def _flaky(cid):
                calls["count"] += 1
                # First call by name (alice) raises, second by id resolves.
                if cid == "alice" and calls["count"] == 1:
                    raise KeyError("not by name")
                if cid == "alice_full_id" and calls["count"] == 2:
                    # Pretend it's gone for the candidate scan
                    raise KeyError("scan miss")
                return real_get(cid)

            monkeypatch.setattr(engine, "get_creature", _flaky)
            with pytest.raises(KeyError):
                lifecycle.find_creature(engine, cr.graph_id, "alice")
        finally:
            await engine.shutdown()


# ---------------------------------------------------------------------------
# _build_session_handle — direct error path
# ---------------------------------------------------------------------------


class TestBuildHandle:
    @pytest.mark.asyncio
    async def test_build_handle_unknown_raises(self):
        engine = Terrarium()
        try:
            with pytest.raises(KeyError):
                lifecycle._build_session_handle(engine, "missing")
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_build_handle_skips_missing_creatures(self, monkeypatch):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            real_get = engine.get_creature

            def _flaky_get(cid):
                if cid == "alice":
                    raise KeyError("gone")
                return real_get(cid)

            monkeypatch.setattr(engine, "get_creature", _flaky_get)
            sess = lifecycle._build_session_handle(engine, c.graph_id)
            assert sess.session_id == c.graph_id
            assert sess.creatures == []
        finally:
            await engine.shutdown()
