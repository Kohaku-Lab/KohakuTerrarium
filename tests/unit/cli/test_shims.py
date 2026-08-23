"""Unit tests for ``cli.shims`` (terminal shim install/status/uninstall).

``build_plan`` is pure (injectable inputs) and tested across the full
(console|bundle)×(posix|windows) matrix without touching disk.  The
filesystem side (``apply_plan`` / uninstall / summary) is exercised on
POSIX with real symlinks/files in ``tmp_path``; symlink-dependent cases
are skipped off-POSIX where ``os.symlink`` needs privilege.
"""

import os

import pytest

from kohakuterrarium.cli import shims

NAMES = ("kt", "kt-cli", "kt-tui")
posix_only = pytest.mark.skipif(
    os.name != "posix", reason="POSIX symlink/exec semantics"
)


class TestBuildPlan:
    def test_console_posix_symlinks_existing_scripts(self, tmp_path):
        src = tmp_path / "bin"
        src.mkdir()
        for n in NAMES:
            (src / n).write_text("#!/bin/sh\n")
        tgt = tmp_path / "local"
        plan = shims.build_plan(
            system="Linux",
            bundle=False,
            scripts_dir=src,
            target_dir=tgt,
            executable=str(src / "python"),
            path_env=str(tgt),
        )
        assert plan.mode == shims.MODE_CONSOLE
        assert plan.on_path is True
        assert {i.name: i.action for i in plan.items} == {
            "kt": "symlink",
            "kt-cli": "symlink",
            "kt-tui": "symlink",
        }

    def test_console_posix_skips_missing_scripts(self, tmp_path):
        src = tmp_path / "bin"
        src.mkdir()
        (src / "kt").write_text("x")
        plan = shims.build_plan(
            system="Linux",
            bundle=False,
            scripts_dir=src,
            target_dir=tmp_path / "t",
            executable="py",
            path_env="",
        )
        actions = {i.name: i.action for i in plan.items}
        assert actions["kt"] == "symlink"
        assert actions["kt-cli"] == "skip"
        assert plan.on_path is False

    def test_bundle_posix_writes_stub_forwarding_wrappers(self, tmp_path):
        plan = shims.build_plan(
            system="Darwin",
            bundle=True,
            target_dir=tmp_path / "b",
            executable="/A/STUB",
            path_env="",
        )
        assert plan.mode == shims.MODE_BUNDLE
        content = {i.name: i.content for i in plan.items}
        assert 'exec "/A/STUB" "$@"' in content["kt"]
        assert 'exec "/A/STUB" cli "$@"' in content["kt-cli"]
        assert 'exec "/A/STUB" tui "$@"' in content["kt-tui"]
        assert all(shims.KT_SHIM_MARKER in c for c in content.values())

    def test_bundle_windows_writes_cmd_wrappers(self, tmp_path):
        plan = shims.build_plan(
            system="Windows",
            bundle=True,
            target_dir=tmp_path / "b",
            executable="C:/A/KT.exe",
            path_env="",
        )
        assert {i.dest.name for i in plan.items} == {
            "kt.cmd",
            "kt-cli.cmd",
            "kt-tui.cmd",
        }
        kc = next(i.content for i in plan.items if i.name == "kt-cli")
        assert '"C:/A/KT.exe" cli %*' in kc

    def test_console_windows_defers_to_pipx(self, tmp_path):
        plan = shims.build_plan(
            system="Windows",
            bundle=False,
            scripts_dir=tmp_path,
            target_dir=tmp_path / "t",
            executable="py",
            path_env="",
        )
        assert plan.mode == shims.MODE_DEFER_PIPX
        assert plan.items == []
        assert "pipx" in plan.note


class TestDetection:
    def test_in_bundle_via_env(self, monkeypatch):
        monkeypatch.setenv("KT_LAUNCHER_EXEC", "1")
        assert shims.in_bundle() is True

    def test_not_bundle_in_plain_env(self, monkeypatch, tmp_path):
        monkeypatch.delenv("KT_LAUNCHER_EXEC", raising=False)
        monkeypatch.setattr(shims.sys, "executable", str(tmp_path / "python"))
        assert shims.in_bundle() is False


