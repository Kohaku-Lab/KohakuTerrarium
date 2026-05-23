"""Unit tests for :mod:`kohakuterrarium.packages.install`.

Git operations (clone / pull) are stubbed; local-directory installs
run for real against ``tmp_path``. Every test asserts the on-disk
result: a copied tree, a ``.link`` pointer, or a clean removal.
"""

import pytest

from kohakuterrarium.packages import install as install_mod
from kohakuterrarium.packages import locations as loc_mod
from kohakuterrarium.packages.install import (
    install_package,
    uninstall_package,
    update_package,
)
from kohakuterrarium.packages.locations import LINK_SUFFIX, read_link


@pytest.fixture
def pkg_dir(tmp_path, monkeypatch):
    d = tmp_path / "packages"
    monkeypatch.setattr(loc_mod, "PACKAGES_DIR", d)
    return d


@pytest.fixture
def no_deps(monkeypatch):
    """Stop _install_python_deps from shelling out to pip."""
    monkeypatch.setattr(install_mod, "_install_python_deps", lambda p: None)


def _source_pkg(tmp_path, name="srcpkg", body="version: 1.0"):
    src = tmp_path / name
    (src / "creatures").mkdir(parents=True)
    (src / "kohaku.yaml").write_text(f"name: {name}\n{body}")
    return src


class TestInstallFromLocalCopy:
    def test_copy_install_creates_directory(self, pkg_dir, tmp_path, no_deps):
        src = _source_pkg(tmp_path)
        name = install_package(str(src), editable=False)
        assert name == "srcpkg"
        # The package tree was physically copied under PACKAGES_DIR.
        copied = pkg_dir / "srcpkg"
        assert copied.is_dir()
        assert (copied / "kohaku.yaml").exists()
        assert (copied / "creatures").is_dir()

    def test_name_override_applied(self, pkg_dir, tmp_path, no_deps):
        src = _source_pkg(tmp_path)
        name = install_package(str(src), editable=False, name_override="renamed")
        assert name == "renamed"
        assert (pkg_dir / "renamed").is_dir()

    def test_reinstall_replaces_existing_copy(self, pkg_dir, tmp_path, no_deps):
        src = _source_pkg(tmp_path)
        install_package(str(src), editable=False)
        # Add a stray file to the installed copy, then reinstall.
        stray = pkg_dir / "srcpkg" / "stray.txt"
        stray.write_text("old")
        install_package(str(src), editable=False)
        # The stale tree was wiped before the fresh copy landed.
        assert not stray.exists()
        assert (pkg_dir / "srcpkg" / "kohaku.yaml").exists()


