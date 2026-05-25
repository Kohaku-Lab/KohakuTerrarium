"""Unit tests for :mod:`kohakuterrarium.packages.marketplace`.

Pure-Python tests with no network — every fetch is stubbed via a
monkeypatched ``httpx.AsyncClient``.  Tests honour ``KT_CONFIG_DIR``
for isolation so they never touch the developer's real
``~/.kohakuterrarium``.
"""

import asyncio
import json
from typing import Any

import pytest

from kohakuterrarium.packages import marketplace
from kohakuterrarium.packages.marketplace_types import (
    IncompatibleFrameworkError,
    InvalidSpecError,
    MarketplaceEntry,
    MarketplaceNotFoundError,
    MarketplaceUnavailableError,
)

SAMPLE_REGISTRY = {
    "schema_version": 1,
    "generated": "2026-05-24T00:00:00Z",
    "packages": [
        {
            "name": "kt-biome",
            "repo": "https://github.com/Kohaku-Lab/kt-biome",
            "description": "Official pack",
            "tags": ["creatures", "official"],
            "author": "Kohaku-Lab",
            "license": "LicenseRef-KohakuTerrarium-1.0",
            "framework": ">=1.5.0,<2.0.0",
            "versions": [
                {"tag": "v1.2.0", "released": "2026-05-01"},
                {"tag": "v1.1.0", "released": "2026-04-01", "yanked": True},
                {"tag": "v1.0.0", "released": "2026-03-01"},
            ],
        },
        {
            "name": "kt-template",
            "repo": "https://github.com/Kohaku-Lab/kt-template",
            "description": "Starter",
            "tags": ["creatures", "template"],
            "author": "Kohaku-Lab",
            "license": "LicenseRef-KohakuTerrarium-1.0",
            "framework": ">=1.5.0,<2.0.0",
            "versions": [{"tag": "main", "released": "2026-05-24"}],
        },
    ],
}


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "", headers: dict | None = None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            req = httpx.Request("GET", "https://example.test")
            raise httpx.HTTPStatusError(
                f"{self.status_code}", request=req, response=self  # type: ignore
            )


class _FakeAsyncClient:
    """In-memory stand-in for ``httpx.AsyncClient``.

    Configure by setting the class-level ``responses`` dict (url ->
    callable returning _FakeResponse).  The callable receives the
    request headers so tests can assert on ``If-None-Match`` etc.
    """

    responses: dict[str, Any] = {}
    requests: list[tuple[str, dict[str, str]]] = []

    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None

    async def get(self, url: str, headers: dict[str, str] | None = None, **_kw):
        type(self).requests.append((url, dict(headers or {})))
        handler = type(self).responses.get(url)
        if handler is None:
            return _FakeResponse(404, text=f"no fake response for {url}")
        return handler(headers or {})

    @classmethod
    def reset(cls) -> None:
        cls.responses = {}
        cls.requests = []


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path, monkeypatch):
    """Every test gets a fresh KT_CONFIG_DIR + cleared module state.

    Also stubs ``_current_framework_version`` so the framework-compat
    check in ``resolve`` doesn't get confused by the dev version of
    the running framework — SAMPLE_REGISTRY declares
    ``>=1.5.0,<2.0.0`` which the dev ``2.0.0.dev*`` install falls
    just outside of.  Tests that specifically exercise the compat
    check override this stub themselves.
    """
    from packaging.version import Version

    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("KT_MARKETPLACE_SOURCES", raising=False)
    monkeypatch.delenv("KT_MARKETPLACE_CACHE_TTL", raising=False)
    monkeypatch.setattr(
        marketplace, "_current_framework_version", lambda: Version("1.5.0")
    )
    marketplace.invalidate_cache()
    _FakeAsyncClient.reset()
    monkeypatch.setattr(marketplace.httpx, "AsyncClient", _FakeAsyncClient)
    yield
    marketplace.invalidate_cache()


