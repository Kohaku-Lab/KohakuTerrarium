"""Unit tests for the UI-free picker data layer (``cli.select``).

Covers the catalog projection (``enumerate_runnables``), the substring
filter, row flattening, and the ``NavState`` cursor/expand/filter state
machine — the logic the two renderers (cli/tui) sit on top of.
"""

from kohakuterrarium.cli import select
from kohakuterrarium.cli.select import (
    NavState,
    Runnable,
    RunnableGroup,
    _matches,
    enumerate_runnables,
    flatten_rows,
)
from kohakuterrarium.studio.catalog.packages_scan import CatalogEntry


def _r(name, kind="creature", source="local", desc="", members=()):
    return Runnable(
        ref=f"@/{name}",
        name=name,
        kind=kind,
        source=source,
        description=desc,
        members=members,
    )


class TestEnumerateRunnables:
    def test_grouping_sorting_and_refs(self, monkeypatch, tmp_path):
        pkg_root = (tmp_path / "pkg").resolve()
        entries = [
            CatalogEntry(
                name="zeta",
                type="creature",
                path=pkg_root / "creatures" / "zeta",
                source="kt-biome",
                description="z",
                model="gpt",
            ),
            CatalogEntry(
                name="alpha",
                type="creature",
                path=pkg_root / "creatures" / "alpha",
                source="kt-biome",
            ),
            CatalogEntry(
                name="team",
                type="terrarium",
                path=pkg_root / "terrariums" / "team",
                source="kt-biome",
                creatures=["a", "b"],
            ),
            CatalogEntry(
                name="loc",
                type="creature",
                path=tmp_path / "creatures" / "loc",
                source="local",
            ),
        ]
        monkeypatch.setattr(select, "scan_catalog", lambda: entries)
        monkeypatch.setattr(
            select, "_package_roots", lambda: {str(pkg_root): "kt-biome"}
        )

        groups = enumerate_runnables()
        # local sorts first, then packages a→z
        assert [g.source for g in groups] == ["local", "kt-biome"]
        kt = groups[1]
        # creatures alphabetised; terrariums separate
        assert [c.name for c in kt.creatures] == ["alpha", "zeta"]
        assert [t.name for t in kt.terrariums] == ["team"]
        # package path → @ref; local path → original absolute path string
        assert kt.creatures[0].ref == "@kt-biome/creatures/alpha"
        assert kt.terrariums[0].ref == "@kt-biome/terrariums/team"
        assert kt.terrariums[0].members == ("a", "b")
        assert kt.creatures[1].model == "gpt"
        assert groups[0].creatures[0].ref == str(tmp_path / "creatures" / "loc")

    def test_description_collapsed_to_single_line(self, monkeypatch, tmp_path):
        entry = CatalogEntry(
            name="x",
            type="creature",
            path=tmp_path / "x",
            source="local",
            description="line1\n  line2\t line3",
        )
        monkeypatch.setattr(select, "scan_catalog", lambda: [entry])
        monkeypatch.setattr(select, "_package_roots", lambda: {})
        groups = enumerate_runnables()
        assert groups[0].creatures[0].description == "line1 line2 line3"

    def test_empty_catalog_yields_no_groups(self, monkeypatch):
        monkeypatch.setattr(select, "scan_catalog", lambda: [])
        monkeypatch.setattr(select, "_package_roots", lambda: {})
        assert enumerate_runnables() == []


class TestMatches:
    def test_matches_name_description_source(self):
        r = _r("alpha", source="kt-biome", desc="hello world")
        assert _matches(r, "lph")
        assert _matches(r, "world")
        assert _matches(r, "biome")
        assert not _matches(r, "zzz")


def _groups():
    g_local = RunnableGroup("local", (_r("alpha"), _r("beta")), ())
    g_pkg = RunnableGroup(
        "kt-biome",
        (_r("gamma", source="kt-biome"),),
        (_r("team", "terrarium", "kt-biome", members=("x",)),),
    )
    return [g_local, g_pkg]


class TestFlattenRows:
    def test_collapsed_group_hides_entries(self):
        rows = flatten_rows(_groups(), expanded=set(), flt="")
        kinds = [(r.kind, r.source) for r in rows]
        assert kinds == [("header", "local"), ("header", "kt-biome")]

    def test_expanded_group_shows_entries(self):
        rows = flatten_rows(_groups(), expanded={"local"}, flt="")
        entries = [r.runnable.name for r in rows if r.kind == "entry"]
        assert entries == ["alpha", "beta"]

    def test_filter_autoexpands_and_drops_nonmatching(self):
        rows = flatten_rows(_groups(), expanded=set(), flt="team")
        assert [(r.kind, r.source) for r in rows] == [
            ("header", "kt-biome"),
            ("entry", "kt-biome"),
        ]
        assert rows[1].runnable.name == "team"


class TestNavState:
    def test_initial_cursor_on_first_entry(self):
        ns = NavState(groups=_groups())
        assert ns.current().kind == "entry"
        assert ns.current().runnable.name == "alpha"

    def test_move_clamps_to_bounds(self):
        ns = NavState(groups=_groups())
        ns.move(-50)
        assert ns.cursor == 0
        ns.move(50)
        assert ns.cursor == len(ns.rows) - 1

    def test_collapse_from_entry_jumps_to_header_then_collapses(self):
        ns = NavState(groups=_groups())
        ns.cursor = 1  # alpha entry
        # First collapse from an entry jumps to the parent header (tree UX);
        # the group's entries are still shown.
        ns.collapse()
        assert ns.current().kind == "header"
        assert ns.current().source == "local"
        assert any(r.kind == "entry" and r.source == "local" for r in ns.rows)
        # Second collapse (now on the header) hides the group's entries.
        ns.collapse()
        assert "local" not in ns.expanded
        assert all(not (r.kind == "entry" and r.source == "local") for r in ns.rows)

    def test_toggle_header_collapses_then_expands(self):
        ns = NavState(groups=_groups())
        ns.cursor = 0  # local header
        assert ns.toggle() is None
        assert "local" not in ns.expanded
        assert ns.toggle() is None
        assert "local" in ns.expanded

    def test_toggle_entry_returns_ref(self):
        ns = NavState(groups=_groups())
        # move to gamma (kt-biome creature)
        for _ in range(len(ns.rows)):
            row = ns.current()
            if row.kind == "entry" and row.runnable.name == "gamma":
                break
            ns.move(1)
        assert ns.toggle() == "@/gamma"

    def test_filter_then_select(self):
        ns = NavState(groups=_groups())
        for ch in "team":
            ns.add_char(ch)
        assert ns.current().runnable.name == "team"
        assert ns.toggle() == "@/team"

    def test_backspace_restores(self):
        ns = NavState(groups=_groups())
        for ch in "team":
            ns.add_char(ch)
        for _ in range(4):
            ns.backspace()
        assert ns.filter == ""
        # all entries visible again (groups start expanded)
        names = {r.runnable.name for r in ns.rows if r.kind == "entry"}
        assert names == {"alpha", "beta", "gamma", "team"}
