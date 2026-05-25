"""Unit tests for :mod:`kohakuterrarium.cli.marketplace`.

Pure formatting + dispatch — every data call is monkeypatched against
the marketplace module, so these tests run with no on-disk state and
no network.
"""

import argparse
import json


from kohakuterrarium.cli import marketplace as cli_mod
from kohakuterrarium.packages import marketplace as data_mod
from kohakuterrarium.packages.marketplace_types import (
    MarketplaceEntry,
    MarketplaceNotFoundError,
    MarketplaceSource,
    MarketplaceUnavailableError,
    MarketplaceVersion,
)


def _ns(**kw):
    return argparse.Namespace(**kw)


def _entry(
    name: str = "kt-biome", tags: tuple[str, ...] = ("creatures", "official")
) -> MarketplaceEntry:
    return MarketplaceEntry(
        name=name,
        repo=f"https://github.com/Kohaku-Lab/{name}",
        description=f"{name} description",
        tags=tags,
        author="Kohaku-Lab",
        license="LicenseRef-KohakuTerrarium-1.0",
        framework=">=1.5.0,<2.0.0",
        versions=(
            MarketplaceVersion(tag="v1.0.0", released="2026-05-01", notes="initial"),
            MarketplaceVersion(tag="v0.9.0", released="2026-04-01"),
        ),
        homepage="",
        source_url=data_mod.DEFAULT_SOURCE_URL,
        source_alias="default",
    )


class TestListSources:
    def test_default_listed(self, monkeypatch, capsys):
        monkeypatch.setattr(
            data_mod,
            "list_sources",
            lambda: [
                MarketplaceSource(alias="default", url=data_mod.DEFAULT_SOURCE_URL)
            ],
        )
        rc = cli_mod.marketplace_cli(_ns(marketplace_command="list"))
        out = capsys.readouterr().out
        assert rc == 0
        assert "default" in out
        assert "(default)" in out
        assert data_mod.DEFAULT_SOURCE_URL in out


