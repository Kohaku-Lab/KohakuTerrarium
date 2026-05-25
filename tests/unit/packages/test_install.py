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

        def fake_clone(url, target, ref=None):
            captured["url"] = url
            captured["target"] = target
            captured["ref"] = ref
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

        def boom(url, target, ref=None):
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

    def test_refuses_pinned_install(self, pkg_dir, tmp_path, monkeypatch, no_deps):
        # AUDIT FIX #2 (round-2): a package installed at a pinned ref
        # (recorded in .kt_install_info.json) is on a detached HEAD
        # after ``git clone -b <tag>``; ``git pull --ff-only`` against
        # detached HEAD fails with a confusing message.  update_package
        # must detect the marker and error cleanly, telling the user
        # to ``kt install @<name>@<newversion>`` instead.
        import json

        pkg = pkg_dir / "gitpkg"
        (pkg / ".git").mkdir(parents=True)
        (pkg / "kohaku.yaml").write_text("name: gitpkg")
        (pkg / ".kt_install_info.json").write_text(
            json.dumps({"source": "https://x/y.git", "ref": "v1.0.0"})
        )
        with pytest.raises(RuntimeError, match="pinned ref 'v1.0.0'"):
            update_package("gitpkg")


class TestPinnedReinstallReplacesCheckout:
    """AUDIT FIX #1 (round-2): re-install with a ref must replace, not pull."""

    def test_existing_pkg_with_ref_replaces_checkout(
        self, pkg_dir, tmp_path, monkeypatch, no_deps
    ):
        # Pre-create an "installed" package — this is the bug
        # scenario where ``install_package(url, ref="v2.0.0")`` on an
        # existing checkout used to silently fall through to
        # ``pull_repo`` (leaving the previous ref in place).
        existing = pkg_dir / "myrepo"
        (existing / ".git").mkdir(parents=True)
        (existing / "kohaku.yaml").write_text("name: myrepo")
        (existing / "OLD_MARKER").touch()

        from kohakuterrarium.packages import git_backend

        captured = {}

        def fake_clone(url, target, ref=None):
            captured["url"] = url
            captured["target"] = target
            captured["ref"] = ref
            # Simulate a fresh clone at the requested ref.
            target.mkdir(parents=True, exist_ok=True)
            (target / ".git").mkdir()
            (target / "kohaku.yaml").write_text("name: myrepo")
            (target / "NEW_MARKER").touch()

        def fake_pull(target):
            captured["pulled"] = True

        monkeypatch.setattr(git_backend, "clone_repo", fake_clone)
        monkeypatch.setattr(git_backend, "pull_repo", fake_pull)
        install_package("https://x/myrepo.git", ref="v2.0.0")

        # clone_repo invoked, pull_repo NOT invoked.
        assert captured.get("ref") == "v2.0.0"
        assert "pulled" not in captured
        # Previous checkout torn down — OLD_MARKER gone, NEW_MARKER
        # present.
        assert not (pkg_dir / "myrepo" / "OLD_MARKER").exists()
        assert (pkg_dir / "myrepo" / "NEW_MARKER").exists()

    def test_pinned_reinstall_rolls_back_on_validation_failure(
        self, pkg_dir, tmp_path, monkeypatch, no_deps
    ):
        # AUDIT FIX (round-3): the previous wipe-then-clone left the
        # user with NO package if the new clone or its validation
        # blew up.  Transactional install must keep the old checkout
        # intact when staging fails.
        existing = pkg_dir / "myrepo"
        (existing / ".git").mkdir(parents=True)
        (existing / "kohaku.yaml").write_text("name: myrepo")
        (existing / "OLD_MARKER").touch()

        from kohakuterrarium.packages import git_backend, install as install_mod

        def fake_clone(url, target, ref=None):
            # Simulate a successful clone into the staging dir so
            # _validate_package gets a chance to run and "fail."
            target.mkdir(parents=True, exist_ok=True)
            (target / ".git").mkdir()
            (target / "kohaku.yaml").write_text("name: myrepo")
            (target / "NEW_MARKER").touch()

        def angry_validate(pkg_dir_arg, name):
            raise RuntimeError("simulated post-clone validation failure")

        monkeypatch.setattr(git_backend, "clone_repo", fake_clone)
        monkeypatch.setattr(install_mod, "_validate_package", angry_validate)

        with pytest.raises(RuntimeError, match="simulated post-clone"):
            install_package("https://x/myrepo.git", ref="v2.0.0")

        # OLD install untouched — user keeps a working package.
        assert (pkg_dir / "myrepo").is_dir()
        assert (pkg_dir / "myrepo" / "OLD_MARKER").exists()
        assert (pkg_dir / "myrepo" / ".git").is_dir()
        assert not (pkg_dir / "myrepo" / "NEW_MARKER").exists()
        # Staging + backup dirs cleaned up.
        leftovers = [
            p
            for p in pkg_dir.iterdir()
            if p.name.startswith("myrepo.tmp-") or p.name.startswith("myrepo.bak-")
        ]
        assert leftovers == [], f"transactional install leaked: {leftovers}"

    def test_pinned_reinstall_rolls_back_on_clone_failure(
        self, pkg_dir, tmp_path, monkeypatch, no_deps
    ):
        # Same invariant when the failure is in the clone itself —
        # not just validation.  The staging dir must be torn down and
        # the original install left in place.
        existing = pkg_dir / "myrepo"
        (existing / ".git").mkdir(parents=True)
        (existing / "kohaku.yaml").write_text("name: myrepo")
        (existing / "OLD_MARKER").touch()

        from kohakuterrarium.packages import git_backend

        def angry_clone(url, target, ref=None):
            # Leave a partial dir behind, as a real failing
            # subprocess clone might.
            target.mkdir(parents=True, exist_ok=True)
            (target / "junk").touch()
            raise RuntimeError("simulated clone failure")

        monkeypatch.setattr(git_backend, "clone_repo", angry_clone)

        with pytest.raises(RuntimeError, match="simulated clone"):
            install_package("https://x/myrepo.git", ref="v2.0.0")

        # OLD install untouched.
        assert (pkg_dir / "myrepo" / "OLD_MARKER").exists()
        # Staging cleaned up despite the partial dir the failing
        # clone left behind.
        leftovers = [
            p
            for p in pkg_dir.iterdir()
            if p.name.startswith("myrepo.tmp-") or p.name.startswith("myrepo.bak-")
        ]
        assert leftovers == [], f"transactional install leaked: {leftovers}"

    def test_pinned_reinstall_first_swap_failure_cleans_staging(
        self, pkg_dir, tmp_path, monkeypatch, no_deps
    ):
        # AUDIT FIX (round-4): if the FIRST ``os.replace(target,
        # backup)`` fails (e.g. Windows lock on the existing
        # install), the validated staging clone must still be torn
        # down — otherwise a leftover ``<name>.tmp-<id>`` dir
        # accumulates next to the package on every failed retry.
        existing = pkg_dir / "myrepo"
        (existing / ".git").mkdir(parents=True)
        (existing / "kohaku.yaml").write_text("name: myrepo")
        (existing / "OLD_MARKER").touch()

        from kohakuterrarium.packages import git_backend, install as install_mod

        def fake_clone(url, target, ref=None):
            target.mkdir(parents=True, exist_ok=True)
            (target / ".git").mkdir()
            (target / "kohaku.yaml").write_text("name: myrepo")

        monkeypatch.setattr(git_backend, "clone_repo", fake_clone)

        real_replace = install_mod.os.replace
        calls = {"n": 0}

        def angry_replace(src, dst):
            calls["n"] += 1
            if calls["n"] == 1:
                # Simulate Windows refusing the first move.
                raise OSError(13, "permission denied (simulated)")
            return real_replace(src, dst)

        monkeypatch.setattr(install_mod.os, "replace", angry_replace)

        with pytest.raises(OSError, match="permission denied"):
            install_package("https://x/myrepo.git", ref="v2.0.0")

        # OLD install untouched, no staging leftover.
        assert (pkg_dir / "myrepo" / "OLD_MARKER").exists()
        leftovers = [
            p
            for p in pkg_dir.iterdir()
            if p.name.startswith("myrepo.tmp-") or p.name.startswith("myrepo.bak-")
        ]
        assert (
            leftovers == []
        ), f"staging/backup leaked on first-swap failure: {leftovers}"

    def test_existing_pkg_without_ref_still_pulls(
        self, pkg_dir, tmp_path, monkeypatch, no_deps
    ):
        # The unpinned re-install path must still pull-in-place
        # (existing behaviour for ``kt update``).
        existing = pkg_dir / "myrepo"
        (existing / ".git").mkdir(parents=True)
        (existing / "kohaku.yaml").write_text("name: myrepo")

        from kohakuterrarium.packages import git_backend

        captured = {}

        def fake_pull(target):
            captured["pulled"] = target

        def fake_clone(url, target, ref=None):
            captured["cloned"] = (url, target, ref)
            # Re-create the dir if pull-in-place tore it down (it shouldn't).
            target.mkdir(parents=True, exist_ok=True)
            (target / ".git").mkdir()
            (target / "kohaku.yaml").write_text("name: myrepo")

        monkeypatch.setattr(git_backend, "pull_repo", fake_pull)
        monkeypatch.setattr(git_backend, "clone_repo", fake_clone)
        install_package("https://x/myrepo.git")  # no ref
        # pull_repo invoked, clone_repo NOT invoked.
        assert "pulled" in captured
        assert "cloned" not in captured


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
