import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"


def _loaded_modules(code: str) -> set[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return set(json.loads(result.stdout))


def test_cli_package_import_does_not_load_command_runtimes():
    loaded = _loaded_modules(
        "import json, sys; import kohakuterrarium.cli; "
        "print(json.dumps(sorted(sys.modules)))"
    )

    assert "kohakuterrarium.serving.web" not in loaded
    assert "kohakuterrarium.cli.run" not in loaded
    assert "kohakuterrarium.cli.admin" not in loaded
    assert not any(name.startswith("fastapi") for name in loaded)
    assert not any(name.startswith("textual") for name in loaded)
    assert not any(name.startswith("prompt_toolkit") for name in loaded)


def test_version_dispatch_does_not_load_command_runtimes():
    loaded = _loaded_modules(
        "import contextlib, io, json, sys; from kohakuterrarium import cli; "
        "sys.argv = ['kt', '--version']; "
        "\nwith contextlib.redirect_stdout(io.StringIO()):\n  cli.main()"
        "\nprint(json.dumps(sorted(sys.modules)))"
    )

    assert "kohakuterrarium.cli._main" not in loaded
    assert "kohakuterrarium.serving.web" not in loaded
    assert not any(name.startswith("fastapi") for name in loaded)
    assert not any(name.startswith("textual") for name in loaded)


def test_default_desktop_dispatch_does_not_load_web_runtime(monkeypatch):
    loaded = _loaded_modules(
        "import json, sys; import kohakuterrarium.serving.desktop as desktop; "
        "desktop.launch_desktop_app = lambda **kwargs: None; "
        "from kohakuterrarium import cli; sys.argv = ['kt']; cli.main(); "
        "print(json.dumps(sorted(sys.modules)))"
    )

    assert "kohakuterrarium.serving.web" not in loaded
    assert "kohakuterrarium.api.app" not in loaded
    assert not any(name.startswith("fastapi") for name in loaded)
    assert not any(name.startswith("uvicorn") for name in loaded)


def test_web_help_does_not_load_terminal_surfaces():
    loaded = _loaded_modules(
        "import contextlib, io, json, sys; from kohakuterrarium import cli; "
        "sys.argv = ['kt', 'web', '--help']; "
        "\ntry:\n  with contextlib.redirect_stdout(io.StringIO()): cli.main()"
        "\nexcept SystemExit: pass\nprint(json.dumps(sorted(sys.modules)))"
    )

    assert "kohakuterrarium.cli._main" not in loaded
    assert "kohakuterrarium.terrarium.engine_cli" not in loaded
    assert "kohakuterrarium.terrarium.engine_rich_cli" not in loaded
    assert not any(name.startswith("textual") for name in loaded)
    assert not any(name.startswith("prompt_toolkit") for name in loaded)


def test_standalone_cli_import_does_not_load_tui_or_web():
    loaded = _loaded_modules(
        "import json, sys; import kohakuterrarium.cli.entry_cli; "
        "print(json.dumps(sorted(sys.modules)))"
    )

    assert "kohakuterrarium.terrarium.engine_cli" not in loaded
    assert "kohakuterrarium.serving.web" not in loaded
    assert not any(name.startswith("textual") for name in loaded)


def test_standalone_tui_import_does_not_load_rich_cli_or_web():
    loaded = _loaded_modules(
        "import json, sys; import kohakuterrarium.cli.entry_tui; "
        "print(json.dumps(sorted(sys.modules)))"
    )

    assert "kohakuterrarium.terrarium.engine_rich_cli" not in loaded
    assert "kohakuterrarium.serving.web" not in loaded
    assert not any(name.startswith("prompt_toolkit") for name in loaded)
