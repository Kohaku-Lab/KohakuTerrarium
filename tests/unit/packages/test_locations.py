"""Unit tests for :mod:`kohakuterrarium.packages.locations`.

The module is pure filesystem plumbing. Every test points
``locations.PACKAGES_DIR`` at a ``tmp_path`` sandbox via monkeypatch
(the documented test seam) and asserts the real on-disk effect.
"""

from pathlib import Path

import pytest

from kohakuterrarium.packages import locations as loc_mod
from kohakuterrarium.packages.locations import (
    LINK_SUFFIX,
    find_package_root_for_path,
    get_package_path,
    get_package_root,
    read_link,
    remove_link,
    write_link,
)


@pytest.fixture
def pkg_dir(tmp_path, monkeypatch):
    """Redirect the packages directory at a sandbox and return it."""
    d = tmp_path / "packages"
    d.mkdir()
    monkeypatch.setattr(loc_mod, "PACKAGES_DIR", d)
    return d


class TestWriteAndReadLink:
    def test_write_then_read_round_trips_to_target(self, pkg_dir, tmp_path):
        target = tmp_path / "real_pkg"
        target.mkdir()
        write_link("mypkg", target)
        # The .link file physically exists with the resolved target.
        link_file = pkg_dir / f"mypkg{LINK_SUFFIX}"
        assert link_file.exists()
        assert link_file.read_text(encoding="utf-8") == str(target.resolve())
        # read_link returns the live directory.
        assert read_link("mypkg") == target

    def test_read_link_missing_file_returns_none(self, pkg_dir):
        assert read_link("absent") is None

    def test_read_link_dangling_target_returns_none(self, pkg_dir, tmp_path):
        # Point a link at a directory that does not exist.
        gone = tmp_path / "deleted"
        link_file = pkg_dir / f"ghost{LINK_SUFFIX}"
        link_file.write_text(str(gone), encoding="utf-8")
        # Target is not a dir → None, not the bogus path.
        assert read_link("ghost") is None


class TestRemoveLink:
    def test_remove_existing_link_returns_true_and_deletes(self, pkg_dir, tmp_path):
        target = tmp_path / "p"
        target.mkdir()
        write_link("p", target)
        assert remove_link("p") is True
        assert not (pkg_dir / f"p{LINK_SUFFIX}").exists()

    def test_remove_absent_link_returns_false(self, pkg_dir):
        assert remove_link("never_existed") is False


class TestGetPackageRoot:
    def test_link_takes_priority_over_directory(self, pkg_dir, tmp_path):
        # A real dir under PACKAGES_DIR AND a .link pointing elsewhere.
        (pkg_dir / "dup").mkdir()
        link_target = tmp_path / "editable_src"
        link_target.mkdir()
        write_link("dup", link_target)
        # The link wins — editable installs shadow copied dirs.
        assert get_package_root("dup") == link_target

    def test_direct_directory_resolved(self, pkg_dir):
        (pkg_dir / "plain").mkdir()
        root = get_package_root("plain")
        assert root == (pkg_dir / "plain").resolve()

    def test_missing_package_returns_none(self, pkg_dir):
        assert get_package_root("nope") is None

    def test_get_package_path_is_alias_of_root(self, pkg_dir):
        (pkg_dir / "aliased").mkdir()
        assert get_package_path("aliased") == get_package_root("aliased")

    def test_legacy_symlink_resolved(self, pkg_dir, tmp_path):
        # Legacy installs may be a bare symlink under PACKAGES_DIR.
        real = tmp_path / "real_target"
        real.mkdir()
        link = pkg_dir / "linked"
        try:
            link.symlink_to(real, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation not permitted on this platform")
        # is_dir() follows the symlink, so this resolves via the dir branch.
        assert get_package_root("linked") == real.resolve()


class TestFindPackageRootForPath:
    def test_none_input_returns_none(self):
        assert find_package_root_for_path(None) is None

    def test_finds_ancestor_with_kohaku_yaml(self, tmp_path):
        root = tmp_path / "pkg"
        nested = root / "creatures" / "swe"
        nested.mkdir(parents=True)
        (root / "kohaku.yaml").write_text("name: pkg")
        # Walking up from a deep file lands on the manifest-bearing root.
        found = find_package_root_for_path(nested)
        assert found == root.resolve()

    def test_accepts_kohaku_yml_extension(self, tmp_path):
        root = tmp_path / "pkg"
        root.mkdir()
        (root / "kohaku.yml").write_text("name: pkg")
        assert find_package_root_for_path(root) == root.resolve()

    def test_starts_from_parent_when_given_a_file(self, tmp_path):
        root = tmp_path / "pkg"
        root.mkdir()
        (root / "kohaku.yaml").write_text("name: pkg")
        config_file = root / "agent.yaml"
        config_file.write_text("name: a")
        # Given a file, the walk starts at its parent dir.
        assert find_package_root_for_path(config_file) == root.resolve()

    def test_no_manifest_anywhere_returns_none(self, tmp_path):
        lonely = tmp_path / "a" / "b" / "c"
        lonely.mkdir(parents=True)
        assert find_package_root_for_path(lonely) is None

    def test_resolve_oserror_returns_none(self, monkeypatch):
        # A path whose .resolve() blows up must yield None, not raise.
        class _BadPath:
            def resolve(self):
                raise OSError("bad path")

        assert find_package_root_for_path(_BadPath()) is None

    def test_walk_bounded_at_20_levels(self, tmp_path):
        # A path deeper than the safety bound, with no manifest anywhere,
        # returns None instead of looping forever.
        deep = tmp_path
        for i in range(25):
            deep = deep / f"d{i}"
        deep.mkdir(parents=True)
        assert find_package_root_for_path(deep) is None


class TestPackagesDirSeam:
    def test_packages_dir_honours_monkeypatched_str_path(self, tmp_path, monkeypatch):
        # Legacy callers may set PACKAGES_DIR to a str — it must be coerced.
        monkeypatch.setattr(loc_mod, "PACKAGES_DIR", str(tmp_path))
        assert loc_mod._packages_dir() == Path(tmp_path)