def _yaml_response(text: str, etag: str = "abc"):
    return lambda headers: (
        _FakeResponse(304, headers={"ETag": etag})
        if headers.get("If-None-Match") == etag
        else _FakeResponse(200, text=text, headers={"ETag": etag})
    )


def _sample_yaml() -> str:
    import yaml as _yaml

    return _yaml.dump(SAMPLE_REGISTRY)


# ── parse_spec ──────────────────────────────────────────────────


class TestParseSpec:
    def test_name_only(self):
        assert marketplace.parse_spec("@kt-biome") == (None, "kt-biome", None)

    def test_name_with_version(self):
        assert marketplace.parse_spec("@kt-biome@v1.2.0") == (
            None,
            "kt-biome",
            "v1.2.0",
        )

    def test_source_alias(self):
        assert marketplace.parse_spec("@myfork/kt-biome") == (
            "myfork",
            "kt-biome",
            None,
        )

    def test_source_alias_with_version(self):
        assert marketplace.parse_spec("@myfork/kt-biome@v0.1") == (
            "myfork",
            "kt-biome",
            "v0.1",
        )

    def test_missing_at(self):
        with pytest.raises(InvalidSpecError):
            marketplace.parse_spec("kt-biome")

    def test_empty_name(self):
        with pytest.raises(InvalidSpecError):
            marketplace.parse_spec("@")

    def test_is_spec(self):
        assert marketplace.is_spec("@x")
        assert not marketplace.is_spec("x")
        assert not marketplace.is_spec("https://github.com/o/r")


# ── source list ─────────────────────────────────────────────────


class TestSourceList:
    def test_default_when_empty(self):
        sources = marketplace.list_sources()
        assert len(sources) == 1
        assert sources[0].alias == "default"
        assert sources[0].url == marketplace.DEFAULT_SOURCE_URL

    def test_add_source_persists(self):
        added = marketplace.add_source("https://example.com/r.yaml", alias="ex")
        assert added.alias == "ex"
        sources = marketplace.list_sources()
        urls = [s.url for s in sources]
        assert marketplace.DEFAULT_SOURCE_URL in urls
        assert "https://example.com/r.yaml" in urls

    def test_add_source_duplicate_rejected(self):
        marketplace.add_source("https://example.com/r.yaml")
        with pytest.raises(ValueError):
            marketplace.add_source("https://example.com/r.yaml")

    def test_add_source_empty_rejected(self):
        with pytest.raises(ValueError):
            marketplace.add_source("")

    def test_remove_source_by_url(self):
        marketplace.add_source("https://example.com/r.yaml")
        assert marketplace.remove_source("https://example.com/r.yaml") is True
        urls = [s.url for s in marketplace.list_sources()]
        assert "https://example.com/r.yaml" not in urls

    def test_remove_source_by_alias(self):
        marketplace.add_source("https://example.com/r.yaml", alias="ex")
        assert marketplace.remove_source("ex") is True

    def test_remove_source_missing(self):
        assert marketplace.remove_source("nope") is False

    def test_reset_sources(self):
        marketplace.add_source("https://example.com/r.yaml")
        marketplace.reset_sources()
        sources = marketplace.list_sources()
        assert len(sources) == 1
        assert sources[0].alias == "default"

    def test_env_override_takes_precedence(self, monkeypatch):
        marketplace.add_source("https://example.com/r.yaml", alias="file")
        monkeypatch.setenv(
            "KT_MARKETPLACE_SOURCES",
            "https://a.test/x.yaml,https://b.test/y.yaml",
        )
        sources = marketplace.list_sources()
        urls = [s.url for s in sources]
        assert urls == ["https://a.test/x.yaml", "https://b.test/y.yaml"]


# ── fetch_marketplace ───────────────────────────────────────────


