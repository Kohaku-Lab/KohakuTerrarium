"""Shims for a native-shell install that ships its own interpreter.

The wrapper scripts are written and then executed for real, with a stand-in
launcher package, so what the user types in a terminal is what is tested.
"""

import json
import platform
import subprocess
import sys
import textwrap

import pytest

from kohakuterrarium.cli import shims

NAMES = ("kt", "kt-cli", "kt-tui")


@pytest.fixture
def bootstrap(tmp_path):
    """A launcher stand-in that reports how it was started."""
    root = tmp_path / "bootstrap"
    package = root / "kohakuterrarium" / "launcher"
    package.mkdir(parents=True)
    (root / "kohakuterrarium" / "__init__.py").write_text("", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        textwrap.dedent("""
            import json, os, sys
            print(json.dumps({
                "argv": sys.argv[1:],
                "pythonpath": os.environ.get("PYTHONPATH"),
                "nousersite": os.environ.get("PYTHONNOUSERSITE"),
            }))
            """),
        encoding="utf-8",
    )
    return root


def _plan(tmp_path, bootstrap, system):
    return shims.build_plan(
        system=system,
        runtime=(sys.executable, str(bootstrap)),
        target_dir=tmp_path / "bin",
        path_env="",
    )


@pytest.mark.parametrize("system", ["Windows", "Linux", "Darwin"])
def test_runtime_install_wraps_the_launcher_for_every_command(
    tmp_path, bootstrap, system
):
    plan = _plan(tmp_path, bootstrap, system)

    assert plan.mode == shims.MODE_RUNTIME
    assert [item.name for item in plan.items] == list(NAMES)
    for item in plan.items:
        assert item.action == "wrapper"
        assert "-m kohakuterrarium.launcher" in item.content
        assert shims.KT_SHIM_MARKER in item.content
        assert str(bootstrap) in item.content and sys.executable in item.content
    by_name = {item.name: item.content for item in plan.items}
    assert "launcher cli" in by_name["kt-cli"] and "launcher tui" in by_name["kt-tui"]
    assert "launcher cli" not in by_name["kt"]


def test_windows_wrapper_keeps_its_environment_local(tmp_path, bootstrap):
    content = _plan(tmp_path, bootstrap, "Windows").items[0].content

    assert content.index("setlocal") < content.index("PYTHONPATH")
    assert content.rstrip().endswith("exit /b %ERRORLEVEL%")


def test_runtime_takes_precedence_over_the_briefcase_stub(tmp_path, bootstrap):
    plan = shims.build_plan(
        system="Linux",
        bundle=True,
        runtime=(sys.executable, str(bootstrap)),
        target_dir=tmp_path / "bin",
        path_env="",
    )
    assert plan.mode == shims.MODE_RUNTIME


def test_environment_decides_whether_a_runtime_is_present(monkeypatch, tmp_path):
    monkeypatch.delenv("KT_PYTHON", raising=False)
    monkeypatch.delenv("KT_BOOTSTRAP_DIR", raising=False)
    assert shims.bundled_runtime() is None
    monkeypatch.setenv("KT_PYTHON", "/p/python")
    assert shims.bundled_runtime() is None
    monkeypatch.setenv("KT_BOOTSTRAP_DIR", "/p/bootstrap")
    assert shims.bundled_runtime() == ("/p/python", "/p/bootstrap")


def test_installed_wrapper_forwards_arguments_and_keeps_the_caller_environment(
    tmp_path, bootstrap
):
    system = platform.system()
    plan = _plan(tmp_path, bootstrap, system)
    shims.apply_plan(plan)
    command = shims.shim_path(plan, "kt-cli")
    assert shims.is_ours(command)

    result = subprocess.run(
        [str(command), "run", "--flag", "two words"],
        capture_output=True,
        text=True,
        timeout=60,
        shell=system == "Windows",
    )

    assert result.returncode == 0, result.stderr
    seen = json.loads(result.stdout.splitlines()[-1])
    assert seen["argv"] == ["cli", "run", "--flag", "two words"]
    assert seen["pythonpath"] == str(bootstrap)
    assert seen["nousersite"] == "1"


def test_wrapper_propagates_the_launcher_exit_status(tmp_path, bootstrap):
    (bootstrap / "kohakuterrarium" / "launcher" / "__main__.py").write_text(
        "raise SystemExit(7)\n", encoding="utf-8"
    )
    system = platform.system()
    plan = _plan(tmp_path, bootstrap, system)
    shims.apply_plan(plan)

    result = subprocess.run(
        [str(shims.shim_path(plan, "kt"))],
        capture_output=True,
        text=True,
        timeout=60,
        shell=system == "Windows",
    )

    assert result.returncode == 7