class TestApplyAndUninstall:
    @posix_only
    def test_symlink_apply_is_idempotent(self, tmp_path):
        src = tmp_path / "bin"
        src.mkdir()
        for n in NAMES:
            (src / n).write_text("#!/bin/sh\n")
        tgt = tmp_path / "local"
        plan = shims.build_plan(
            system="Linux",
            bundle=False,
            scripts_dir=src,
            target_dir=tgt,
            executable=str(src / "python"),
            path_env=str(tgt),
        )
        shims.apply_plan(plan)
        for n in NAMES:
            assert (tgt / n).is_symlink()
            assert shims._is_ours(tgt / n)
        shims.apply_plan(plan)  # again
        assert sorted(p.name for p in tgt.iterdir()) == sorted(NAMES)

    @posix_only
    def test_uninstall_removes_only_our_shims(self, tmp_path, monkeypatch):
        src = tmp_path / "bin"
        src.mkdir()
        for n in NAMES:
            (src / n).write_text("#!/bin/sh\n")
        tgt = tmp_path / "local"
        plan = shims.build_plan(
            system="Linux",
            bundle=False,
            scripts_dir=src,
            target_dir=tgt,
            executable=str(src / "python"),
            path_env=str(tgt),
        )
        shims.apply_plan(plan)
        monkeypatch.setattr(shims, "build_plan", lambda **kw: plan)
        shims.shims_uninstall()
        assert list(tgt.iterdir()) == []

    def test_bundle_wrapper_apply_writes_executable_marker(self, tmp_path):
        tgt = tmp_path / "b"
        plan = shims.build_plan(
            system="Linux",
            bundle=True,
            target_dir=tgt,
            executable="/A/STUB",
            path_env="",
        )
        shims.apply_plan(plan)
        kc = tgt / "kt-cli"
        assert kc.exists()
        assert shims._is_ours(kc)
        if os.name == "posix":
            assert os.access(kc, os.X_OK)

    def test_apply_leaves_foreign_file_untouched(self, tmp_path):
        tgt = tmp_path / "local"
        tgt.mkdir()
        foreign = tgt / "kt"
        foreign.write_text("a user's own command\n", encoding="utf-8")
        plan = shims.build_plan(
            system="Linux",
            bundle=True,
            target_dir=tgt,
            executable="/A/STUB",
            path_env="",
        )

        messages = shims.apply_plan(plan)

        assert foreign.read_text(encoding="utf-8") == "a user's own command\n"
        assert any(
            "skip kt:" in message and "not a kohaku" in message for message in messages
        )

    def test_uninstall_leaves_foreign_file(self, tmp_path, monkeypatch):
        tgt = tmp_path / "local"
        tgt.mkdir()
        (tgt / "kt").write_text("a user's own script, no marker\n")
        plan = shims.build_plan(
            system="Linux",
            bundle=False,
            scripts_dir=tmp_path,
            target_dir=tgt,
            executable="py",
            path_env="",
        )
        monkeypatch.setattr(shims, "build_plan", lambda **kw: plan)
        shims.shims_uninstall()
        assert (tgt / "kt").exists()


class TestSummary:
    def test_not_installed(self, tmp_path, monkeypatch):
        plan = shims.build_plan(
            system="Linux",
            bundle=False,
            scripts_dir=tmp_path,
            target_dir=tmp_path / "empty",
            executable="py",
            path_env="",
        )
        monkeypatch.setattr(shims, "build_plan", lambda **kw: plan)
        assert "not installed" in shims.shims_summary()

    @posix_only
    def test_installed_reports_path_state(self, tmp_path, monkeypatch):
        src = tmp_path / "bin"
        src.mkdir()
        for n in NAMES:
            (src / n).write_text("x")
        tgt = tmp_path / "local"
        plan = shims.build_plan(
            system="Linux",
            bundle=False,
            scripts_dir=src,
            target_dir=tgt,
            executable=str(src / "python"),
            path_env=str(tgt),
        )
        shims.apply_plan(plan)
        monkeypatch.setattr(shims, "build_plan", lambda **kw: plan)
        summary = shims.shims_summary()
        assert "kt" in summary
        assert "on PATH" in summary