class TestFetchMarketplace:
    def test_fetches_default_source(self):
        _FakeAsyncClient.responses = {
            marketplace.DEFAULT_SOURCE_URL: _yaml_response(_sample_yaml()),
        }
        entries = asyncio.run(marketplace.fetch_marketplace())
        names = [e.name for e in entries]
        assert names == ["kt-biome", "kt-template"]

    def test_cached_within_ttl(self):
        _FakeAsyncClient.responses = {
            marketplace.DEFAULT_SOURCE_URL: _yaml_response(_sample_yaml()),
        }
        asyncio.run(marketplace.fetch_marketplace())
        n_requests_after_first = len(_FakeAsyncClient.requests)
        # Second call should hit memory cache; no new requests.
        asyncio.run(marketplace.fetch_marketplace())
        assert len(_FakeAsyncClient.requests) == n_requests_after_first

    def test_force_refresh_bypasses_cache(self):
        _FakeAsyncClient.responses = {
            marketplace.DEFAULT_SOURCE_URL: _yaml_response(_sample_yaml()),
        }
        asyncio.run(marketplace.fetch_marketplace())
        n_after = len(_FakeAsyncClient.requests)
        asyncio.run(marketplace.fetch_marketplace(force=True))
        assert len(_FakeAsyncClient.requests) > n_after

    def test_etag_conditional(self):
        _FakeAsyncClient.responses = {
            marketplace.DEFAULT_SOURCE_URL: _yaml_response(_sample_yaml(), etag="V1"),
        }
        asyncio.run(marketplace.fetch_marketplace())
        # Force re-fetch — should send If-None-Match and get 304.
        asyncio.run(marketplace.fetch_marketplace(force=True))
        # Last request should carry the ETag we cached.
        url, headers = _FakeAsyncClient.requests[-1]
        assert headers.get("If-None-Match") == "V1"

    def test_network_failure_falls_back_to_cache(self):
        _FakeAsyncClient.responses = {
            marketplace.DEFAULT_SOURCE_URL: _yaml_response(_sample_yaml()),
        }
        asyncio.run(marketplace.fetch_marketplace())
        # Subsequent fetch where the source errors — should use cache.
        import httpx

        def _boom(_headers):
            raise httpx.ConnectError("simulated network failure")

        _FakeAsyncClient.responses = {marketplace.DEFAULT_SOURCE_URL: _boom}
        entries = asyncio.run(marketplace.fetch_marketplace(force=True))
        assert [e.name for e in entries] == ["kt-biome", "kt-template"]

    def test_cold_failure_raises(self):
        import httpx

        def _boom(_headers):
            raise httpx.ConnectError("simulated network failure")

        _FakeAsyncClient.responses = {marketplace.DEFAULT_SOURCE_URL: _boom}
        with pytest.raises(MarketplaceUnavailableError):
            asyncio.run(marketplace.fetch_marketplace())

    def test_multi_source_first_wins(self):
        import yaml as _yaml

        alt = {
            "schema_version": 1,
            "generated": "2026-05-24T00:00:00Z",
            "packages": [
                {
                    "name": "kt-biome",
                    "repo": "https://github.com/forker/kt-biome",
                    "description": "Fork",
                    "tags": ["creatures"],
                    "author": "forker",
                    "license": "MIT",
                    "framework": ">=1.5.0",
                    "versions": [{"tag": "main", "released": "2026-05-24"}],
                }
            ],
        }
        marketplace.add_source("https://fork.test/r.yaml", alias="fork")
        _FakeAsyncClient.responses = {
            marketplace.DEFAULT_SOURCE_URL: _yaml_response(_sample_yaml()),
            "https://fork.test/r.yaml": _yaml_response(_yaml.dump(alt), etag="F1"),
        }
        # ``fetch_marketplace`` returns ALL entries un-deduped — the
        # fork's kt-biome appears alongside the default's kt-biome so
        # ``resolve("@fork/kt-biome")`` can find it (without this
        # change, first-source-wins dedup at this layer would shadow
        # the fork entirely + break the explicit-source spec form).
        entries = asyncio.run(marketplace.fetch_marketplace())
        names = sorted({e.name for e in entries})
        assert names == ["kt-biome", "kt-template"]
        # Both sources' kt-biome are present, in source-priority order.
        biome_entries = [e for e in entries if e.name == "kt-biome"]
        assert [e.source_alias for e in biome_entries] == ["default", "fork"]

        # ``search()`` dedupes user-facing: default wins.
        deduped = asyncio.run(marketplace.search())
        deduped_biome = next(e for e in deduped if e.name == "kt-biome")
        assert deduped_biome.source_alias == "default"


