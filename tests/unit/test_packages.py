"""Tests for the package manager (kt install/list/resolve)."""

from pathlib import Path

import pytest
import yaml

import kohakuterrarium.packages as pkg_mod
from kohakuterrarium.packages import (
    install_package,
    is_package_ref,
    list_packages,
    resolve_package_path,
    uninstall_package,
)


@pytest.fixture
def tmp_packages(tmp_path, monkeypatch):
    """Use a temporary directory for packages instead of ~/.kohakuterrarium/packages."""
    import kohakuterrarium.packages as pkg_mod

    monkeypatch.setattr(pkg_mod, "PACKAGES_DIR", tmp_path / "packages")
    (tmp_path / "packages").mkdir()
    return tmp_path / "packages"


@pytest.fixture
def sample_package(tmp_path):
    """Create a minimal package directory for testing."""
    pkg = tmp_path / "test-pack"
    pkg.mkdir()
    (pkg / "creatures").mkdir()
    (pkg / "creatures" / "my-agent").mkdir()
    (pkg / "creatures" / "my-agent" / "config.yaml").write_text(
        yaml.dump({"name": "my-agent", "version": "1.0"})
    )
    (pkg / "creatures" / "my-agent" / "prompts").mkdir()
    (pkg / "creatures" / "my-agent" / "prompts" / "system.md").write_text(
        "# My Agent\nYou are helpful."
    )
    (pkg / "terrariums").mkdir()
    (pkg / "terrariums" / "my-team").mkdir()
    (pkg / "terrariums" / "my-team" / "terrarium.yaml").write_text(
        yaml.dump({"terrarium": {"name": "my-team", "creatures": []}})
    )
    (pkg / "kohaku.yaml").write_text(
        yaml.dump(
            {
                "name": "test-pack",
                "version": "1.0.0",
                "description": "Test package",
                "creatures": [
                    {"name": "my-agent", "path": "creatures/my-agent"},
                ],
                "terrariums": [
                    {"name": "my-team", "path": "terrariums/my-team"},
                ],
            }
        )
    )
    return pkg


class TestIsPackageRef:
    def test_at_prefix(self):
        assert is_package_ref("@kt-defaults/creatures/swe")

    def test_no_prefix(self):
        assert not is_package_ref("creatures/swe")

    def test_relative_path(self):
        assert not is_package_ref("../general")

    def test_none(self):
        assert not is_package_ref(None)

    def test_empty(self):
        assert not is_package_ref("")


class TestForceRmtree:
    def test_uses_onexc_on_python_312_plus(self, monkeypatch, tmp_path):
        called = {}

        def fake_rmtree(path, **kwargs):
            called["path"] = path
            called.update(kwargs)

        monkeypatch.setattr(pkg_mod.shutil, "rmtree", fake_rmtree)
        monkeypatch.setattr(pkg_mod.sys, "version_info", (3, 12, 0))

        pkg_mod._force_rmtree(tmp_path)

        assert called["path"] == tmp_path
        assert "onexc" in called
        assert "onerror" not in called

    def test_uses_onerror_before_python_312(self, monkeypatch, tmp_path):
        called = {}

        def fake_rmtree(path, **kwargs):
            called["path"] = path
            called.update(kwargs)

        monkeypatch.setattr(pkg_mod.shutil, "rmtree", fake_rmtree)
        monkeypatch.setattr(pkg_mod.sys, "version_info", (3, 11, 0))

        pkg_mod._force_rmtree(tmp_path)

        assert called["path"] == tmp_path
        assert "onerror" in called
        assert "onexc" not in called


