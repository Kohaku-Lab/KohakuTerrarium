"""Installed terminal commands follow the install that is running."""

import pytest

from kohakuterrarium.cli import shims

OLD = ("C:/Old/python.exe", "C:/Old/bootstrap")
NEW = ("C:/New/python.exe", "C:/New/bootstrap")


real_build_plan = shims.build_plan


def _plan(target_dir, runtime):
    return real_build_plan(
        system="Windows",
        bundle=False,
        target_dir=target_dir,
        path_env="",
        runtime=runtime,
    )


@pytest.fixture
def installed(tmp_path, monkeypatch):
    """Shims written by an earlier install at OLD; the running install is NEW."""
    target = tmp_path / "bin"
    shims.apply_plan(_plan(target, OLD))
    monkeypatch.setattr(
        shims, "build_plan", lambda **kw: _plan(target, NEW) if not kw else None
    )
    return target


def test_a_shim_written_by_an_older_install_is_rewritten_for_this_one(installed):
    before = (installed / "kt.cmd").read_text(encoding="utf-8")
    assert "C:/Old/python.exe" in before

    lines = shims.refresh_installed_shims()

    after = (installed / "kt.cmd").read_text(encoding="utf-8")
    assert "C:/New/python.exe" in after and "C:/Old" not in after
    assert 'set "PYTHONPATH=C:/New/bootstrap"' in after
    assert len(lines) == len(shims.SHIM_SUBCOMMANDS)
    assert shims.refresh_installed_shims() == []


def test_verbatim_paths_from_the_shell_are_written_as_plain_paths(tmp_path):
    runtime = (
        "\\\\?\\C:\\Kt\\python\\python.exe",
        "\\\\?\\UNC\\host\\share\\bootstrap",
    )
    shims.apply_plan(_plan(tmp_path / "bin", runtime))

    text = (tmp_path / "bin" / "kt.cmd").read_text(encoding="utf-8")
    assert "\\\\?\\" not in text
    assert '"C:\\Kt\\python\\python.exe" -m' in text
    assert 'set "PYTHONPATH=\\\\host\\share\\bootstrap"' in text


def test_shims_that_already_match_are_left_alone(installed):
    shims.refresh_installed_shims()
    stamp = (installed / "kt.cmd").stat().st_mtime_ns
    assert shims.refresh_installed_shims() == []
    assert (installed / "kt.cmd").stat().st_mtime_ns == stamp


def test_nothing_is_created_and_a_foreign_command_is_never_replaced(
    tmp_path, monkeypatch
):
    target = tmp_path / "bin"
    target.mkdir()
    (target / "kt.cmd").write_text("@echo off\necho mine\n", encoding="utf-8")
    monkeypatch.setattr(
        shims, "build_plan", lambda **kw: _plan(target, NEW) if not kw else None
    )

    assert shims.refresh_installed_shims() == []
    assert (target / "kt.cmd").read_text(encoding="utf-8") == "@echo off\necho mine\n"
    assert not (target / "kt-cli.cmd").exists()
    assert not (target / "kt-tui.cmd").exists()


def test_no_wrappers_means_nothing_to_refresh(tmp_path, monkeypatch):
    plan = shims.ShimPlan(shims.MODE_DEFER_PIPX, tmp_path, "Windows", [], False, "pipx")
    monkeypatch.setattr(shims, "build_plan", lambda **kw: plan)
    assert shims.refresh_installed_shims() == []
