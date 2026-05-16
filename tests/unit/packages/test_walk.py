"""Unit tests for :mod:`kohakuterrarium.packages.walk`.

Package enumeration over a sandboxed ``PACKAGES_DIR``. Every test
builds a real directory layout (plain dirs, ``.link`` pointer files)
and asserts the enumerated shape reflects what is on disk.
"""

import pytest

from kohakuterrarium.packages import locations as loc_mod
from kohakuterrarium.packages.walk import get_package_modules, list_packages


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
            "tools:\n  - name: t\nskills:\n  - name: s\ncommands:\n  - name: c",
        )
        pkg = list_packages()[0]
        assert pkg["tools"] == [{"name": "t"}]
        assert pkg["skills"] == [{"name": "s"}]
        assert pkg["commands"] == [{"name": "c"}]
        # Missing slots default to empty lists, not KeyError.
        assert pkg["plugins"] == []
        assert pkg["templates"] == []

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