class TestInstallLocal:
    def test_install_copy(self, tmp_packages, sample_package):
        name = install_package(str(sample_package), editable=False)
        assert name == "test-pack"
        installed = tmp_packages / "test-pack"
        assert installed.is_dir()
        assert not installed.is_symlink()
        assert (installed / "kohaku.yaml").exists()
        assert (installed / "creatures" / "my-agent" / "config.yaml").exists()

    def test_install_editable(self, tmp_packages, sample_package):
        name = install_package(str(sample_package), editable=True)
        assert name == "test-pack"
        # Editable uses a .link pointer file, not a symlink
        link_file = tmp_packages / "test-pack.link"
        assert link_file.exists()
        assert Path(link_file.read_text().strip()) == sample_package.resolve()
        # No directory should be created
        assert not (tmp_packages / "test-pack").exists()

    def test_install_name_override(self, tmp_packages, sample_package):
        name = install_package(str(sample_package), name_override="custom-name")
        assert name == "custom-name"
        assert (tmp_packages / "custom-name").exists()

    def test_reinstall_overwrites(self, tmp_packages, sample_package):
        install_package(str(sample_package))
        # Modify source
        (sample_package / "NEW_FILE").write_text("new")
        install_package(str(sample_package))
        # Should have the new file
        assert (tmp_packages / "test-pack" / "NEW_FILE").exists()


class TestUninstall:
    def test_uninstall_copy(self, tmp_packages, sample_package):
        install_package(str(sample_package))
        assert uninstall_package("test-pack")
        assert not (tmp_packages / "test-pack").exists()

    def test_uninstall_editable(self, tmp_packages, sample_package):
        install_package(str(sample_package), editable=True)
        assert uninstall_package("test-pack")
        assert not (tmp_packages / "test-pack.link").exists()
        # Source should still exist
        assert sample_package.exists()

    def test_uninstall_nonexistent(self, tmp_packages):
        assert not uninstall_package("no-such-package")


class TestUpdatePackage:
    """Regression tests for `kt update` / `update_package`."""

    def _make_git_repo(self, tmp_path, name: str):
        """Create a git repo with a sample package and first commit.

        Path ends with ``.git`` so ``install_package`` routes it through the
        git branch (same dispatch rule it uses for real URLs).
        """
        import subprocess

        src = tmp_path / f"{name}.git"
        src.mkdir()
        (src / "kohaku.yaml").write_text(yaml.dump({"name": name, "version": "1.0.0"}))
        subprocess.run(["git", "init", "-q"], check=True, cwd=src)
        subprocess.run(
            ["git", "-c", "user.email=a@b", "-c", "user.name=t", "add", "-A"],
            check=True,
            cwd=src,
        )
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=a@b",
                "-c",
                "user.name=t",
                "commit",
                "-q",
                "-m",
                "v1",
            ],
            check=True,
            cwd=src,
        )
        return src

    def test_update_pulls_from_remote(self, tmp_packages, tmp_path, monkeypatch):
        """Regression: `kt update` must run git pull, not re-copy the local install.

        Previously `_update_package` called `install_package(str(path))` where
        path was the local install dir — `install_package` then routed that
        through the local-dir branch, so an installed git clone was never
        updated. This test pins the fix: a change on the remote must land in
        the installed copy after `update_package(name)`.
        """
        import subprocess

        from kohakuterrarium.packages import install_package, update_package

        # Skip on systems where git is not available.
        if not subprocess.run(["which", "git"], capture_output=True).stdout.strip():
            pytest.skip("git not available")

        remote = self._make_git_repo(tmp_path, "gitpack")
        # Source path ends in .git so install_package dispatches to the git
        # branch and clones it (same rule as real remote URLs).
        install_package(str(remote))
        installed = tmp_packages / "gitpack"
        assert (installed / ".git").exists(), "install should produce a git clone"

        # Add a new commit on the remote with a sentinel file + version bump.
        (remote / "NEW.txt").write_text("added after install")
        (remote / "kohaku.yaml").write_text(
            yaml.dump({"name": "gitpack", "version": "2.0.0"})
        )
        subprocess.run(
            ["git", "-c", "user.email=a@b", "-c", "user.name=t", "add", "-A"],
            check=True,
            cwd=remote,
        )
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=a@b",
                "-c",
                "user.name=t",
                "commit",
                "-q",
                "-m",
                "v2",
            ],
            check=True,
            cwd=remote,
        )

        # Before update: sentinel must not be present yet.
        assert not (installed / "NEW.txt").exists()

        # Act.
        update_package("gitpack")

        # After update: pull landed, sentinel present, manifest bumped.
        assert (
            installed / "NEW.txt"
        ).exists(), "update_package should have pulled the remote commit"
        assert "2.0.0" in (installed / "kohaku.yaml").read_text()

    def test_update_rejects_local_install(self, tmp_packages, sample_package):
        """A non-git local install must not be treated as updatable."""
        from kohakuterrarium.packages import install_package, update_package

        install_package(str(sample_package))
        with pytest.raises(RuntimeError, match="not a git clone"):
            update_package("test-pack")

    def test_update_unknown_package(self, tmp_packages):
        from kohakuterrarium.packages import update_package

        with pytest.raises(FileNotFoundError):
            update_package("no-such-pack")


