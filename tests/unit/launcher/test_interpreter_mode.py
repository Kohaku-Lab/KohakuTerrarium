"""Interpreter mode: the embedded stub acting as a ``python`` command line.

Each case runs ``interpreter_mode.run`` in a real child interpreter, exactly as
the stub does after it starts, and checks what that child produced.
"""

import json
import os
import subprocess
import sys
import textwrap

import pytest

from kohakuterrarium.launcher import interpreter_mode
from kohakuterrarium.launcher.tree_ops import write_active_pointer

DRIVER = textwrap.dedent("""
    import sys
    from kohakuterrarium.launcher import interpreter_mode
    sys.exit(interpreter_mode.run(["stub", interpreter_mode.FLAG, *sys.argv[1:]]))
    """)


def _run(args, *, cwd, env=None, stdin=None):
    return subprocess.run(
        [sys.executable, "-c", DRIVER, *args],
        cwd=cwd,
        env={**os.environ, **(env or {})},
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.fixture
def release_home(tmp_path):
    """A config dir whose active release tree provides a marker module."""
    home = tmp_path / "home"
    site = home / "runtime" / "versions" / "9.9.9" / "site-packages"
    site.mkdir(parents=True)
    (site / "ktmarker.py").write_text("VALUE = 'from-release-tree'\n", encoding="utf-8")
    os.environ["KT_CONFIG_DIR"] = str(home)
    try:
        write_active_pointer("9.9.9")
    finally:
        del os.environ["KT_CONFIG_DIR"]
    return home


def test_requested_only_for_the_flag():
    assert interpreter_mode.requested(["stub", interpreter_mode.FLAG, "-c", "1"])
    assert not interpreter_mode.requested(["stub", "cli"])
    assert not interpreter_mode.requested(["stub"])


def test_dash_c_runs_code_with_argv_and_returns_zero(tmp_path):
    result = _run(["-c", "import sys; print(sys.argv)", "a", "b"], cwd=tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "['-c', 'a', 'b']"


def test_exit_status_and_stderr_are_propagated(tmp_path):
    code = _run(["-c", "raise SystemExit(7)"], cwd=tmp_path)
    assert code.returncode == 7
    text = _run(["-c", "raise SystemExit('boom')"], cwd=tmp_path)
    assert text.returncode == 1 and "boom" in text.stderr
    failing = _run(["-c", "1/0"], cwd=tmp_path)
    assert failing.returncode == 1 and "ZeroDivisionError" in failing.stderr


def test_switches_before_the_program_are_skipped(tmp_path):
    result = _run(["-u", "-X", "utf8", "-c", "print('ok')"], cwd=tmp_path)
    assert result.stdout.strip() == "ok"


def test_module_mode_runs_a_module_as_main(tmp_path):
    (tmp_path / "ktrunme.py").write_text(
        "import sys\nprint(__name__, sys.argv[1:])\n", encoding="utf-8"
    )
    result = _run(["-m", "ktrunme", "x"], cwd=tmp_path)
    assert result.stdout.strip() == "__main__ ['x']"


def test_script_mode_runs_a_file_with_its_own_argv(tmp_path):
    script = tmp_path / "tool.py"
    script.write_text(
        "import json, sys\nprint(json.dumps(sys.argv))\n", encoding="utf-8"
    )
    result = _run([str(script), "q"], cwd=tmp_path)
    assert json.loads(result.stdout) == [str(script), "q"]


def test_stdin_program_is_executed(tmp_path):
    result = _run(["-"], cwd=tmp_path, stdin="print('from stdin')\n")
    assert result.stdout.strip() == "from stdin"


def test_missing_program_prints_usage(tmp_path):
    result = _run([], cwd=tmp_path)
    assert result.returncode == 2 and "usage" in result.stderr


def test_active_release_tree_is_importable(tmp_path, release_home):
    result = _run(
        ["-c", "import ktmarker; print(ktmarker.VALUE)"],
        cwd=tmp_path,
        env={"KT_CONFIG_DIR": str(release_home)},
    )
    assert result.stdout.strip() == "from-release-tree"


def test_without_a_pointer_nothing_is_added(tmp_path):
    home = tmp_path / "empty"
    home.mkdir()
    result = _run(
        [
            "-c",
            "import json, sys; print(json.dumps(any('9.9.9' in p for p in sys.path)))",
        ],
        cwd=tmp_path,
        env={"KT_CONFIG_DIR": str(home)},
    )
    assert json.loads(result.stdout) is False


def test_launcher_marker_is_set_for_the_program(tmp_path):
    result = _run(
        ["-c", "import os; print(os.environ.get('KT_LAUNCHER_EXEC'))"], cwd=tmp_path
    )
    assert result.stdout.strip() == "1"


def test_activate_release_keeps_launcher_modules_and_drops_the_rest(tmp_path):
    """Runs in a child: evicting modules would break class identity in this process."""
    site = str(tmp_path / "site")
    script = textwrap.dedent(f"""
        import json, sys
        from kohakuterrarium.launcher import interpreter_mode, paths
        import kohakuterrarium.errors
        interpreter_mode.activate_release({site!r})
        print(json.dumps({{
            "errors_dropped": "kohakuterrarium.errors" not in sys.modules,
            "launcher_kept": sys.modules.get("kohakuterrarium.launcher.paths") is paths,
            "site_first": sys.path[0],
        }}))
        """)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert json.loads(result.stdout) == {
        "errors_dropped": True,
        "launcher_kept": True,
        "site_first": site,
    }
