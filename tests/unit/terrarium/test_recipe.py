"""Unit tests for :mod:`kohakuterrarium.terrarium.recipe`.

Uses a stub ``creature_builder`` so we don't load real ``Agent``
instances — the engine layer only cares about ``Creature.agent``
shape, which our fake satisfies.
"""

from pathlib import Path


from kohakuterrarium.terrarium import recipe as recipe_mod
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


# ── _build_recipe_creature ────────────────────────────────────


class TestBuildRecipeCreature:
    def test_use_default_builder_passes_kwargs(self):
        called = {}

        def default(cfg, *, creature_id, pwd, llm_override, environment):
            called.update(
                {
                    "creature_id": creature_id,
                    "pwd": pwd,
                    "llm_override": llm_override,
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
            llm_override="model",
            env=env,
            use_default_builder=True,
        )
        assert called == {
            "creature_id": "cid",
            "pwd": "/wd",
            "llm_override": "model",
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
            llm_override=None,
            env=env,
            use_default_builder=False,
        )
        assert out.agent.environment is env