class TestInstallFromLocalEditable:
    def test_editable_install_writes_link_pointer(self, pkg_dir, tmp_path, no_deps):
        src = _source_pkg(tmp_path)
        name = install_package(str(src), editable=True)
        assert name == "srcpkg"
        link_file = pkg_dir / f"srcpkg{LINK_SUFFIX}"
        assert link_file.exists()
        # No copied directory — just the pointer.
        assert not (pkg_dir / "srcpkg").exists()
        assert read_link("srcpkg") == src

    def test_install_replaces_prior_legacy_symlink(self, pkg_dir, tmp_path, no_deps):
        # A legacy symlink install at PACKAGES_DIR/<name> must be unlinked
        # before a fresh copy install lands.
        src = _source_pkg(tmp_path)
        pkg_dir.mkdir(parents=True, exist_ok=True)
        stale_target = tmp_path / "stale"
        stale_target.mkdir()
        link = pkg_dir / "srcpkg"
        try:
            link.symlink_to(stale_target, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation not permitted on this platform")
        install_package(str(src), editable=False)
        # The symlink was replaced by a real copied directory.
        assert (pkg_dir / "srcpkg").is_dir()
        assert not (pkg_dir / "srcpkg").is_symlink()
        assert (pkg_dir / "srcpkg" / "kohaku.yaml").exists()

    def test_editable_replaces_prior_copy_install(self, pkg_dir, tmp_path, no_deps):
        src = _source_pkg(tmp_path)
        install_package(str(src), editable=False)
        assert (pkg_dir / "srcpkg").is_dir()
        # Switching to editable removes the copied dir, leaves a .link.
        install_package(str(src), editable=True)
        assert not (pkg_dir / "srcpkg").exists()
        assert (pkg_dir / f"srcpkg{LINK_SUFFIX}").exists()


class TestInstallErrors:
    def test_non_dir_non_url_source_raises(self, pkg_dir, no_deps):
        with pytest.raises(ValueError, match="Cannot install from"):
            install_package("/no/such/path/anywhere")

    def test_git_url_routes_to_git_install(
        self, pkg_dir, tmp_path, monkeypatch, no_deps
    ):
        # install_package no longer shells out directly — it goes
        # through ``packages.git_backend``.  Stub the backend's
        # clone helper so we don't actually touch the network.
        from kohakuterrarium.packages import git_backend

        captured = {}

        def fake_clone(url, target):
            captured["url"] = url
            captured["target"] = target
            # Simulate a successful clone by creating the target dir.
            target.mkdir(parents=True, exist_ok=True)
            (target / "creatures").mkdir()
            (target / "kohaku.yaml").write_text("name: myrepo")

        monkeypatch.setattr(git_backend, "clone_repo", fake_clone)
        name = install_package("https://example.com/myrepo.git")
        assert name == "myrepo"
        assert captured["url"] == "https://example.com/myrepo.git"
        assert (pkg_dir / "myrepo").is_dir()

    def test_git_clone_failure_raises_runtime_error(
        self, pkg_dir, monkeypatch, no_deps
    ):
        from kohakuterrarium.packages import git_backend

        def boom(url, target):
            raise RuntimeError("Git clone failed: clone denied")

        monkeypatch.setattr(git_backend, "clone_repo", boom)
        with pytest.raises(RuntimeError, match="Git clone failed"):
            install_package("https://example.com/x.git")

    def test_git_install_on_existing_dir_pulls(self, pkg_dir, monkeypatch, no_deps):
        # Pre-create the target so the git path takes the "update" branch.
        target = pkg_dir / "myrepo"
        (target / "creatures").mkdir(parents=True)
        (target / "kohaku.yaml").write_text("name: myrepo")
        from kohakuterrarium.packages import git_backend

        pulled = []

        def fake_pull(t):
            pulled.append(t)

        monkeypatch.setattr(git_backend, "pull_repo", fake_pull)
        name = install_package("https://example.com/myrepo.git")
        assert name == "myrepo"
        # Existing checkout → pull_repo invoked (not clone_repo).
        assert len(pulled) == 1
        assert pulled[0].name == "myrepo"

    def test_git_install_existing_dir_pull_failure_raises(
        self, pkg_dir, monkeypatch, no_deps
    ):
        target = pkg_dir / "myrepo"
        target.mkdir(parents=True)
        (target / "kohaku.yaml").write_text("name: myrepo")
        from kohakuterrarium.packages import git_backend

        def boom(t):
            raise RuntimeError("Git pull failed: pull rejected")

        monkeypatch.setattr(git_backend, "pull_repo", boom)
        with pytest.raises(RuntimeError, match="Git pull failed"):
            install_package("https://example.com/myrepo.git")


class TestUpdatePackage:
    def test_unknown_package_raises_file_not_found(self, pkg_dir):
        with pytest.raises(FileNotFoundError, match="not installed"):
            update_package("ghost")

    def test_non_git_package_raises_runtime_error(self, pkg_dir, tmp_path, no_deps):
        src = _source_pkg(tmp_path)
        install_package(str(src), editable=False)
        # Copied install has no .git → update refuses.
        with pytest.raises(RuntimeError, match="not a git clone"):
            update_package("srcpkg")

    def test_git_pull_runs_and_revalidates(
        self, pkg_dir, tmp_path, monkeypatch, no_deps
    ):
        # Build an "installed git package".
        pkg = pkg_dir / "gitpkg"
        (pkg / ".git").mkdir(parents=True)
        (pkg / "creatures").mkdir()
        (pkg / "kohaku.yaml").write_text("name: gitpkg")
        from kohakuterrarium.packages import git_backend

        calls = []

        def fake_pull(t):
            calls.append(t)

        monkeypatch.setattr(git_backend, "pull_repo", fake_pull)
        name = update_package("gitpkg")
        assert name == "gitpkg"
        # pull_repo was invoked against the gitpkg checkout.
        assert len(calls) == 1
        assert calls[0].name == "gitpkg"

    def test_git_pull_failure_raises_runtime_error(
        self, pkg_dir, tmp_path, monkeypatch, no_deps
    ):
        pkg = pkg_dir / "gitpkg"
        (pkg / ".git").mkdir(parents=True)
        (pkg / "kohaku.yaml").write_text("name: gitpkg")
        from kohakuterrarium.packages import git_backend

        def boom(t):
            raise RuntimeError("Git pull failed: diverged")

        monkeypatch.setattr(git_backend, "pull_repo", boom)
        with pytest.raises(RuntimeError, match="Git pull failed for gitpkg"):
            update_package("gitpkg")


class TestUninstallPackage:
    def test_uninstall_copy_removes_directory(self, pkg_dir, tmp_path, no_deps):
        src = _source_pkg(tmp_path)
        install_package(str(src), editable=False)
        assert (pkg_dir / "srcpkg").is_dir()
        assert uninstall_package("srcpkg") is True
        assert not (pkg_dir / "srcpkg").exists()

    def test_uninstall_editable_removes_link(self, pkg_dir, tmp_path, no_deps):
        src = _source_pkg(tmp_path)
        install_package(str(src), editable=True)
        assert uninstall_package("srcpkg") is True
        assert not (pkg_dir / f"srcpkg{LINK_SUFFIX}").exists()
        # The linked source itself is untouched.
        assert src.is_dir()

    def test_uninstall_missing_package_returns_false(self, pkg_dir):
        assert uninstall_package("never_installed") is False

    def test_uninstall_legacy_symlink_unlinks_it(self, pkg_dir, tmp_path):
        # A legacy symlink-style install: PACKAGES_DIR/<name> -> real dir.
        real = tmp_path / "real_pkg"
        real.mkdir()
        link = pkg_dir / "legacy"
        pkg_dir.mkdir(parents=True, exist_ok=True)
        try:
            link.symlink_to(real, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation not permitted on this platform")
        assert uninstall_package("legacy") is True
        # The symlink is gone; the real target survives.
        assert not link.exists()
        assert real.is_dir()
