"""Unit tests for :mod:`kohakuterrarium.packages.walk`.

Package enumeration over a sandboxed ``PACKAGES_DIR``. Every test
builds a real directory layout (plain dirs, ``.link`` pointer files)
and asserts the enumerated shape reflects what is on disk.
"""

import asyncio

import pytest

from kohakuterrarium.packages import locations as loc_mod
from kohakuterrarium.packages.walk import (
    get_package_modules,
    list_packages,
    package_snapshot,
)


@pytest.fixture
def pkg_dir(tmp_path, monkeypatch):
    d = tmp_path / "packages"
    d.mkdir()
    monkeypatch.setattr(loc_mod, "PACKAGES_DIR", d)
    return d


def _make_pkg(parent, name, manifest_body=""):
    p = parent / name
    p.mkdir()
    (p / "kohaku.yaml").write_text(f"name: {name}\n{manifest_body}")
    return p


class TestListPackages:
    def test_missing_packages_dir_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(loc_mod, "PACKAGES_DIR", tmp_path / "does_not_exist")
        assert list_packages() == []

    def test_empty_packages_dir_returns_empty(self, pkg_dir):
        assert list_packages() == []

    def test_plain_directory_package_listed(self, pkg_dir):
        _make_pkg(pkg_dir, "alpha", "version: '1.2'\ndescription: a pkg")
        pkgs = list_packages()
        assert len(pkgs) == 1
        assert pkgs[0]["name"] == "alpha"
        assert pkgs[0]["version"] == "1.2"
        assert pkgs[0]["description"] == "a pkg"
        assert pkgs[0]["editable"] is False
        assert pkgs[0]["path"] == str(pkg_dir / "alpha")

    def test_manifest_slot_fields_surface(self, pkg_dir):
        _make_pkg(
            pkg_dir,
            "rich",
            "tools:\n  - name: t\nskills:\n  - name: s\ncommands:\n  - name: c\n"
            "drive_registrations:\n  - name: goal\n    kind: goal",
        )
        pkg = list_packages()[0]
        assert pkg["tools"] == [{"name": "t"}]
        assert pkg["skills"] == [{"name": "s"}]
        assert pkg["commands"] == [{"name": "c"}]
        assert pkg["drive_registrations"] == [{"name": "goal", "kind": "goal"}]
        # Missing slots default to empty lists, not KeyError.
        assert pkg["plugins"] == []
        assert pkg["templates"] == []

    def test_drive_registrations_slot_defaults_empty(self, pkg_dir):
        _make_pkg(pkg_dir, "plain", "version: '1.0'")
        # A package that declares no drive_registrations still carries the key.
        assert list_packages()[0]["drive_registrations"] == []

    def test_link_file_package_listed_as_editable(self, pkg_dir, tmp_path):
        src = tmp_path / "editable_src"
        src.mkdir()
        (src / "kohaku.yaml").write_text("name: edpkg\nversion: '9.9'")
        (pkg_dir / "edpkg.link").write_text(str(src.resolve()))
        pkgs = list_packages()
        assert len(pkgs) == 1
        assert pkgs[0]["name"] == "edpkg"
        assert pkgs[0]["editable"] is True
        assert pkgs[0]["path"] == str(src)

    def test_dangling_link_file_skipped(self, pkg_dir, tmp_path):
        (pkg_dir / "ghost.link").write_text(str(tmp_path / "gone"))
        # A link with no live target is dropped entirely.
        assert list_packages() == []

    def test_duplicate_name_deduplicated(self, pkg_dir, tmp_path):
        # A plain dir AND a .link both named "dup" — the first sorted wins.
        _make_pkg(pkg_dir, "dup", "version: dir")
        src = tmp_path / "dup_src"
        src.mkdir()
        (src / "kohaku.yaml").write_text("name: dup\nversion: link")
        (pkg_dir / "dup.link").write_text(str(src.resolve()))
        pkgs = list_packages()
        # Only one "dup" entry survives.
        assert [p["name"] for p in pkgs] == ["dup"]

    def test_non_package_entries_ignored(self, pkg_dir):
        # A loose file that is not a .link and not a dir.
        (pkg_dir / "README.txt").write_text("hi")
        _make_pkg(pkg_dir, "real", "")
        assert [p["name"] for p in list_packages()] == ["real"]


class TestPackageSnapshot:
    def test_reuses_one_walk_and_refreshes_after_scope(self, pkg_dir, monkeypatch):
        _make_pkg(pkg_dir, "alpha", "version: '1.0'")
        from kohakuterrarium.packages import walk

        calls = 0
        original = walk._list_packages_uncached

        def counted():
            nonlocal calls
            calls += 1
            return original()

        monkeypatch.setattr(walk, "_list_packages_uncached", counted)

        with package_snapshot():
            assert list_packages()[0]["version"] == "1.0"
            (pkg_dir / "alpha" / "kohaku.yaml").write_text(
                "name: alpha\nversion: '2.0'"
            )
            assert list_packages()[0]["version"] == "1.0"

        assert list_packages()[0]["version"] == "2.0"
        assert calls == 2

    def test_copy_mutation_does_not_change_later_reads(self, pkg_dir):
        _make_pkg(pkg_dir, "alpha", "version: '1.0'")

        with package_snapshot():
            first = list_packages()
            first[0]["version"] = "changed"
            first.clear()
            assert list_packages()[0]["version"] == "1.0"

    @pytest.mark.asyncio
    async def test_child_task_does_not_inherit_snapshot(self, pkg_dir):
        _make_pkg(pkg_dir, "alpha", "version: '1.0'")
        release = asyncio.Event()

        async def read_after_scope():
            await release.wait()
            return list_packages()[0]["version"]

        with package_snapshot():
            task = asyncio.create_task(read_after_scope())
            (pkg_dir / "alpha" / "kohaku.yaml").write_text(
                "name: alpha\nversion: '2.0'"
            )

        release.set()
        assert await task == "2.0"

    def test_nested_scopes_share_the_outer_snapshot(self, pkg_dir, monkeypatch):
        _make_pkg(pkg_dir, "alpha", "version: '1.0'")
        from kohakuterrarium.packages import walk

        calls = 0
        original = walk._list_packages_uncached

        def counted():
            nonlocal calls
            calls += 1
            return original()

        monkeypatch.setattr(walk, "_list_packages_uncached", counted)

        with package_snapshot():
            list_packages()
            with package_snapshot():
                list_packages()

        assert calls == 1


class TestGetPackageModules:
    def test_missing_package_returns_empty(self, pkg_dir):
        assert get_package_modules("nonexistent", "tools") == []

    def test_returns_declared_modules_of_kind(self, pkg_dir):
        _make_pkg(
            pkg_dir,
            "toolpkg",
            "tools:\n  - name: a\n    module: m\n  - name: b\n    module: m",
        )
        tools = get_package_modules("toolpkg", "tools")
        assert [t["name"] for t in tools] == ["a", "b"]

    def test_missing_kind_returns_empty(self, pkg_dir):
        _make_pkg(pkg_dir, "p", "tools:\n  - name: a")
        # Package exists but declares no plugins.
        assert get_package_modules("p", "plugins") == []
