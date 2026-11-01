"""Write roots shared by the sandbox's file checks and kernel backends."""

from pathlib import Path

from kohakuterrarium.builtins.plugins.sandbox import roots
from kohakuterrarium.modules.sandbox.presets import WORKSPACE


class TestWriteRoots:
    def test_workspace_level_lists_cwd_temp_and_caches(self, tmp_path, monkeypatch):
        monkeypatch.setattr(roots, "temp_roots", lambda: ["/scratch"])
        assert roots.write_roots(WORKSPACE, tmp_path) == [
            "/dev",
            str(tmp_path),
            "/scratch",
            *roots.CACHE_ROOTS,
        ]

    def test_broad_level_is_the_whole_filesystem(self, tmp_path):
        profile = WORKSPACE.with_overrides(fs_write="broad")
        assert roots.write_roots(profile, tmp_path) == ["/"]

    def test_deny_level_is_devices_only(self, tmp_path):
        profile = WORKSPACE.with_overrides(fs_write="deny")
        assert roots.write_roots(profile, tmp_path) == ["/dev"]

    def test_temp_roots_include_tmpdir(self, monkeypatch):
        monkeypatch.setenv("TMPDIR", "/var/kt-tmp")
        assert roots.temp_roots()[0] == "/tmp"
        assert "/var/kt-tmp" in roots.temp_roots()


class TestDeniedPaths:
    def test_tags_are_dropped(self):
        profile = WORKSPACE.with_overrides(fs_deny=("~/.ssh", "%secrets"))
        assert roots.denied_paths(profile) == ["~/.ssh"]


class TestExpand:
    def test_home_and_variables_expand(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KT_ROOT_TEST", "sub")
        assert roots.expand("~/x", tmp_path) == Path("~/x").expanduser()
        assert roots.expand("$KT_ROOT_TEST/y", tmp_path) == tmp_path / "sub" / "y"

    def test_relative_paths_join_the_cwd(self, tmp_path):
        assert roots.expand("build", tmp_path) == tmp_path / "build"
