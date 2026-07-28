"""Unit tests for :mod:`kohakuterrarium.terrarium.recipe`.

Uses a stub ``creature_builder`` so we don't load real ``Agent``
instances — the engine layer only cares about ``Creature.agent``
shape, which our fake satisfies.
"""

import asyncio
from pathlib import Path

import pytest

from kohakuterrarium.terrarium import recipe as recipe_mod
from kohakuterrarium.terrarium import graph_checkpoint as checkpoint_mod
from kohakuterrarium.terrarium.config import (
    ChannelConfig,
    CreatureConfig,
    RootConfig,
    TerrariumConfig,
)
from kohakuterrarium.terrarium.creature_host import Creature
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.testing.terrarium import _FakeAgent


def _fake_builder(cfg, *, creature_id, pwd=None, **kw):
    return Creature(
        creature_id=creature_id,
        name=cfg.name,
        agent=_FakeAgent(name=cfg.name),
    )


def _creature_cfg(name, listen=None, send=None):
    return CreatureConfig(
        name=name,
        config_data={"name": name},
        base_dir=Path("."),
        listen_channels=list(listen or []),
        send_channels=list(send or []),
    )


class _StartFailAgent(_FakeAgent):
    async def start(self) -> None:
        raise RuntimeError("start failed")


class _BlockingStartAgent(_FakeAgent):
    started = asyncio.Event()
    release = asyncio.Event()

    async def start(self) -> None:
        self.started.set()
        await self.release.wait()


def _recipe(creatures=None, channels=None, root=None):
    return TerrariumConfig(
        name="test",
        creatures=list(creatures or []),
        channels=list(channels or []),
        root=root,
    )


# ── _resolve_recipe ───────────────────────────────────────────


class TestResolveRecipe:
    def test_passes_through_config(self):
        r = _recipe()
        assert recipe_mod._resolve_recipe(r) is r


# ── apply_recipe ──────────────────────────────────────────────


