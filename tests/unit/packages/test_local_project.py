"""The local project: the package with the empty name, referenced as ``@/...``."""

from pathlib import Path

import pytest

from kohakuterrarium.errors import PackageError
from kohakuterrarium.packages import locations as loc_mod
from kohakuterrarium.packages.install import uninstall_package, update_package
from kohakuterrarium.packages.locations import (
    ensure_local_project,
    get_package_root,
    project_dir,
)
from kohakuterrarium.packages.walk import list_packages
from kohakuterrarium.studio.catalog import packages_scan
from kohakuterrarium.studio.catalog.packages_scan import (
    invalidate_scan_caches,
    scan_catalog,
    scan_creatures_in_dirs,
    scan_terrariums_in_dirs,
    to_ref,
)


@pytest.fixture
def pkg_dir(tmp_path, monkeypatch):
    d = tmp_path / "packages"
    monkeypatch.setattr(loc_mod, "PACKAGES_DIR", d)
    monkeypatch.setattr(loc_mod, "PROJECT_DIR", None)
    invalidate_scan_caches()
    yield d
    invalidate_scan_caches()


def _creature(root: Path, name: str, description: str = "") -> Path:
    folder = root / "creatures" / name
    folder.mkdir(parents=True)
    (folder / "config.yaml").write_text(
        f"name: {name}\ndescription: {description}\n", encoding="utf-8"
    )
    return folder


class TestLocation:
    def test_sits_beside_a_pinned_packages_dir_and_follows_an_explicit_override(
        self, pkg_dir, tmp_path, monkeypatch
    ):
        assert project_dir() == pkg_dir.parent / "project"
        monkeypatch.setattr(loc_mod, "PROJECT_DIR", tmp_path / "elsewhere")
        assert project_dir() == tmp_path / "elsewhere"

    def test_does_not_exist_until_ensured_then_has_folders_and_a_manifest(
        self, pkg_dir
    ):
        assert get_package_root("") is None
        root = ensure_local_project()
        assert get_package_root("") == root
        assert {p.name for p in root.iterdir()} >= {
            "creatures",
            "terrariums",
            "modules",
            "kohaku.yaml",
        }
        manifest = (root / "kohaku.yaml").read_text(encoding="utf-8")
        assert "name: local" in manifest
        (root / "kohaku.yaml").write_text("name: mine\n", encoding="utf-8")
        ensure_local_project()
        assert (root / "kohaku.yaml").read_text(encoding="utf-8") == "name: mine\n"

    def test_the_empty_name_never_means_the_packages_dir(self, pkg_dir):
        pkg_dir.mkdir()
        assert get_package_root("") is None
        assert get_package_root("  ") is None


class TestListing:
    def test_listed_last_with_its_folders_as_creatures_and_terrariums(self, pkg_dir):
        (pkg_dir / "biome").mkdir(parents=True)
        root = ensure_local_project()
        _creature(root, "helper")
        (root / "creatures" / "not-a-creature").mkdir()
        team = root / "terrariums" / "team"
        team.mkdir(parents=True)
        (team / "terrarium.yaml").write_text("name: team\n", encoding="utf-8")
        packages = list_packages()
        assert [p["name"] for p in packages] == ["biome", ""]
        local = packages[-1]
        assert local["local"] is True and local["editable"] is True
        assert local["manifest_name"] == "local"
        assert local["creatures"] == [{"name": "helper", "path": "creatures/helper"}]
        assert local["terrariums"] == [{"name": "team", "path": "terrariums/team"}]
        assert packages[0]["local"] is False

    def test_listed_without_any_installed_package_and_respects_declared_entries(
        self, pkg_dir
    ):
        root = ensure_local_project()
        _creature(root, "a")
        _creature(root, "b")
        (root / "kohaku.yaml").write_text(
            "name: local\ncreatures:\n  - name: b\n", encoding="utf-8"
        )
        [local] = list_packages()
        assert local["creatures"] == [{"name": "b"}]


class TestRefusals:
    def test_uninstall_refuses_and_leaves_every_package_in_place(self, pkg_dir):
        (pkg_dir / "biome").mkdir(parents=True)
        ensure_local_project()
        with pytest.raises(PackageError, match="cannot be uninstalled"):
            uninstall_package("")
        assert (pkg_dir / "biome").is_dir()
        assert project_dir().is_dir()

    def test_update_refuses(self, pkg_dir):
        ensure_local_project()
        with pytest.raises(PackageError, match="cannot be updated"):
            update_package("")


class TestDiscovery:
    def test_scans_render_project_paths_as_at_slash_refs(self, pkg_dir):
        root = ensure_local_project()
        _creature(root, "helper", "Helps")
        team = root / "terrariums" / "team"
        team.mkdir(parents=True)
        (team / "terrarium.yaml").write_text("name: team\n", encoding="utf-8")
        creatures = scan_creatures_in_dirs([])
        assert {
            "name": "helper",
            "path": "@/creatures/helper",
            "description": "Helps",
        } in creatures
        assert any(
            t["path"] == "@/terrariums/team" for t in scan_terrariums_in_dirs([])
        )
        catalog = {e.name: e for e in scan_catalog()}
        assert catalog["helper"].source == "project"

    def test_to_ref_prefers_the_longest_root_and_renders_the_project_root_as_at(
        self, pkg_dir
    ):
        roots = {
            str(Path("/x/project").resolve()): "",
            str(Path("/x/project/vendor").resolve()): "vendor",
        }
        assert to_ref(Path("/x/project"), roots) == "@"
        assert (
            to_ref(Path("/x/project/modules/tools/a.py"), roots)
            == "@/modules/tools/a.py"
        )
        assert to_ref(Path("/x/project/vendor/b"), roots) == "@vendor/b"
        assert to_ref(Path("/y/c"), roots) == str(Path("/y/c"))

    def test_package_root_map_includes_the_project_without_a_packages_dir(
        self, pkg_dir
    ):
        root = ensure_local_project()
        assert packages_scan._build_package_root_map() == {str(root): ""}
