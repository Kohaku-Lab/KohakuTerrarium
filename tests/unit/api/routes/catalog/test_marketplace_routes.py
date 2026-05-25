"""Unit tests for the ``/api/catalog/marketplace/*`` router.

Every backend call is stubbed against the marketplace module — these
tests cover the HTTP layer only (status codes, response shape, admin
gate enforcement).  The data layer's behaviour is covered by
``tests/unit/packages/test_marketplace.py``.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.routes.catalog import marketplace as route_mod
from kohakuterrarium.packages import marketplace as data_mod
from kohakuterrarium.packages.marketplace_types import (
    IncompatibleFrameworkError,
    InvalidSpecError,
    MarketplaceEntry,
    MarketplaceNotFoundError,
    MarketplaceSource,
    MarketplaceUnavailableError,
    MarketplaceVersion,
)

PREFIX = "/api/catalog/marketplace"


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("KT_AUTH_ADMIN_TOKEN", "")  # L3 off → admin gate is no-op
    app = FastAPI()
    app.state.lab_mode = "standalone"
    app.include_router(route_mod.router, prefix=PREFIX)
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def _entry(name: str = "kt-biome") -> MarketplaceEntry:
    return MarketplaceEntry(
        name=name,
        repo=f"https://github.com/Kohaku-Lab/{name}",
        description=f"{name} description",
        tags=("creatures", "official"),
        author="Kohaku-Lab",
        license="LicenseRef-KohakuTerrarium-1.0",
        framework=">=1.5.0,<2.0.0",
        versions=(MarketplaceVersion(tag="v1.0.0", released="2026-05-01"),),
        source_url=data_mod.DEFAULT_SOURCE_URL,
        source_alias="default",
    )


def _source() -> MarketplaceSource:
    return MarketplaceSource(alias="default", url=data_mod.DEFAULT_SOURCE_URL)


# ── reads ───────────────────────────────────────────────────────


class TestListPackages:
    def test_happy_path(self, client, monkeypatch):
        async def fake_search(q="", *, tag=None, author=None):
            return [_entry("kt-biome"), _entry("kt-template")]

        # Route now routes through ``search`` (no filter) so the
        # user-facing first-source-wins dedup applies — pre-fix the
        # un-deduped fetch_marketplace was leaking shadowed
        # duplicates into the card grid.
        monkeypatch.setattr(data_mod, "search", fake_search)
        monkeypatch.setattr(data_mod, "list_sources", lambda: [_source()])

        r = client.get(f"{PREFIX}/packages")
        assert r.status_code == 200
        body = r.json()
        assert [p["name"] for p in body["packages"]] == ["kt-biome", "kt-template"]
        assert body["sources"][0]["alias"] == "default"

    def test_unavailable_503(self, client, monkeypatch):
        async def fake_search(q="", *, tag=None, author=None):
            raise MarketplaceUnavailableError("offline")

        monkeypatch.setattr(data_mod, "search", fake_search)
        r = client.get(f"{PREFIX}/packages")
        assert r.status_code == 503
        assert "offline" in r.json()["detail"]


class TestGetPackage:
    def test_happy_path(self, client, monkeypatch):
        async def fake_resolve(spec):
            entry = _entry("kt-biome")
            return entry, entry.versions[0]

        monkeypatch.setattr(data_mod, "resolve", fake_resolve)
        r = client.get(f"{PREFIX}/packages/kt-biome")
        assert r.status_code == 200
        body = r.json()
        assert body["entry"]["name"] == "kt-biome"
        assert body["resolved_version"] == "v1.0.0"

    def test_not_found_404(self, client, monkeypatch):
        async def fake_resolve(spec):
            raise MarketplaceNotFoundError("no such")

        monkeypatch.setattr(data_mod, "resolve", fake_resolve)
        r = client.get(f"{PREFIX}/packages/nope")
        assert r.status_code == 404


class TestSearch:
    def test_query_passed(self, client, monkeypatch):
        captured = {}

        async def fake_search(q, *, tag=None, author=None):
            captured["q"] = q
            captured["tag"] = tag
            captured["author"] = author
            return [_entry("kt-biome")]

        monkeypatch.setattr(data_mod, "search", fake_search)
        r = client.get(f"{PREFIX}/search", params={"q": "bio", "tag": "creatures"})
        assert r.status_code == 200
        assert captured == {"q": "bio", "tag": "creatures", "author": None}
        assert r.json()["packages"][0]["name"] == "kt-biome"

    def test_empty_query_ok(self, client, monkeypatch):
        async def fake_search(q, *, tag=None, author=None):
            return []

        monkeypatch.setattr(data_mod, "search", fake_search)
        r = client.get(f"{PREFIX}/search")
        assert r.status_code == 200
        assert r.json()["packages"] == []


class TestRefresh:
    def test_invalidates_and_returns_count(self, client, monkeypatch):
        called = {}

        async def fake_fetch(*, force=False):
            called["force"] = force
            return [_entry("a"), _entry("b")]

        monkeypatch.setattr(data_mod, "fetch_marketplace", fake_fetch)
        r = client.post(f"{PREFIX}/refresh")
        assert r.status_code == 200
        assert called["force"] is True
        assert r.json() == {"ok": True, "packages": 2}

    def test_admin_gated_when_l3_on(self, monkeypatch):
        # AUDIT FIX #4: /refresh used to be public.  Confirm it's now
        # admin-gated when L3 is configured (same shape as /sources +
        # /install).  Without the gate, an anonymous caller on a
        # multi-user host could DoS the upstream marketplace via
        # refresh spam.
        monkeypatch.setenv("KT_AUTH_ADMIN_TOKEN", "secret-admin")
        app = FastAPI()
        app.state.lab_mode = "standalone"
        app.include_router(route_mod.router, prefix=PREFIX)
        client = TestClient(app)

        async def fake_fetch(*, force=False):
            return []

        monkeypatch.setattr(data_mod, "fetch_marketplace", fake_fetch)

        # Without admin header → 401/403.
        r = client.post(f"{PREFIX}/refresh")
        assert r.status_code in (401, 403)

        # With admin header → 200.
        r = client.post(
            f"{PREFIX}/refresh",
            headers={"X-Admin-Token": "secret-admin"},
        )
        assert r.status_code == 200


# ── source management ───────────────────────────────────────────


class TestGetSources:
    def test_lists(self, client, monkeypatch):
        monkeypatch.setattr(data_mod, "list_sources", lambda: [_source()])
        r = client.get(f"{PREFIX}/sources")
        assert r.status_code == 200
        assert r.json()["sources"][0]["alias"] == "default"


class TestAddSource:
    def test_added(self, client, monkeypatch):
        captured = {}

        def fake_add(url, *, alias=None):
            captured["args"] = (url, alias)
            return MarketplaceSource(alias=alias or url, url=url)

        monkeypatch.setattr(data_mod, "add_source", fake_add)
        monkeypatch.setattr(data_mod, "list_sources", lambda: [_source()])
        r = client.post(
            f"{PREFIX}/sources",
            json={"url": "https://ex.test/r.yaml", "alias": "ex"},
        )
        assert r.status_code == 200
        assert captured["args"] == ("https://ex.test/r.yaml", "ex")

    def test_duplicate_400(self, client, monkeypatch):
        def boom(_url, *, alias=None):
            raise ValueError("already configured")

        monkeypatch.setattr(data_mod, "add_source", boom)
        r = client.post(f"{PREFIX}/sources", json={"url": "https://x.test/r.yaml"})
        assert r.status_code == 400


class TestRemoveSource:
    def test_removed_by_alias_query(self, client, monkeypatch):
        # AUDIT FIX #5 (round-2): route is DELETE /sources?target=...
        # (query string) so URL targets containing slashes serialize
        # cleanly.  Aliases work the same way.
        captured = {}
        monkeypatch.setattr(
            data_mod, "remove_source", lambda t: captured.setdefault("t", t) or True
        )
        monkeypatch.setattr(data_mod, "list_sources", lambda: [_source()])
        r = client.delete(f"{PREFIX}/sources", params={"target": "ex"})
        assert r.status_code == 200
        assert captured["t"] == "ex"

    def test_removed_by_url_query(self, client, monkeypatch):
        # URL targets contain slashes — must survive the round-trip
        # through the query string (httpx urlencodes; FastAPI
        # decodes).  Pre-fix this case was unreachable via the path-
        # param form even if the data layer supported it.
        captured = {}
        monkeypatch.setattr(
            data_mod, "remove_source", lambda t: captured.setdefault("t", t) or True
        )
        monkeypatch.setattr(data_mod, "list_sources", lambda: [_source()])
        url = "https://raw.githubusercontent.com/owner/repo/main/registry.yaml"
        r = client.delete(f"{PREFIX}/sources", params={"target": url})
        assert r.status_code == 200
        assert captured["t"] == url

    def test_missing_404(self, client, monkeypatch):
        monkeypatch.setattr(data_mod, "remove_source", lambda t: False)
        r = client.delete(f"{PREFIX}/sources", params={"target": "nope"})
        assert r.status_code == 404


# ── install ─────────────────────────────────────────────────────


class TestInstall:
    def test_happy_path(self, client, monkeypatch):
        captured = {}

        def fake_op(*, source, name=None, editable=False):
            captured["args"] = (source, name, editable)
            return "kt-biome"

        monkeypatch.setattr(route_mod, "install_package_op", fake_op)
        r = client.post(f"{PREFIX}/install", json={"spec": "@kt-biome"})
        assert r.status_code == 200
        body = r.json()
        assert body == {"status": "installed", "name": "kt-biome", "spec": "@kt-biome"}
        # ``editable`` defaults to False when omitted from the request.
        assert captured["args"] == ("@kt-biome", None, False)

    def test_editable_kwarg_passed_through(self, client, monkeypatch):
        # Frontend's "Install from source" modal sends ``editable: true``
        # when the user ticks the editable checkbox for a local path.
        # The route must forward that through to ``install_package_op``.
        captured = {}

        def fake_op(*, source, name=None, editable=False):
            captured["editable"] = editable
            return "demo"

        monkeypatch.setattr(route_mod, "install_package_op", fake_op)
        r = client.post(
            f"{PREFIX}/install",
            json={"spec": "/tmp/local-pack", "editable": True},
        )
        assert r.status_code == 200
        assert captured["editable"] is True

    def test_editable_on_marketplace_spec_400(self, client, monkeypatch):
        # ``install_package_spec`` (and thus ``install_package_op``)
        # raises ValueError when editable is requested against a
        # marketplace spec — git clones can't be ``-e`` linked.  The
        # route must translate that to 400, not 500.
        def boom(*, source, name=None, editable=False):
            raise ValueError(
                "Cannot install a marketplace spec as editable; "
                "use `kt install -e <local-path>` instead"
            )

        monkeypatch.setattr(route_mod, "install_package_op", boom)
        r = client.post(
            f"{PREFIX}/install",
            json={"spec": "@kt-biome", "editable": True},
        )
        assert r.status_code == 400
        assert "editable" in r.json()["detail"].lower()

    def test_empty_spec_400(self, client):
        r = client.post(f"{PREFIX}/install", json={"spec": ""})
        assert r.status_code == 400

    def test_not_found_404(self, client, monkeypatch):
        def boom(*, source, name=None, editable=False):
            raise MarketplaceNotFoundError("no such")

        monkeypatch.setattr(route_mod, "install_package_op", boom)
        r = client.post(f"{PREFIX}/install", json={"spec": "@nope"})
        assert r.status_code == 404

    def test_incompatible_framework_409(self, client, monkeypatch):
        def boom(*, source, name=None, editable=False):
            raise IncompatibleFrameworkError("requires >=2.0")

        monkeypatch.setattr(route_mod, "install_package_op", boom)
        r = client.post(f"{PREFIX}/install", json={"spec": "@x"})
        assert r.status_code == 409

    def test_unavailable_503(self, client, monkeypatch):
        def boom(*, source, name=None, editable=False):
            raise MarketplaceUnavailableError("offline")

        monkeypatch.setattr(route_mod, "install_package_op", boom)
        r = client.post(f"{PREFIX}/install", json={"spec": "@x"})
        assert r.status_code == 503

    def test_invalid_spec_404(self, client, monkeypatch):
        def boom(*, source, name=None, editable=False):
            raise InvalidSpecError("not a spec")

        monkeypatch.setattr(route_mod, "install_package_op", boom)
        r = client.post(f"{PREFIX}/install", json={"spec": "@x"})
        # InvalidSpecError is mapped to 404 — the resolver couldn't
        # turn the spec into something installable.
        assert r.status_code == 404

    def test_generic_error_500(self, client, monkeypatch):
        def boom(*, source, name=None, editable=False):
            raise RuntimeError("clone failed")

        monkeypatch.setattr(route_mod, "install_package_op", boom)
        r = client.post(f"{PREFIX}/install", json={"spec": "@x"})
        assert r.status_code == 500
        assert "clone failed" in r.json()["detail"]