class TestAddSource:
    def test_added(self, monkeypatch, capsys):
        captured = []

        def fake_add(url, *, alias=None):
            captured.append((url, alias))
            return MarketplaceSource(alias=alias or url, url=url)

        monkeypatch.setattr(data_mod, "add_source", fake_add)
        monkeypatch.setattr(
            data_mod,
            "list_sources",
            lambda: [
                MarketplaceSource(alias="default", url=data_mod.DEFAULT_SOURCE_URL),
                MarketplaceSource(alias="ex", url="https://ex.test/r.yaml"),
            ],
        )
        rc = cli_mod.marketplace_cli(
            _ns(
                marketplace_command="add",
                url="https://ex.test/r.yaml",
                alias="ex",
            )
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert captured == [("https://ex.test/r.yaml", "ex")]
        assert "Added source" in out
        assert "2 sources now configured" in out

    def test_duplicate_error(self, monkeypatch, capsys):
        def boom(_url, *, alias=None):
            raise ValueError("already configured")

        monkeypatch.setattr(data_mod, "add_source", boom)
        rc = cli_mod.marketplace_cli(
            _ns(marketplace_command="add", url="https://ex.test/r.yaml", alias=None)
        )
        err = capsys.readouterr().err
        assert rc == 1
        assert "already configured" in err


class TestRemoveSource:
    def test_removed(self, monkeypatch, capsys):
        monkeypatch.setattr(data_mod, "remove_source", lambda t: True)
        rc = cli_mod.marketplace_cli(_ns(marketplace_command="remove", target="ex"))
        assert rc == 0
        assert "Removed" in capsys.readouterr().out

    def test_missing(self, monkeypatch, capsys):
        monkeypatch.setattr(data_mod, "remove_source", lambda t: False)
        rc = cli_mod.marketplace_cli(_ns(marketplace_command="remove", target="ex"))
        assert rc == 1
        assert "No source" in capsys.readouterr().err


class TestReset:
    def test_reset_runs(self, monkeypatch, capsys):
        called = []
        monkeypatch.setattr(data_mod, "reset_sources", lambda: called.append("reset"))
        rc = cli_mod.marketplace_cli(_ns(marketplace_command="reset"))
        assert rc == 0
        assert called == ["reset"]
        assert "reset" in capsys.readouterr().out.lower()


class TestRefresh:
    def test_success(self, monkeypatch, capsys):
        monkeypatch.setattr(
            data_mod, "fetch_marketplace_sync", lambda *, force: [_entry(), _entry("x")]
        )
        rc = cli_mod.marketplace_cli(_ns(marketplace_command="refresh"))
        assert rc == 0
        assert "2 package" in capsys.readouterr().out

    def test_unavailable(self, monkeypatch, capsys):
        def boom(*, force):
            raise MarketplaceUnavailableError("offline")

        monkeypatch.setattr(data_mod, "fetch_marketplace_sync", boom)
        rc = cli_mod.marketplace_cli(_ns(marketplace_command="refresh"))
        assert rc == 1
        assert "offline" in capsys.readouterr().err


class TestSearch:
    def test_table_output(self, monkeypatch, capsys):
        monkeypatch.setattr(
            data_mod,
            "search_sync",
            lambda q="", *, tag=None, author=None: [_entry("kt-biome"), _entry("x")],
        )
        rc = cli_mod.marketplace_cli(
            _ns(
                marketplace_command="search",
                query="bio",
                tag=None,
                author=None,
                json=False,
            )
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "kt-biome" in out
        assert "v1.0.0" in out
        assert "2 package" in out
        assert "kt install @<name>" in out

    def test_json_output(self, monkeypatch, capsys):
        monkeypatch.setattr(
            data_mod,
            "search_sync",
            lambda q="", *, tag=None, author=None: [_entry("kt-biome")],
        )
        rc = cli_mod.marketplace_cli(
            _ns(
                marketplace_command="search",
                query=None,
                tag=None,
                author=None,
                json=True,
            )
        )
        out = capsys.readouterr().out
        assert rc == 0
        parsed = json.loads(out)
        assert len(parsed) == 1
        assert parsed[0]["name"] == "kt-biome"
        assert parsed[0]["tags"] == ["creatures", "official"]
        assert parsed[0]["versions"][0]["tag"] == "v1.0.0"

    def test_no_results(self, monkeypatch, capsys):
        monkeypatch.setattr(
            data_mod,
            "search_sync",
            lambda q="", *, tag=None, author=None: [],
        )
        rc = cli_mod.marketplace_cli(
            _ns(
                marketplace_command="search",
                query="nope",
                tag=None,
                author=None,
                json=False,
            )
        )
        assert rc == 0
        assert "No matches" in capsys.readouterr().out


class TestInfo:
    def test_info(self, monkeypatch, capsys):
        entry = _entry()
        monkeypatch.setattr(
            data_mod, "resolve_sync", lambda spec: (entry, entry.versions[0])
        )
        rc = cli_mod.marketplace_cli(_ns(marketplace_command="info", spec="kt-biome"))
        out = capsys.readouterr().out
        assert rc == 0
        assert "kt-biome" in out
        assert "v1.0.0" in out
        assert "Install: kt install @kt-biome" in out

    def test_info_at_prefix_optional(self, monkeypatch):
        captured = []

        def fake_resolve(spec):
            captured.append(spec)
            return _entry(), _entry().versions[0]

        monkeypatch.setattr(data_mod, "resolve_sync", fake_resolve)
        cli_mod.marketplace_cli(_ns(marketplace_command="info", spec="kt-biome"))
        cli_mod.marketplace_cli(_ns(marketplace_command="info", spec="@kt-biome"))
        # Both call sites end up resolving the same spec.
        assert captured == ["@kt-biome", "@kt-biome"]

    def test_not_found(self, monkeypatch, capsys):
        def boom(_spec):
            raise MarketplaceNotFoundError("missing")

        monkeypatch.setattr(data_mod, "resolve_sync", boom)
        rc = cli_mod.marketplace_cli(_ns(marketplace_command="info", spec="x"))
        assert rc == 1
        assert "missing" in capsys.readouterr().err


class TestDispatcher:
    def test_unknown_subcommand(self, monkeypatch, capsys):
        rc = cli_mod.marketplace_cli(_ns(marketplace_command="bogus"))
        assert rc == 2
        assert "Unknown" in capsys.readouterr().err

    def test_no_subcommand_lists_sources(self, monkeypatch, capsys):
        monkeypatch.setattr(
            data_mod,
            "list_sources",
            lambda: [
                MarketplaceSource(alias="default", url=data_mod.DEFAULT_SOURCE_URL)
            ],
        )
        rc = cli_mod.marketplace_cli(_ns())  # no marketplace_command attr at all
        assert rc == 0
        assert "default" in capsys.readouterr().out
