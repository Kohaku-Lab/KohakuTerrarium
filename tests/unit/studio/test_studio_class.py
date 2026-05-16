"""Unit tests for the :class:`Studio` facade in
:mod:`kohakuterrarium.studio.studio`.

We focus on the constructor branches + the namespace wiring; the
heavy ``catalog.packages.install`` etc. functions delegate to
``builtins/packages.py`` (3rd-party-blocked) so they stay out of scope.
"""

import pytest

from kohakuterrarium.studio.studio import Studio
from kohakuterrarium.terrarium import LocalTerrariumService, Terrarium

# ── constructor branches ───────────────────────────────────────


class TestStudioConstruction:
    def test_default_creates_fresh_engine(self):
        s = Studio()
        assert isinstance(s.service, LocalTerrariumService)
        assert isinstance(s.engine, Terrarium)
        assert s.nodes is None

    def test_with_engine_uses_it(self):
        engine = Terrarium()
        s = Studio(engine=engine)
        assert s.engine is engine

    def test_with_service_uses_it(self):
        engine = Terrarium()
        svc = LocalTerrariumService(engine)
        s = Studio(service=svc)
        assert s.service is svc

    def test_both_service_and_engine_raises(self):
        engine = Terrarium()
        svc = LocalTerrariumService(engine)
        with pytest.raises(TypeError, match="at most one"):
            Studio(engine=engine, service=svc)


# ── namespace wiring ───────────────────────────────────────────


class TestStudioNamespaces:
    def test_namespaces_are_the_expected_namespace_types(self):
        from kohakuterrarium.studio import studio as studio_mod

        s = Studio()
        # Each public attribute is wired to its dedicated namespace
        # class — not, e.g., all aliased to the same object.
        assert isinstance(s.catalog, studio_mod._CatalogNS)
        assert isinstance(s.identity, studio_mod._IdentityNS)
        assert isinstance(s.sessions, studio_mod._SessionsNS)
        assert isinstance(s.persistence, studio_mod._PersistenceNS)
        assert isinstance(s.editors, studio_mod._EditorsNS)
        assert isinstance(s.attach, studio_mod._AttachNS)

    def test_catalog_sub_namespaces_expose_their_methods(self):
        s = Studio()
        # The catalog sub-namespaces must expose their real entry
        # points, not just exist as attributes.
        assert callable(s.catalog.packages.list)
        assert callable(s.catalog.creatures.list)
        assert callable(s.catalog.modules.list)
        assert callable(s.catalog.builtins.list)
        assert callable(s.catalog.introspect.builtin_schema)


# ── async context manager ──────────────────────────────────────


class TestAsyncContextManager:
    async def test_enter_exit_stops_running_creatures(self):
        from kohakuterrarium.testing.terrarium import TestTerrariumBuilder

        engine = await TestTerrariumBuilder().with_creature("alice").build()
        async with Studio(engine=engine) as out:
            assert out.engine is engine
            assert engine.get_creature("alice").agent.is_running is True
        # __aexit__ shuts the engine down → the creature is stopped.
        assert engine.get_creature("alice").agent.is_running is False

    async def test_shutdown_stops_running_creatures(self):
        from kohakuterrarium.testing.terrarium import TestTerrariumBuilder

        engine = await TestTerrariumBuilder().with_creature("alice").build()
        s = Studio(engine=engine)
        assert engine.get_creature("alice").agent.is_running is True
        await s.shutdown()
        assert engine.get_creature("alice").agent.is_running is False


# ── classmethod constructors ───────────────────────────────────


class TestClassmethodConstructors:
    async def test_with_creature_delegates(self, monkeypatch):
        # Patch start_creature so we don't load a real config.
        captured = {}

        async def fake_start(self, config, *, pwd=None, llm_override=None):
            captured["config"] = config
            captured["pwd"] = pwd
            captured["llm_override"] = llm_override

        # Patch _SessionsNS.start_creature.
        from kohakuterrarium.studio.studio import _SessionsNS

        monkeypatch.setattr(_SessionsNS, "start_creature", fake_start)
        studio = await Studio.with_creature(
            "/some/cfg", pwd="/x", llm_override="claude"
        )
        assert isinstance(studio, Studio)
        assert captured["config"] == "/some/cfg"
        assert captured["pwd"] == "/x"
        assert captured["llm_override"] == "claude"
        await studio.shutdown()

    async def test_resume_delegates(self, monkeypatch):
        from kohakuterrarium.studio.studio import _PersistenceNS

        captured = {}

        async def fake_resume(self, path, *, pwd_override=None, llm_override=None):
            captured["path"] = path

        monkeypatch.setattr(_PersistenceNS, "resume", fake_resume)
        studio = await Studio.resume("/x.kohakutr")
        assert isinstance(studio, Studio)
        assert captured["path"] == "/x.kohakutr"
        await studio.shutdown()

    async def test_from_recipe_delegates(self, monkeypatch):
        # Patch Terrarium.from_recipe to avoid loading a real recipe.
        captured = {}

        async def fake_from_recipe(recipe, *, pwd=None):
            captured["recipe"] = recipe
            return Terrarium()

        monkeypatch.setattr(Terrarium, "from_recipe", fake_from_recipe)
        studio = await Studio.from_recipe("/x")
        assert isinstance(studio, Studio)
        assert captured["recipe"] == "/x"
        await studio.shutdown()


# ── service-injection multi-node branch ───────────────────────


class TestMultiNodeServiceBranch:
    def test_multi_node_service_attaches_nodes(self, monkeypatch):
        # Patch build_node_map_if_multi_node so we don't need a real
        # MultiNodeTerrariumService.
        from kohakuterrarium.studio import studio as studio_mod

        sentinel = object()
        monkeypatch.setattr(
            studio_mod, "build_node_map_if_multi_node", lambda svc: sentinel
        )

        class _FakeMultiService:
            engine = Terrarium()
            node_id = "_host"

            def connected_nodes(self):
                return ("_host", "w1")

        s = Studio(service=_FakeMultiService())
        assert s.nodes is sentinel
