"""Unit tests for :mod:`kohakuterrarium.api.app` factory."""

import pytest

from kohakuterrarium.api import app as app_mod

# ── _parse_bind ───────────────────────────────────────────────


class TestParseBind:
    def test_host_port(self):
        host, port = app_mod._parse_bind("127.0.0.1:8100")
        assert host == "127.0.0.1"
        assert port == 8100

    def test_ipv6_form(self):
        host, port = app_mod._parse_bind("[::1]:8200")
        # rpartition splits on last ":", so host preserves brackets.
        assert port == 8200
        assert "::1" in host

    def test_missing_colon_raises(self):
        with pytest.raises(ValueError, match="invalid lab bind"):
            app_mod._parse_bind("no-colon")


# ── _make_output_wire_target_resolver ─────────────────────────


class TestMakeOutputWireTargetResolver:
    def test_resolves_from_cache(self):
        # service has _creature_name_cache attribute populated
        class _Svc:
            _creature_name_cache = {"alice": ("worker-1", "cid-1")}

        resolver = app_mod._make_output_wire_target_resolver(_Svc())
        assert resolver("alice") == ("worker-1", "cid-1")

    def test_miss_returns_none(self):
        class _Svc:
            _creature_name_cache = {}

        resolver = app_mod._make_output_wire_target_resolver(_Svc())
        assert resolver("ghost") is None

    def test_no_cache_attr_returns_none(self):
        class _Svc:
            pass

        resolver = app_mod._make_output_wire_target_resolver(_Svc())
        assert resolver("anyone") is None


# ── create_app ─────────────────────────────────────────────────


class TestCreateApp:
    def test_standalone_basic_boot(self):
        # Only assert the factory builds something with the expected
        # state. Skip TestClient because lifespan attaches a real
        # engine and uses asyncio resources.
        app = app_mod.create_app(lab_mode="standalone")
        assert app.state.lab_mode == "standalone"
        assert app.state.lab_bind == "127.0.0.1:8100"
        assert app.state.lab_token == ""
        # Routers wired.
        paths = {r.path for r in app.routes if hasattr(r, "path")}
        assert any(p.startswith("/api/catalog/") for p in paths)
        assert any(p.startswith("/api/sessions/") for p in paths)
        assert any(p.startswith("/api/persistence/") for p in paths)

    def test_with_creatures_dirs(self, tmp_path):
        from pathlib import Path

        from kohakuterrarium.api.routes.catalog import (
            creatures_scan as catalog_creatures_scan,
            terrariums_scan as catalog_terrariums_scan,
        )

        try:
            app_mod.create_app(
                creatures_dirs=[str(tmp_path)],
                terrariums_dirs=[str(tmp_path)],
            )
            # The factory wires the supplied dirs into the catalog scan
            # routers (resolved to absolute paths) — observe that side
            # effect, not just "app exists".
            assert catalog_creatures_scan._creatures_dirs == [Path(tmp_path).resolve()]
            assert catalog_terrariums_scan._terrariums_dirs == [
                Path(tmp_path).resolve()
            ]
        finally:
            catalog_creatures_scan.set_creatures_dirs([])
            catalog_terrariums_scan.set_terrariums_dirs([])

    def test_with_static_dir_missing(self, tmp_path):
        # static_dir provided but path doesn't exist as dir → skipped
        app = app_mod.create_app(static_dir=tmp_path / "does-not-exist")
        # No SPA fallback route registered.
        paths = [r.path for r in app.routes if hasattr(r, "path")]
        assert not any("full_path" in p for p in paths)

    def test_with_static_dir_present(self, tmp_path):
        (tmp_path / "index.html").write_text("<html></html>")
        app = app_mod.create_app(static_dir=tmp_path)
        # SPA fallback route present.
        path_strs = [str(r.path) for r in app.routes if hasattr(r, "path")]
        assert any("full_path" in p for p in path_strs)

    def test_with_static_dir_with_assets(self, tmp_path):
        (tmp_path / "index.html").write_text("<html></html>")
        assets = tmp_path / "assets"
        assets.mkdir()
        (assets / "a.js").write_text("//js")
        app = app_mod.create_app(static_dir=tmp_path)
        # Mounted /assets route.
        # The static mount registers as a Mount, look for it.
        mounts = [r for r in app.routes if hasattr(r, "name") and r.name == "assets"]
        assert mounts

    def test_spa_fallback_serves_real_files_and_index(self, tmp_path):
        from fastapi.testclient import TestClient

        # The SPA catch-all serves real on-disk files verbatim, and
        # routes everything else to index.html for client-side routing.
        (tmp_path / "index.html").write_text("<html>SPA-ROOT</html>")
        (tmp_path / "favicon.ico").write_text("ICON-BYTES")
        app = app_mod.create_app(static_dir=tmp_path)
        with TestClient(app) as client:
            # A real file on disk → served as-is.
            r1 = client.get("/favicon.ico")
            assert r1.status_code == 200
            assert r1.text == "ICON-BYTES"
            # An unknown client-side route → index.html (Vue Router).
            r2 = client.get("/dashboard/sessions/abc")
            assert r2.status_code == 200
            assert "SPA-ROOT" in r2.text
            # A path that escapes the static root is NOT served as a
            # file — it falls through to index.html.
            r3 = client.get("/../etc/passwd")
            assert r3.status_code == 200
            assert "SPA-ROOT" in r3.text

    def test_lab_host_mode_attrs(self):
        app = app_mod.create_app(
            lab_mode="lab-host",
            lab_bind="127.0.0.1:0",
            lab_token="secret",
        )
        assert app.state.lab_mode == "lab-host"
        assert app.state.lab_bind == "127.0.0.1:0"
        assert app.state.lab_token == "secret"