# ── search ──────────────────────────────────────────────────────


class TestSearch:
    def _setup_default(self):
        _FakeAsyncClient.responses = {
            marketplace.DEFAULT_SOURCE_URL: _yaml_response(_sample_yaml()),
        }

    def test_no_filter_returns_all(self):
        self._setup_default()
        assert len(asyncio.run(marketplace.search())) == 2

    def test_substring_match(self):
        self._setup_default()
        out = asyncio.run(marketplace.search("biome"))
        assert [e.name for e in out] == ["kt-biome"]

    def test_tag_filter(self):
        self._setup_default()
        out = asyncio.run(marketplace.search(tag="template"))
        assert [e.name for e in out] == ["kt-template"]

    def test_author_filter(self):
        self._setup_default()
        out = asyncio.run(marketplace.search(author="kohaku-lab"))
        assert len(out) == 2

    def test_query_searches_description(self):
        self._setup_default()
        out = asyncio.run(marketplace.search("starter"))
        assert [e.name for e in out] == ["kt-template"]


# ── resolve ─────────────────────────────────────────────────────


class TestResolve:
    def _setup_default(self):
        _FakeAsyncClient.responses = {
            marketplace.DEFAULT_SOURCE_URL: _yaml_response(_sample_yaml()),
        }

    def test_name_only_picks_newest_non_yanked(self):
        self._setup_default()
        entry, version = asyncio.run(marketplace.resolve("@kt-biome"))
        # v1.2.0 is newest non-yanked; v1.1.0 is yanked so skipped.
        assert version.tag == "v1.2.0"
        assert entry.name == "kt-biome"

    def test_exact_version_allowed_yanked(self):
        self._setup_default()
        entry, version = asyncio.run(marketplace.resolve("@kt-biome@v1.1.0"))
        # Explicit pin returns even yanked versions for reproducibility.
        assert version.tag == "v1.1.0"
        assert version.yanked is True

    def test_unknown_name_raises(self):
        self._setup_default()
        with pytest.raises(MarketplaceNotFoundError):
            asyncio.run(marketplace.resolve("@nope"))

    def test_unknown_version_raises(self):
        self._setup_default()
        with pytest.raises(MarketplaceNotFoundError):
            asyncio.run(marketplace.resolve("@kt-biome@v99.0.0"))

    def test_invalid_spec_raises(self):
        with pytest.raises(InvalidSpecError):
            asyncio.run(marketplace.resolve("kt-biome"))

    def test_source_alias_filter(self):
        # Two sources; @default/kt-biome matches; @fork/kt-biome does
        # not because the fork doesn't have a kt-biome entry.
        marketplace.add_source("https://fork.test/r.yaml", alias="fork")
        empty_alt = {
            "schema_version": 1,
            "generated": "2026-05-24T00:00:00Z",
            "packages": [],
        }
        import yaml as _yaml

        _FakeAsyncClient.responses = {
            marketplace.DEFAULT_SOURCE_URL: _yaml_response(_sample_yaml()),
            "https://fork.test/r.yaml": _yaml_response(_yaml.dump(empty_alt), etag="E"),
        }
        entry, _ = asyncio.run(marketplace.resolve("@default/kt-biome"))
        assert entry.source_alias == "default"
        with pytest.raises(MarketplaceNotFoundError):
            asyncio.run(marketplace.resolve("@fork/kt-biome"))

    def test_source_alias_resolves_shadowed_entry(self):
        # AUDIT FIX #2: a fork entry that's shadowed in the user-facing
        # search view by the default source MUST still be resolvable
        # via the explicit @fork/<name> spec.  Pre-fix, _project did
        # first-source-wins dedup, so the fork's entry never made it
        # into the list that resolve() filtered.
        import yaml as _yaml

        fork_alt = {
            "schema_version": 1,
            "generated": "2026-05-24T00:00:00Z",
            "packages": [
                {
                    "name": "kt-biome",
                    "repo": "https://github.com/forker/kt-biome",
                    "description": "Fork variant",
                    "tags": ["creatures"],
                    "author": "forker",
                    "license": "MIT",
                    "framework": ">=1.5.0",
                    "versions": [{"tag": "main", "released": "2026-05-24"}],
                }
            ],
        }
        marketplace.add_source("https://fork.test/r.yaml", alias="fork")
        _FakeAsyncClient.responses = {
            marketplace.DEFAULT_SOURCE_URL: _yaml_response(_sample_yaml()),
            "https://fork.test/r.yaml": _yaml_response(_yaml.dump(fork_alt), etag="F"),
        }
        # Default source's kt-biome is the one search() would show.
        default_entry, _ = asyncio.run(marketplace.resolve("@kt-biome"))
        assert default_entry.source_alias == "default"
        assert default_entry.repo == "https://github.com/Kohaku-Lab/kt-biome"
        # But the fork's kt-biome MUST resolve via @fork/kt-biome,
        # even though it's shadowed in the un-pinned resolution.
        fork_entry, _ = asyncio.run(marketplace.resolve("@fork/kt-biome"))
        assert fork_entry.source_alias == "fork"
        assert fork_entry.repo == "https://github.com/forker/kt-biome"


