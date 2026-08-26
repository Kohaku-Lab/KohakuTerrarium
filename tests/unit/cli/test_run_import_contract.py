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


def test_run_module_import_defers_engine_and_terminal_surfaces():
    loaded = _loaded_modules(
        "import json, sys; import kohakuterrarium.cli.run; "
        "print(json.dumps(sorted(sys.modules)))"
    )

    assert "kohakuterrarium.terrarium.engine" not in loaded
    assert "kohakuterrarium.terrarium.engine_rich_cli" not in loaded
    assert "kohakuterrarium.terrarium.engine_cli" not in loaded
    assert not any(name.startswith("textual") for name in loaded)
    assert not any(name.startswith("prompt_toolkit") for name in loaded)


def test_rich_cli_runtime_does_not_load_textual():
    loaded = _loaded_modules(
        "import json, sys; import kohakuterrarium.terrarium.engine_rich_cli; "
        "print(json.dumps(sorted(sys.modules)))"
    )

    assert "kohakuterrarium.terrarium.engine_cli" not in loaded
    assert not any(name.startswith("textual") for name in loaded)
