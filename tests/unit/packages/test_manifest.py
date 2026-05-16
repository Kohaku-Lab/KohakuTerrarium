"""Unit tests for :mod:`kohakuterrarium.packages.manifest`.

Covers manifest IO, structural validation, dependency-install hooks
(subprocess stubbed) and the framework-hints reader.
"""

import subprocess


from kohakuterrarium.packages import manifest as man_mod
from kohakuterrarium.packages.manifest import (
    _force_rmtree,
    _install_python_deps,
    _load_manifest,
    _validate_package,
    get_package_framework_hints,
)


class TestLoadManifest:
    def test_reads_kohaku_yaml(self, tmp_path):
        (tmp_path / "kohaku.yaml").write_text("name: alpha\nversion: '2.0'\nfoo: bar")
        data = _load_manifest(tmp_path)
        assert data["name"] == "alpha"
        assert data["version"] == "2.0"
        assert data["foo"] == "bar"

    def test_falls_back_to_yml_extension(self, tmp_path):
        (tmp_path / "kohaku.yml").write_text("name: beta")
        assert _load_manifest(tmp_path)["name"] == "beta"

    def test_no_manifest_returns_dir_name(self, tmp_path):
        pkg = tmp_path / "no_manifest_pkg"
        pkg.mkdir()
        # Missing manifest → synthesised {name: <dirname>}.
        assert _load_manifest(pkg) == {"name": "no_manifest_pkg"}

    def test_empty_manifest_file_returns_empty_dict(self, tmp_path):
        (tmp_path / "kohaku.yaml").write_text("")
        # yaml.safe_load("") is None → coerced to {}.
        assert _load_manifest(tmp_path) == {}


class TestValidatePackage:
    """``_validate_package`` warns (and only warns) for an empty package.

    The single observable behaviour is the warning call, so each test
    spies on ``logger.warning`` and asserts whether it fired.
    """

    def _warn_spy(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            man_mod.logger, "warning", lambda msg, **kw: calls.append(msg)
        )
        return calls

    def test_creatures_dir_passes_silently(self, tmp_path, monkeypatch):
        calls = self._warn_spy(monkeypatch)
        (tmp_path / "creatures").mkdir()
        _validate_package(tmp_path, "p")
        assert calls == []

    def test_terrariums_dir_passes_silently(self, tmp_path, monkeypatch):
        calls = self._warn_spy(monkeypatch)
        (tmp_path / "terrariums").mkdir()
        _validate_package(tmp_path, "p")
        assert calls == []

    def test_manifest_extension_modules_pass(self, tmp_path, monkeypatch):
        calls = self._warn_spy(monkeypatch)
        (tmp_path / "kohaku.yaml").write_text("name: p\ntools:\n  - name: t")
        _validate_package(tmp_path, "p")
        assert calls == []

    def test_empty_package_warns(self, tmp_path, monkeypatch):
        calls = self._warn_spy(monkeypatch)
        # No creatures/, no terrariums/, no manifest extension modules.
        _validate_package(tmp_path, "hollow")
        assert len(calls) == 1
        assert "no creatures" in calls[0].lower()


class TestInstallPythonDeps:
    def test_manifest_deps_invoke_pip(self, tmp_path, monkeypatch):
        (tmp_path / "kohaku.yaml").write_text(
            "name: p\npython_dependencies:\n  - requests\n  - httpx"
        )
        calls = []
        monkeypatch.setattr(
            man_mod.subprocess,
            "run",
            lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0),
        )
        _install_python_deps(tmp_path)
        # pip install was called with the two declared deps.
        assert calls == [["pip", "install", "requests", "httpx"]]

    def test_requirements_txt_invokes_pip(self, tmp_path, monkeypatch):
        (tmp_path / "requirements.txt").write_text("rich\n")
        calls = []
        monkeypatch.setattr(
            man_mod.subprocess,
            "run",
            lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0),
        )
        _install_python_deps(tmp_path)
        assert ["pip", "install", "-r", str(tmp_path / "requirements.txt")] in calls

    def test_no_deps_no_subprocess(self, tmp_path, monkeypatch):
        (tmp_path / "kohaku.yaml").write_text("name: p")
        called = []
        monkeypatch.setattr(
            man_mod.subprocess, "run", lambda *a, **kw: called.append(a)
        )
        _install_python_deps(tmp_path)
        # No deps, no requirements.txt → pip never invoked.
        assert called == []

    def test_dep_install_failure_is_swallowed(self, tmp_path, monkeypatch):
        (tmp_path / "kohaku.yaml").write_text("name: p\npython_dependencies:\n  - x")

        def boom(cmd, **kw):
            raise subprocess.CalledProcessError(1, cmd, stderr=b"boom")

        monkeypatch.setattr(man_mod.subprocess, "run", boom)
        # A failed pip install must not propagate — install continues.
        _install_python_deps(tmp_path)

    def test_requirements_failure_is_swallowed(self, tmp_path, monkeypatch):
        (tmp_path / "requirements.txt").write_text("x\n")

        def boom(cmd, **kw):
            raise subprocess.CalledProcessError(1, cmd, stderr=b"reqfail")

        monkeypatch.setattr(man_mod.subprocess, "run", boom)
        _install_python_deps(tmp_path)


class TestGetPackageFrameworkHints:
    def test_none_root_returns_empty(self):
        assert get_package_framework_hints(None) == {}

    def test_no_hints_section_returns_empty(self, tmp_path):
        (tmp_path / "kohaku.yaml").write_text("name: p")
        assert get_package_framework_hints(tmp_path) == {}

    def test_framework_hints_block_read_and_stringified(self, tmp_path):
        (tmp_path / "kohaku.yaml").write_text(
            "name: p\nframework_hints:\n  tool_syntax: 'use ##'\n  count: 3\n  empty:"
        )
        hints = get_package_framework_hints(tmp_path)
        assert hints["tool_syntax"] == "use ##"
        # Non-string values coerced to str; None → "".
        assert hints["count"] == "3"
        assert hints["empty"] == ""

    def test_framework_hint_overrides_alias_accepted(self, tmp_path):
        (tmp_path / "kohaku.yaml").write_text(
            "name: p\nframework_hint_overrides:\n  k: v"
        )
        assert get_package_framework_hints(tmp_path) == {"k": "v"}

    def test_malformed_hints_section_returns_empty(self, tmp_path):
        # framework_hints is a list, not a dict → ignored.
        (tmp_path / "kohaku.yaml").write_text("name: p\nframework_hints:\n  - a\n  - b")
        assert get_package_framework_hints(tmp_path) == {}


class TestForceRmtree:
    def test_removes_tree_including_readonly_files(self, tmp_path):
        import os
        import stat

        root = tmp_path / "tree"
        sub = root / "sub"
        sub.mkdir(parents=True)
        ro_file = sub / "locked.txt"
        ro_file.write_text("data")
        # Make the file read-only — the onexc/onerror hook must clear it.
        os.chmod(ro_file, stat.S_IREAD)
        _force_rmtree(root)
        assert not root.exists()