# ── Audit fix #1: ref pinning ───────────────────────────────────


class TestInstallSpecRefPinning:
    def test_install_url_returns_repo_only(self):
        # install_url is the public projection — the ref is plumbed
        # through install_package(ref=...) instead of being embedded
        # in the URL.  This test pins that the projection stays a
        # plain repo URL so callers don't end up double-encoding.
        entry, version = asyncio.run(_resolve_kt_biome())
        assert marketplace.install_url(entry, version) == entry.repo


async def _resolve_kt_biome():
    _FakeAsyncClient.responses = {
        marketplace.DEFAULT_SOURCE_URL: _yaml_response(_sample_yaml()),
    }
    return await marketplace.resolve("@kt-biome")


# ── Audit fix #3: framework constraint ──────────────────────────


class TestFrameworkConstraint:
    def _setup_default(self):
        _FakeAsyncClient.responses = {
            marketplace.DEFAULT_SOURCE_URL: _yaml_response(_sample_yaml()),
        }

    def test_skips_incompatible_versions_un_pinned(self, monkeypatch):
        # SAMPLE_REGISTRY's kt-biome declares >=1.5.0,<2.0.0.  Pretend
        # the running framework is 2.5.0 → resolve must raise.
        from packaging.version import Version

        self._setup_default()
        monkeypatch.setattr(
            marketplace, "_current_framework_version", lambda: Version("2.5.0")
        )
        with pytest.raises(IncompatibleFrameworkError) as exc:
            asyncio.run(marketplace.resolve("@kt-biome"))
        # Error must mention the running framework + the constraint
        # so the user knows exactly what to upgrade / downgrade.
        msg = str(exc.value)
        assert "2.5.0" in msg
        assert ">=1.5.0,<2.0.0" in msg

    def test_explicit_version_pin_allows_incompatible(self, monkeypatch, caplog):
        # Explicit @x@v1.2.0 must still install even if the constraint
        # excludes the current framework — reproducibility wins.  The
        # implementation logs a warning so the user is told it
        # happened (pinned here so a future refactor that silently
        # drops the warning can't slip past CI).
        import logging

        from packaging.version import Version

        self._setup_default()
        monkeypatch.setattr(
            marketplace, "_current_framework_version", lambda: Version("2.5.0")
        )
        # kohakuterrarium logger has propagate=False so caplog (root-attached)
        # can't see it normally — flip propagation for this test only.
        kt_logger = logging.getLogger("kohakuterrarium")
        monkeypatch.setattr(kt_logger, "propagate", True)
        with caplog.at_level(
            logging.WARNING, logger="kohakuterrarium.packages.marketplace"
        ):
            entry, version = asyncio.run(marketplace.resolve("@kt-biome@v1.2.0"))
        assert version.tag == "v1.2.0"
        assert entry.name == "kt-biome"
        assert any(
            "incompatible" in r.getMessage().lower() for r in caplog.records
        ), "Expected an incompatibility warning; got: " + str(
            [r.getMessage() for r in caplog.records]
        )

    def test_unknown_framework_version_is_permissive(self, monkeypatch):
        # If we can't read our own version metadata (e.g. dev install,
        # Briefcase bundle stripped of dist-info), the check is
        # skipped — better than refusing every install.
        self._setup_default()
        monkeypatch.setattr(marketplace, "_current_framework_version", lambda: None)
        entry, version = asyncio.run(marketplace.resolve("@kt-biome"))
        assert version.tag == "v1.2.0"

    def test_malformed_constraint_is_permissive(self, monkeypatch):
        # A malformed constraint string in a marketplace entry must
        # not lock out every install — log + allow.
        from packaging.version import Version

        monkeypatch.setattr(
            marketplace, "_current_framework_version", lambda: Version("1.5.0")
        )
        assert marketplace._framework_compatible("not a constraint", Version("1.5.0"))