class TestListPackages:
    def test_empty(self, tmp_packages):
        assert list_packages() == []

    def test_list_installed(self, tmp_packages, sample_package):
        install_package(str(sample_package))
        pkgs = list_packages()
        assert len(pkgs) == 1
        assert pkgs[0]["name"] == "test-pack"
        assert pkgs[0]["version"] == "1.0.0"
        assert pkgs[0]["editable"] is False
        assert len(pkgs[0]["creatures"]) == 1
        assert len(pkgs[0]["terrariums"]) == 1

    def test_list_editable(self, tmp_packages, sample_package):
        install_package(str(sample_package), editable=True)
        pkgs = list_packages()
        assert len(pkgs) == 1
        assert pkgs[0]["editable"] is True

    def test_list_multiple(self, tmp_packages, sample_package, tmp_path):
        install_package(str(sample_package))
        # Create second package
        pkg2 = tmp_path / "other-pack"
        pkg2.mkdir()
        (pkg2 / "creatures").mkdir()
        (pkg2 / "kohaku.yaml").write_text(
            yaml.dump({"name": "other-pack", "version": "2.0"})
        )
        install_package(str(pkg2))
        pkgs = list_packages()
        assert len(pkgs) == 2
        names = {p["name"] for p in pkgs}
        assert names == {"test-pack", "other-pack"}


class TestResolvePackagePath:
    def test_resolve_creature(self, tmp_packages, sample_package):
        install_package(str(sample_package))
        path = resolve_package_path("@test-pack/creatures/my-agent")
        assert path.is_dir()
        assert (path / "config.yaml").exists()

    def test_resolve_terrarium(self, tmp_packages, sample_package):
        install_package(str(sample_package))
        path = resolve_package_path("@test-pack/terrariums/my-team")
        assert path.is_dir()
        assert (path / "terrarium.yaml").exists()

    def test_resolve_package_root(self, tmp_packages, sample_package):
        install_package(str(sample_package))
        path = resolve_package_path("@test-pack")
        assert path.is_dir()
        assert (path / "kohaku.yaml").exists()

    def test_resolve_not_installed(self, tmp_packages):
        with pytest.raises(FileNotFoundError, match="Package not installed"):
            resolve_package_path("@nonexistent/creatures/foo")

    def test_resolve_bad_path(self, tmp_packages, sample_package):
        install_package(str(sample_package))
        with pytest.raises(FileNotFoundError, match="Path not found"):
            resolve_package_path("@test-pack/no/such/path")

    def test_resolve_no_at(self):
        with pytest.raises(ValueError, match="must start with @"):
            resolve_package_path("test-pack/creatures/foo")

    def test_resolve_editable(self, tmp_packages, sample_package):
        install_package(str(sample_package), editable=True)
        path = resolve_package_path("@test-pack/creatures/my-agent")
        # Resolved path should point to the actual source
        assert path.is_dir()
        assert (path / "config.yaml").exists()


class TestConfigResolution:
    """Test that @package refs work in config loading."""

    def test_base_config_package_ref(self, tmp_packages, sample_package):
        """Verify _resolve_base_config_path handles @package refs."""
        install_package(str(sample_package))
        from kohakuterrarium.core.config import _resolve_base_config_path

        result = _resolve_base_config_path(
            "@test-pack/creatures/my-agent", Path("/dummy")
        )
        assert result is not None
        assert result.is_dir()
        assert (result / "config.yaml").exists()

    def test_base_config_quoted_ref(self, tmp_packages, sample_package):
        """YAML may quote the @ symbol."""
        install_package(str(sample_package))
        from kohakuterrarium.core.config import _resolve_base_config_path

        result = _resolve_base_config_path(
            '"@test-pack/creatures/my-agent"', Path("/dummy")
        )
        assert result is not None
