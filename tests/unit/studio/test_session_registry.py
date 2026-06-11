"""Unit tests for :mod:`kohakuterrarium.studio.sessions.registry`.

The headline pin is ISOLATION: two services / engines in one process
must not share session bookkeeping (the audit-verified defect was a
module-global ``_meta`` dict that cross-contaminated every Studio /
LocalTerrariumService instance).
"""

from types import SimpleNamespace

from kohakuterrarium.studio.sessions import lifecycle
from kohakuterrarium.studio.sessions.registry import (
    StudioSessionRegistry,
    get_session_meta,
    get_session_store,
    list_session_stores,
    meta_for,
    register_session_meta,
    registry_for,
    stores_for,
)
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.service import LocalTerrariumService


class TestRegistryIsolation:
    async def test_two_services_two_engines_do_not_cross_contaminate(self):
        """The audit defect: two engines sharing one module-global dict."""
        t1, t2 = Terrarium(), Terrarium()
        svc1, svc2 = LocalTerrariumService(t1), LocalTerrariumService(t2)
        try:
            meta_for(svc1)["sid-a"] = {"name": "alpha"}
            # The second service must NOT see the first one's session.
            assert "sid-a" not in meta_for(svc2)
            assert get_session_meta(svc2, "sid-a") == {}
            # ... and writes on the second don't leak back.
            meta_for(svc2)["sid-b"] = {"name": "bravo"}
            assert "sid-b" not in meta_for(svc1)
            # Stores are isolated the same way.
            stores_for(svc1)["sid-a"] = SimpleNamespace(name="store-a")
            assert get_session_store(svc2, "sid-a") is None
            assert list_session_stores(svc2) == []
        finally:
            await t1.shutdown()
            await t2.shutdown()

    async def test_service_and_raw_engine_share_one_registry(self):
        """Anchor is the ENGINE: a LocalTerrariumService wrapper created
        per-request (the L4 pool does this) must see the same registry
        as the raw engine and as any other wrapper of that engine."""
        t = Terrarium()
        wrapper1, wrapper2 = LocalTerrariumService(t), LocalTerrariumService(t)
        try:
            meta_for(wrapper1)["sid"] = {"name": "shared"}
            assert meta_for(t)["sid"] == {"name": "shared"}
            assert meta_for(wrapper2)["sid"] == {"name": "shared"}
            assert registry_for(t) is registry_for(wrapper1)
        finally:
            await t.shutdown()

    def test_multi_node_like_service_anchors_on_itself(self):
        """A lab-host service (exposes ``connected_nodes``) has no host
        engine — its remote-session cache anchors on the service."""
        svc = SimpleNamespace(connected_nodes=lambda: [])
        meta_for(svc)["sid-r"] = {"on_node": "worker-1"}
        assert get_session_meta(svc, "sid-r") == {"on_node": "worker-1"}
        # A second lab-host service is independent.
        other = SimpleNamespace(connected_nodes=lambda: [])
        assert meta_for(other) == {}

    def test_registry_survives_non_settable_anchor(self):
        """Anchors that reject attribute writes fall back to the weak map."""

        class Frozen:
            __slots__ = ("__weakref__",)

        anchor = Frozen()
        reg = registry_for(anchor)
        assert isinstance(reg, StudioSessionRegistry)
        # Lazy accessor returns the SAME registry on re-entry.
        assert registry_for(anchor) is reg


class TestAccessors:
    async def test_register_session_meta_defaults_created_at(self):
        t = Terrarium()
        try:
            entry = register_session_meta(t, "sid", {"name": "n"})
            assert entry["name"] == "n"
            assert entry["created_at"]  # auto-filled, ISO-8601
            # The caller's explicit created_at is preserved.
            entry2 = register_session_meta(t, "sid2", {"created_at": "X"})
            assert entry2["created_at"] == "X"
            # get_session_meta returns a COPY — mutating it must not
            # write through to the registry.
            copy = get_session_meta(t, "sid")
            copy["name"] = "mutated"
            assert meta_for(t)["sid"]["name"] == "n"
        finally:
            await t.shutdown()

    async def test_list_session_stores_skips_none(self):
        t = Terrarium()
        try:
            stores_for(t)["a"] = None
            holder = SimpleNamespace(name="real")
            stores_for(t)["b"] = holder
            assert list_session_stores(t) == [holder]
        finally:
            await t.shutdown()

    async def test_lifecycle_reexports_are_the_same_objects(self):
        """api/ and sibling modules import these via lifecycle."""
        assert lifecycle.meta_for is meta_for
        assert lifecycle.stores_for is stores_for
        assert lifecycle.get_session_meta is get_session_meta
        assert lifecycle.register_session_meta is register_session_meta