# ── Audit round-2 fix #4: commit preferred over tag ─────────────


class TestInstallSpecCommitPreferred:
    def test_install_uses_commit_when_set(self, monkeypatch):
        # install_package_spec must pass commit (immutable) over tag
        # (mutable) when both are present in the resolved version.
        from kohakuterrarium.packages import install as install_mod

        entry = MarketplaceEntry(
            name="kt-biome",
            repo="https://github.com/Kohaku-Lab/kt-biome",
            description="",
            tags=("creatures",),
            author="Kohaku-Lab",
            license="MIT",
            framework=">=1.5.0",
            versions=(
                marketplace.MarketplaceVersion(
                    tag="v1.0.0", released="2026-05-01", commit="deadbeef" * 5
                ),
            ),
            source_alias="default",
        )
        version = entry.versions[0]
        captured: dict[str, object] = {}

        def fake_resolve(spec: str):
            return entry, version

        def fake_install(source, *, editable, name_override, ref=None):
            captured["ref"] = ref
            return name_override

        monkeypatch.setattr(install_mod.marketplace, "resolve_sync", fake_resolve)
        monkeypatch.setattr(install_mod, "install_package", fake_install)

        install_mod.install_package_spec("@kt-biome")
        # Commit wins over tag.
        assert captured["ref"] == "deadbeef" * 5

    def test_install_falls_back_to_tag_without_commit(self, monkeypatch):
        from kohakuterrarium.packages import install as install_mod

        entry = MarketplaceEntry(
            name="kt-biome",
            repo="https://github.com/Kohaku-Lab/kt-biome",
            description="",
            tags=("creatures",),
            author="Kohaku-Lab",
            license="MIT",
            framework=">=1.5.0",
            versions=(
                marketplace.MarketplaceVersion(
                    tag="v1.0.0", released="2026-05-01"  # no commit
                ),
            ),
            source_alias="default",
        )
        captured: dict[str, object] = {}

        def fake_install(source, *, editable, name_override, ref=None):
            captured["ref"] = ref
            return name_override

        monkeypatch.setattr(
            install_mod.marketplace,
            "resolve_sync",
            lambda spec: (entry, entry.versions[0]),
        )
        monkeypatch.setattr(install_mod, "install_package", fake_install)
        install_mod.install_package_spec("@kt-biome")
        assert captured["ref"] == "v1.0.0"