class TestApplyRecipe:
    async def test_duplicate_names_fail_before_creating_a_partial_graph(self):
        engine = Terrarium()
        try:
            recipe = _recipe(
                creatures=[_creature_cfg("worker"), _creature_cfg("worker")]
            )
            with pytest.raises(ValueError, match="duplicate"):
                await recipe_mod.apply_recipe(
                    engine, recipe, creature_builder=_fake_builder
                )
            assert engine.list_graphs() == []
            assert engine.list_creatures() == []
        finally:
            await engine.shutdown()

    async def test_existing_name_conflict_does_not_declare_recipe_channels(self):
        engine = Terrarium()
        try:
            existing = _fake_builder(_creature_cfg("worker"), creature_id="existing")
            await engine.add_creature(existing, start=False, session=False)
            graph = engine.get_graph(existing.graph_id)
            with pytest.raises(ValueError, match="already contains creature name"):
                await recipe_mod.apply_recipe(
                    engine,
                    _recipe(
                        creatures=[_creature_cfg("worker")],
                        channels=[ChannelConfig(name="should-not-exist")],
                    ),
                    graph=graph.graph_id,
                    creature_builder=_fake_builder,
                )
            assert graph.creature_ids == {"existing"}
            assert graph.channels == {}
        finally:
            await engine.shutdown()

    async def test_empty_recipe(self):
        engine = Terrarium()
        try:
            r = _recipe()
            graph = await recipe_mod.apply_recipe(
                engine, r, creature_builder=_fake_builder
            )
            assert graph.graph_id  # got a graph id
            assert graph.creature_ids == set()
        finally:
            await engine.shutdown()

    async def test_creates_per_creature_direct_channel(self):
        engine = Terrarium()
        try:
            r = _recipe(creatures=[_creature_cfg("alice")])
            graph = await recipe_mod.apply_recipe(
                engine, r, creature_builder=_fake_builder
            )
            # Per-creature direct channel auto-added.
            assert "alice" in graph.channels
            # Auto-listen on own channel.
            alice = engine.get_creature("alice")
            assert "alice" in alice.listen_channels
        finally:
            await engine.shutdown()

    async def test_declared_channels(self):
        engine = Terrarium()
        try:
            r = _recipe(
                creatures=[
                    _creature_cfg("alice", listen=["chat"], send=["chat"]),
                    _creature_cfg("bob", listen=["chat"]),
                ],
                channels=[ChannelConfig(name="chat")],
            )
            graph = await recipe_mod.apply_recipe(
                engine, r, creature_builder=_fake_builder
            )
            assert "chat" in graph.channels
            alice = engine.get_creature("alice")
            assert "chat" in alice.send_channels
        finally:
            await engine.shutdown()

    async def test_skip_undeclared_listen_channel(self):
        engine = Terrarium()
        try:
            # Listen channel "ghost" not declared — should be silently
            # skipped without raising.
            r = _recipe(
                creatures=[_creature_cfg("alice", listen=["ghost"])],
            )
            await recipe_mod.apply_recipe(engine, r, creature_builder=_fake_builder)
            alice = engine.get_creature("alice")
            assert "ghost" not in alice.listen_channels
        finally:
            await engine.shutdown()

    async def test_recipe_with_root(self):
        engine = Terrarium()
        try:
            r = _recipe(
                creatures=[_creature_cfg("bob")],
                root=RootConfig(config_data={"name": "root"}, base_dir=Path(".")),
            )
            graph = await recipe_mod.apply_recipe(
                engine, r, creature_builder=_fake_builder
            )
            # report_to_root auto-added.
            assert "report_to_root" in graph.channels
            assert "root" in graph.creature_ids
            root = engine.get_creature("root")
            assert root.is_privileged is True
            # bob got send edge on report_to_root.
            bob = engine.get_creature("bob")
            assert "report_to_root" in bob.send_channels
        finally:
            await engine.shutdown()

    async def test_reuses_existing_graph(self):
        engine = Terrarium()
        try:
            r1 = _recipe(creatures=[_creature_cfg("alice")])
            g1 = await recipe_mod.apply_recipe(
                engine, r1, creature_builder=_fake_builder
            )
            r2 = _recipe(creatures=[_creature_cfg("bob")])
            g2 = await recipe_mod.apply_recipe(
                engine,
                r2,
                graph=g1.graph_id,
                creature_builder=_fake_builder,
            )
            assert g1.graph_id == g2.graph_id
            assert "alice" in g2.creature_ids
            assert "bob" in g2.creature_ids
        finally:
            await engine.shutdown()

    async def test_concurrent_applies_reserve_distinct_id_sets(self):
        engine = Terrarium()
        both_applies_entered = asyncio.Event()
        release_applies = asyncio.Event()
        entered = 0

        original_add = engine.add_creature

        async def blocked_add(*args, **kwargs):
            nonlocal entered
            entered += 1
            if entered == 2:
                both_applies_entered.set()
            await release_applies.wait()
            return await original_add(*args, **kwargs)

        engine.add_creature = blocked_add
        first_ids: list[str] = []
        second_ids: list[str] = []
        recipe = _recipe(creatures=[_creature_cfg("alice"), _creature_cfg("bob")])
        try:
            first = asyncio.create_task(
                recipe_mod.apply_recipe(
                    engine,
                    recipe,
                    creature_builder=_fake_builder,
                    created_ids=first_ids,
                )
            )
            second = asyncio.create_task(
                recipe_mod.apply_recipe(
                    engine,
                    recipe,
                    creature_builder=_fake_builder,
                    created_ids=second_ids,
                )
            )
            await asyncio.wait_for(both_applies_entered.wait(), timeout=1)
            release_applies.set()
            await asyncio.gather(first, second)

            assert first_ids == ["alice", "bob"]
            assert second_ids == ["alice_2", "bob_2"]
            assert set(first_ids).isdisjoint(second_ids)
        finally:
            release_applies.set()
            await engine.shutdown()

    async def test_existing_id_uses_next_available_suffix(self):
        engine = Terrarium()
        try:
            existing = _fake_builder(_creature_cfg("alice"), creature_id="alice")
            await engine.add_creature(existing, start=False, session=False)
            created_ids: list[str] = []

            await recipe_mod.apply_recipe(
                engine,
                _recipe(creatures=[_creature_cfg("alice")]),
                start=False,
                creature_builder=_fake_builder,
                created_ids=created_ids,
            )

            assert created_ids == ["alice_2"]
        finally:
            await engine.shutdown()

    async def test_builder_error_releases_reservation_and_does_not_deadlock(self):
        engine = Terrarium()

        def failing_builder(*args, **kwargs):
            raise RuntimeError("build failed")

        try:
            recipe = _recipe(creatures=[_creature_cfg("alice")])
            with pytest.raises(RuntimeError, match="build failed"):
                await recipe_mod.apply_recipe(
                    engine, recipe, creature_builder=failing_builder
                )

            created_ids: list[str] = []
            await asyncio.wait_for(
                recipe_mod.apply_recipe(
                    engine,
                    recipe,
                    start=False,
                    creature_builder=_fake_builder,
                    created_ids=created_ids,
                ),
                timeout=1,
            )
            assert created_ids == ["alice"]
        finally:
            await engine.shutdown()

    async def test_cancellation_releases_reservation(self):
        engine = Terrarium()
        add_entered = asyncio.Event()
        never_release = asyncio.Event()
        original_add = engine.add_creature

        async def blocked_add(*args, **kwargs):
            add_entered.set()
            await never_release.wait()
            return await original_add(*args, **kwargs)

        engine.add_creature = blocked_add
        recipe = _recipe(creatures=[_creature_cfg("alice")])
        task = asyncio.create_task(
            recipe_mod.apply_recipe(engine, recipe, creature_builder=_fake_builder)
        )
        try:
            await asyncio.wait_for(add_entered.wait(), timeout=1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

            engine.add_creature = original_add
            created_ids: list[str] = []
            await asyncio.wait_for(
                recipe_mod.apply_recipe(
                    engine,
                    recipe,
                    start=False,
                    creature_builder=_fake_builder,
                    created_ids=created_ids,
                ),
                timeout=1,
            )
            assert created_ids == ["alice"]
        finally:
            task.cancel()
            await engine.shutdown()

    async def test_rejects_duplicate_logical_names_before_mutation(self):
        engine = Terrarium()
        recipe = _recipe(creatures=[_creature_cfg("alice"), _creature_cfg("alice")])
        try:
            with pytest.raises(ValueError, match="duplicate logical creature name"):
                await recipe_mod.apply_recipe(
                    engine, recipe, creature_builder=_fake_builder
                )
            assert engine.list_graphs() == []
            assert engine.list_creatures() == []
        finally:
            await engine.shutdown()

    async def test_rejects_root_section_and_regular_root_name(self):
        engine = Terrarium()
        recipe = _recipe(
            creatures=[_creature_cfg("root")],
            root=RootConfig(config_data={"name": "root"}, base_dir=Path(".")),
        )
        try:
            with pytest.raises(ValueError, match="regular creature named 'root'"):
                await recipe_mod.apply_recipe(
                    engine, recipe, creature_builder=_fake_builder
                )
            assert engine.list_graphs() == []
            assert engine.list_creatures() == []
        finally:
            await engine.shutdown()

    async def test_existing_graph_rejects_duplicate_logical_name(self):
        engine = Terrarium()
        existing = _fake_builder(_creature_cfg("alice"), creature_id="existing")
        try:
            added = await engine.add_creature(existing, start=False, session=False)
            with pytest.raises(ValueError, match="already contains creature name"):
                await recipe_mod.apply_recipe(
                    engine,
                    _recipe(creatures=[_creature_cfg("alice")]),
                    graph=added.graph_id,
                    creature_builder=_fake_builder,
                )
            assert engine.get_graph(added.graph_id).creature_ids == {"existing"}
        finally:
            await engine.shutdown()

    async def test_existing_graph_starts_only_new_members(self):
        engine = Terrarium()
        existing = _fake_builder(_creature_cfg("existing"), creature_id="existing")
        try:
            added = await engine.add_creature(existing, start=False, session=False)
            created_ids: list[str] = []

            await recipe_mod.apply_recipe(
                engine,
                _recipe(creatures=[_creature_cfg("new")]),
                graph=added.graph_id,
                creature_builder=_fake_builder,
                created_ids=created_ids,
            )

            assert existing.agent.start_calls == 0
            assert engine.get_creature("new").agent.start_calls == 1
            assert created_ids == ["new"]
        finally:
            await engine.shutdown()

    async def test_existing_persisted_graph_attaches_store_to_new_members(
        self, tmp_path
    ):
        engine = Terrarium()
        existing = _fake_builder(_creature_cfg("existing"), creature_id="existing")
        try:
            added = await engine.add_creature(
                existing,
                start=False,
                session=tmp_path / "existing.kohakutr",
            )
            store = engine._session_stores[added.graph_id]

            await engine.apply_recipe(
                _recipe(creatures=[_creature_cfg("new")]),
                graph=added.graph_id,
                start=False,
                creature_builder=_fake_builder,
            )

            assert engine.get_creature("new").agent.session_store is store
            assert store.meta["agents"] == ["existing", "new"]
            assert store.meta["config_type"] == "terrarium"
        finally:
            await engine.shutdown()


class TestRecipeRollback:
    async def test_builder_cannot_return_preexisting_creature_id(self):
        engine = Terrarium()
        existing = _fake_builder(_creature_cfg("existing"), creature_id="existing")

        def bad_builder(config, **kwargs):
            return _fake_builder(config, creature_id="existing")

        try:
            await engine.add_creature(existing, start=False, session=False)
            with pytest.raises(ValueError, match="reserved"):
                await recipe_mod.apply_recipe(
                    engine,
                    _recipe(creatures=[_creature_cfg("worker")]),
                    creature_builder=bad_builder,
                )
            assert [creature.creature_id for creature in engine.list_creatures()] == [
                "existing"
            ]
        finally:
            await engine.shutdown()

    async def test_start_failure_rolls_back_only_new_members(self):
        engine = Terrarium()
        existing = _fake_builder(_creature_cfg("existing"), creature_id="existing")
        built = {}

        def failing_builder(config, **kwargs):
            creature = _fake_builder(config, **kwargs)
            if config.name == "bad":
                creature.agent = _StartFailAgent("bad")
            built[config.name] = creature
            return creature

        try:
            added = await engine.add_creature(existing, start=False, session=False)
            with pytest.raises(RuntimeError, match="start failed"):
                await recipe_mod.apply_recipe(
                    engine,
                    _recipe(creatures=[_creature_cfg("good"), _creature_cfg("bad")]),
                    graph=added.graph_id,
                    creature_builder=failing_builder,
                )

            assert [creature.creature_id for creature in engine.list_creatures()] == [
                "existing"
            ]
            graph = engine.get_graph(added.graph_id)
            assert graph.creature_ids == {"existing"}
            assert graph.channels == {}
            assert existing.send_channels == []
            assert graph.send_edges.get("existing", set()) == set()
            assert built["good"].agent.trigger_manager._triggers == {}
            assert built["bad"].agent.trigger_manager._triggers == {}
        finally:
            await engine.shutdown()

    async def test_engine_session_failure_rolls_back_created_graph(self, monkeypatch):
        engine = Terrarium()

        async def fail_attach(*args, **kwargs):
            raise RuntimeError("session attach failed")

        monkeypatch.setattr(
            "kohakuterrarium.terrarium.autosession.attach_for_recipe",
            fail_attach,
        )
        with pytest.raises(RuntimeError, match="session attach failed"):
            await engine.apply_recipe(
                _recipe(creatures=[_creature_cfg("worker")]),
                start=False,
                creature_builder=_fake_builder,
            )

        assert engine.list_creatures() == []
        assert engine.list_graphs() == []
        await engine.shutdown()

    async def test_checkpoint_failure_closes_owned_recipe_store(
        self, monkeypatch, tmp_path
    ):
        engine = Terrarium()

        async def fail_checkpoint(*args, **kwargs):
            raise RuntimeError("checkpoint failed")

        monkeypatch.setattr(
            "kohakuterrarium.terrarium.graph_checkpoint.checkpoint",
            fail_checkpoint,
        )
        session_path = tmp_path / "run.kohakutr"
        with pytest.raises(RuntimeError, match="checkpoint failed"):
            await engine.apply_recipe(
                _recipe(creatures=[_creature_cfg("worker")]),
                start=False,
                session=session_path,
            )

        assert engine.list_creatures() == []
        assert engine.list_graphs() == []
        assert engine._session_stores == {}
        assert engine._owned_sessions == set()
        await engine.shutdown()

    async def test_existing_graph_checkpoint_failure_removes_stale_membership(
        self, monkeypatch, tmp_path
    ):
        engine = Terrarium()
        existing = _fake_builder(_creature_cfg("existing"), creature_id="existing")
        added = await engine.add_creature(existing, start=False, session=False)
        await engine.add_channel(added.graph_id, "worker")

        real_checkpoint = checkpoint_mod.checkpoint

        async def fail_checkpoint(engine_arg, graph_id):
            if checkpoint_mod._suppression.get(engine_arg, 0):
                return await real_checkpoint(engine_arg, graph_id)
            raise RuntimeError("checkpoint failed")

        monkeypatch.setattr(
            "kohakuterrarium.terrarium.graph_checkpoint.checkpoint",
            fail_checkpoint,
        )
        with pytest.raises(RuntimeError, match="checkpoint failed"):
            await engine.apply_recipe(
                _recipe(creatures=[_creature_cfg("worker")]),
                graph=added.graph_id,
                start=False,
                session=tmp_path / "run.kohakutr",
                creature_builder=_fake_builder,
            )

        graph = engine.get_graph(added.graph_id)
        assert graph.creature_ids == {"existing"}
        assert set(engine._creatures) == {"existing"}
        assert set(graph.channels) == {"worker"}
        assert engine._session_stores == {}
        assert engine._owned_sessions == set()
        assert existing.agent.session_store is None
        await engine.shutdown()

    async def test_failed_store_replacement_restores_open_previous_store(
        self, monkeypatch, tmp_path
    ):
        engine = Terrarium()
        existing = _fake_builder(_creature_cfg("existing"), creature_id="existing")
        added = await engine.add_creature(
            existing,
            start=False,
            session=tmp_path / "previous.kohakutr",
        )
        previous = engine._session_stores[added.graph_id]
        real_checkpoint = checkpoint_mod.checkpoint

        async def fail_checkpoint(engine_arg, graph_id):
            if checkpoint_mod._suppression.get(engine_arg, 0):
                return await real_checkpoint(engine_arg, graph_id)
            raise RuntimeError("checkpoint failed")

        monkeypatch.setattr(
            "kohakuterrarium.terrarium.graph_checkpoint.checkpoint",
            fail_checkpoint,
        )
        with pytest.raises(RuntimeError, match="checkpoint failed"):
            await engine.apply_recipe(
                _recipe(creatures=[_creature_cfg("new")]),
                graph=added.graph_id,
                start=False,
                session=tmp_path / "replacement.kohakutr",
                creature_builder=_fake_builder,
            )

        assert engine._session_stores[added.graph_id] is previous
        assert getattr(previous, "_closed", False) is False
        assert existing.agent.session_store is previous
        assert set(engine._creatures) == {"existing"}
        await engine.shutdown()

    async def test_failed_existing_store_reuse_restores_session_meta(
        self, monkeypatch, tmp_path
    ):
        engine = Terrarium()
        existing = _fake_builder(_creature_cfg("existing"), creature_id="existing")
        added = await engine.add_creature(
            existing,
            start=False,
            session=tmp_path / "existing.kohakutr",
        )
        store = engine._session_stores[added.graph_id]

        async def fail_checkpoint(*args, **kwargs):
            raise RuntimeError("checkpoint failed")

        monkeypatch.setattr(
            "kohakuterrarium.terrarium.graph_checkpoint.checkpoint",
            fail_checkpoint,
        )
        with pytest.raises(RuntimeError, match="checkpoint failed"):
            await engine.apply_recipe(
                _recipe(creatures=[_creature_cfg("new")]),
                graph=added.graph_id,
                start=False,
                creature_builder=_fake_builder,
            )

        assert engine._session_stores[added.graph_id] is store
        assert store.meta["agents"] == ["existing"]
        assert store.meta["config_type"] == "agent"
        assert existing.agent.session_store is store
        assert set(engine._creatures) == {"existing"}
        await engine.shutdown()

    async def test_cancelled_start_finishes_rollback_and_releases_identity(self):
        engine = Terrarium()
        _BlockingStartAgent.started = asyncio.Event()
        _BlockingStartAgent.release = asyncio.Event()

        def blocking_builder(config, **kwargs):
            creature = _fake_builder(config, **kwargs)
            creature.agent = _BlockingStartAgent(config.name)
            return creature

        task = asyncio.create_task(
            recipe_mod.apply_recipe(
                engine,
                _recipe(creatures=[_creature_cfg("worker")]),
                creature_builder=blocking_builder,
            )
        )
        await _BlockingStartAgent.started.wait()
        task.cancel()
        _BlockingStartAgent.release.set()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert engine.list_graphs() == []
        assert engine.list_creatures() == []
        graph = await recipe_mod.apply_recipe(
            engine,
            _recipe(creatures=[_creature_cfg("worker")]),
            creature_builder=_fake_builder,
            start=False,
        )
        try:
            assert graph.creature_ids == {"worker"}
        finally:
            await engine.shutdown()


# ── _build_recipe_creature ────────────────────────────────────


class TestBuildRecipeCreature:
    def test_use_default_builder_passes_kwargs(self):
        called = {}

        def default(cfg, *, creature_id, pwd, llm, environment, strict=True):
            called.update(
                {
                    "creature_id": creature_id,
                    "pwd": pwd,
                    "llm": llm,
                    "environment": environment,
                }
            )
            return Creature(
                creature_id=creature_id,
                name=cfg.name,
                agent=_FakeAgent(name=cfg.name),
            )

        cfg = _creature_cfg("x")
        env = object()
        recipe_mod._build_recipe_creature(
            default,
            cfg,
            creature_id="cid",
            pwd="/wd",
            llm="model",
            env=env,
            use_default_builder=True,
        )
        assert called == {
            "creature_id": "cid",
            "pwd": "/wd",
            "llm": "model",
            "environment": env,
        }

    def test_stub_builder_injects_env(self):
        cfg = _creature_cfg("x")
        env = object()
        out = recipe_mod._build_recipe_creature(
            _fake_builder,
            cfg,
            creature_id="cid",
            pwd=None,
            llm=None,
            env=env,
            use_default_builder=False,
        )
        assert out.agent.environment is env
