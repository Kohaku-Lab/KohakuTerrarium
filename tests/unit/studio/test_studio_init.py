"""Unit tests for :mod:`kohakuterrarium.studio.__init__`."""

from types import SimpleNamespace

import pytest

from kohakuterrarium import studio as studio_pkg


class TestStudioInitHooks:
    async def test_store_attach_hook_attaches_a_session_store(
        self, tmp_path, monkeypatch
    ):
        # The hook bridges engine creature-add into the session layer:
        # after it runs, the creature's graph must have a live store.
        from kohakuterrarium.studio.sessions import lifecycle
        from kohakuterrarium.terrarium.service import LocalTerrariumService
        from kohakuterrarium.testing.terrarium import TestTerrariumBuilder

        lifecycle._meta.clear()
        lifecycle._session_stores.clear()
        monkeypatch.setattr(lifecycle, "_session_dir", lambda: str(tmp_path))
        t = await TestTerrariumBuilder().with_creature("alice").build()
        svc = LocalTerrariumService(t)
        creature = t.get_creature("alice")
        attached = []
        creature.agent.attach_session_store = lambda s: attached.append(s)
        try:
            studio_pkg._store_attach_hook(svc, creature, config_path="/tmp/cfg.yaml")
            sid = creature.graph_id
            assert sid in lifecycle._session_stores
            assert attached and attached[0] is lifecycle._session_stores[sid]
            lifecycle._session_stores[sid].close()
        finally:
            lifecycle._meta.clear()
            lifecycle._session_stores.clear()
            await t.shutdown()

    def test_spawnable_hook_lists_package_creatures(self):
        # With no workspace the hook surfaces the installed-package
        # creature catalog; each entry must carry a resolvable ref +
        # name + source so a privileged node could spawn it.
        # On a clean install / CI runner there may be NO packages —
        # that's a valid empty state, not a failure. We still
        # exercise the hook to confirm it runs and that anything it
        # DOES return has the right shape; the kt-biome-specific
        # assertion is gated on kt-biome actually being installed.
        out = studio_pkg._spawnable_hook(workspace=None)
        assert isinstance(out, list)
        for entry in out:
            assert entry["ref"].startswith("@")
            assert entry["name"]
            assert entry["source"]
        if not out:
            pytest.skip("no installed packages on this runner — nothing to enumerate")
        if any(e["source"] == "kt-biome" for e in out):
            # 'general' is the canonical base creature shipped by kt-biome.
            assert any(e["name"] == "general" for e in out)


class TestResolveWorkspaceHook:
    def test_no_executor_returns_none(self):
        creature = SimpleNamespace(agent=SimpleNamespace(executor=None))
        assert studio_pkg._resolve_workspace_hook(None, creature) is None

    def test_executor_no_working_dir(self):
        creature = SimpleNamespace(
            agent=SimpleNamespace(executor=SimpleNamespace(_working_dir=""))
        )
        assert studio_pkg._resolve_workspace_hook(None, creature) is None

    def test_invalid_workspace_returns_none(self, tmp_path):
        creature = SimpleNamespace(
            agent=SimpleNamespace(
                executor=SimpleNamespace(_working_dir=str(tmp_path / "ghost"))
            )
        )
        assert studio_pkg._resolve_workspace_hook(None, creature) is None

    def test_valid_workspace_returns_handle(self, tmp_path):
        creature = SimpleNamespace(
            agent=SimpleNamespace(executor=SimpleNamespace(_working_dir=str(tmp_path)))
        )
        ws = studio_pkg._resolve_workspace_hook(None, creature)
        # The hook resolves the creature's working dir into a real
        # workspace handle rooted at that directory.
        assert ws.root == str(tmp_path)


class TestPackagePublic:
    def test_studio_re_exported_is_the_class(self):
        from kohakuterrarium.studio.studio import Studio as _RealStudio

        assert studio_pkg.Studio is _RealStudio

    def test_all_lists_studio(self):
        assert "Studio" in studio_pkg.__all__