# ── Audit fix #5: duplicate alias rejection ─────────────────────


class TestDuplicateAliasRejection:
    def test_duplicate_alias_rejected(self):
        marketplace.add_source("https://a.test/r.yaml", alias="ex")
        with pytest.raises(ValueError, match="Alias already in use"):
            marketplace.add_source("https://b.test/r.yaml", alias="ex")

    def test_duplicate_url_still_rejected(self):
        marketplace.add_source("https://a.test/r.yaml", alias="a")
        with pytest.raises(ValueError, match="already configured"):
            marketplace.add_source("https://a.test/r.yaml", alias="different")


# ── cache TTL boundary ──────────────────────────────────────────


class TestCacheTTL:
    def test_custom_ttl_env(self, monkeypatch):
        monkeypatch.setenv("KT_MARKETPLACE_CACHE_TTL", "0")
        _FakeAsyncClient.responses = {
            marketplace.DEFAULT_SOURCE_URL: _yaml_response(_sample_yaml()),
        }
        asyncio.run(marketplace.fetch_marketplace())
        n_after = len(_FakeAsyncClient.requests)
        # TTL=0 means every call re-fetches.
        asyncio.run(marketplace.fetch_marketplace())
        assert len(_FakeAsyncClient.requests) > n_after


# ── install_url projection ──────────────────────────────────────


def test_install_url_returns_entry_repo():
    entry = MarketplaceEntry(
        name="x",
        repo="https://github.com/o/r",
        description="",
        tags=(),
        author="",
        license="",
        framework="",
        versions=(marketplace.MarketplaceVersion(tag="main", released=""),),
    )
    assert marketplace.install_url(entry, entry.versions[0]) == "https://github.com/o/r"


# ── disk cache survives module re-import ────────────────────────


class TestDiskCache:
    def test_load_existing(self, tmp_path):
        # Pre-seed a cache file as if a previous run had populated it.
        cache_dir = tmp_path / "marketplace"
        cache_dir.mkdir(exist_ok=True)
        cache_payload = {
            "version": 1,
            "sources": {
                marketplace.DEFAULT_SOURCE_URL: {
                    "fetched_at": 1e12,  # far future — guaranteed within TTL
                    "etag": "X",
                    "data": SAMPLE_REGISTRY,
                }
            },
        }
        (cache_dir / "cache.json").write_text(json.dumps(cache_payload))
        entries = asyncio.run(marketplace.fetch_marketplace())
        assert [e.name for e in entries] == ["kt-biome", "kt-template"]
        # Never touched the network.
        assert _FakeAsyncClient.requests == []

    def test_corrupt_cache_resets(self, tmp_path):
        cache_dir = tmp_path / "marketplace"
        cache_dir.mkdir(exist_ok=True)
        (cache_dir / "cache.json").write_text("not json")
        _FakeAsyncClient.responses = {
            marketplace.DEFAULT_SOURCE_URL: _yaml_response(_sample_yaml()),
        }
        entries = asyncio.run(marketplace.fetch_marketplace())
        assert [e.name for e in entries] == ["kt-biome", "kt-template"]


# ── add_source invalidates cache ────────────────────────────────


def test_add_source_invalidates_cache():
    _FakeAsyncClient.responses = {
        marketplace.DEFAULT_SOURCE_URL: _yaml_response(_sample_yaml()),
    }
    asyncio.run(marketplace.fetch_marketplace())
    n_after = len(_FakeAsyncClient.requests)
    marketplace.add_source("https://added.test/r.yaml")
    # Next fetch should re-network because cache was invalidated.
    import yaml as _yaml

    empty = {"schema_version": 1, "generated": "2026-05-24T00:00:00Z", "packages": []}
    _FakeAsyncClient.responses["https://added.test/r.yaml"] = _yaml_response(
        _yaml.dump(empty), etag="E"
    )
    asyncio.run(marketplace.fetch_marketplace())
    assert len(_FakeAsyncClient.requests) > n_after