# ── lifespan: standalone + lab-host boot/teardown ──────────────


class TestLifespan:
    def test_standalone_lifespan_boots_and_tears_down(self):
        from fastapi.testclient import TestClient

        # Entering the TestClient context runs the lifespan startup;
        # exiting runs teardown. A standalone app must survive both.
        app = app_mod.create_app(lab_mode="standalone")
        with TestClient(app) as client:
            # The runtime-graph route is mounted and reachable, proving
            # the app booted past lifespan startup.
            r = client.get("/api/runtime/graph")
            # 404 or 405 is fine — the point is the server is up, not a
            # WS upgrade on a GET. We only assert it didn't 500.
            assert r.status_code != 500

    def test_lab_host_lifespan_starts_host_engine(self):
        from fastapi.testclient import TestClient

        # lab-host mode boots a real HostEngine on an ephemeral port,
        # wires the multi-node service + adapters, and stashes them on
        # app.state — then tears the whole stack down cleanly on exit.
        app = app_mod.create_app(
            lab_mode="lab-host",
            lab_bind="127.0.0.1:0",
            lab_token="secret",
        )
        with TestClient(app):
            # The lab-host branch stashes the host engine + adapters so
            # admin routes / programmatic callers can reach them.
            assert hasattr(app.state, "lab_host_engine")
            assert hasattr(app.state, "identity_adapter")
            assert hasattr(app.state, "session_mirror")
        # Exiting the context ran the full teardown path (membership
        # watcher cancel, mirror close, adapter detach, service +
        # host shutdown) without raising.


# ── _watch_membership ──────────────────────────────────────────


class TestWatchMembership:
    async def test_join_and_leave_update_the_service(self):

        from kohakuterrarium.laboratory._internal.membership import (
            MembershipEvent,
        )

        events = [
            (MembershipEvent.JOINED, "worker-1"),
            (MembershipEvent.JOINED, "worker-2"),
            (MembershipEvent.LEFT, "worker-1"),
            (MembershipEvent.LOST, "worker-2"),
        ]

        class _FakeMembership:
            async def subscribe(self):
                for e in events:
                    yield e

        class _FakeHost:
            membership = _FakeMembership()

        class _FakeService:
            def __init__(self):
                self.remotes: set[str] = set()

            def add_remote(self, node_id):
                self.remotes.add(node_id)

            def drop_remote(self, node_id):
                self.remotes.discard(node_id)

        svc = _FakeService()
        await app_mod._watch_membership(_FakeHost(), svc)
        # JOINED added both, LEFT/LOST dropped both → empty at the end.
        assert svc.remotes == set()

    async def test_join_then_cancelled_propagates(self):
        import asyncio

        from kohakuterrarium.laboratory._internal.membership import (
            MembershipEvent,
        )

        started = asyncio.Event()

        class _FakeMembership:
            async def subscribe(self):
                yield (MembershipEvent.JOINED, "worker-1")
                started.set()
                # Block forever so the test can cancel the watcher.
                await asyncio.Event().wait()

        class _FakeHost:
            membership = _FakeMembership()

        class _FakeService:
            def __init__(self):
                self.remotes: set[str] = set()

            def add_remote(self, node_id):
                self.remotes.add(node_id)

            def drop_remote(self, node_id):
                self.remotes.discard(node_id)

        svc = _FakeService()
        task = asyncio.create_task(app_mod._watch_membership(_FakeHost(), svc))
        await started.wait()
        # The first JOINED was processed before the watcher blocked.
        assert svc.remotes == {"worker-1"}
        # Cancellation must propagate out (the watcher re-raises it).
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
