import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import kohakuterrarium.builtins.inputs as builtin_inputs
import kohakuterrarium.builtins.outputs as builtin_outputs
from kohakuterrarium.builtins.inputs import create_builtin_input, list_builtin_inputs
from kohakuterrarium.builtins.outputs import create_builtin_output, list_builtin_outputs
from kohakuterrarium.builtins.tui.input import TUIInput
from kohakuterrarium.builtins.tui.output import TUIOutput

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"


def test_builtin_io_catalog_import_defers_textual():
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json, sys; import kohakuterrarium.builtins.inputs; "
            "import kohakuterrarium.builtins.outputs; "
            "print(json.dumps(sorted(sys.modules)))",
        ],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    loaded = set(json.loads(result.stdout))
    assert not any(name.startswith("textual") for name in loaded)


def test_tui_resolution_does_not_overwrite_concurrent_registration(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    real_import = builtin_inputs.importlib.import_module

    class OverrideInput:
        pass

    def import_module(name):
        entered.set()
        release.wait(timeout=2)
        return real_import(name)

    monkeypatch.delitem(builtin_inputs._BUILTIN_INPUTS, "tui", raising=False)
    monkeypatch.setattr(builtin_inputs.importlib, "import_module", import_module)
    result = []
    worker = threading.Thread(
        target=lambda: result.append(builtin_inputs.get_builtin_input("tui"))
    )
    worker.start()
    entered.wait(timeout=2)
    builtin_inputs.register_builtin_input("tui", OverrideInput)
    release.set()
    worker.join(timeout=2)

    assert builtin_inputs.get_builtin_input("tui") is OverrideInput
    builtin_inputs._BUILTIN_INPUTS.pop("tui", None)


def test_registered_tui_override_does_not_change_public_class(monkeypatch):
    class OverrideInput:
        def __init__(self, **_options):
            pass

    class OverrideOutput:
        def __init__(self, **_options):
            pass

    monkeypatch.setitem(builtin_inputs._BUILTIN_INPUTS, "tui", OverrideInput)
    monkeypatch.setitem(builtin_outputs._BUILTIN_OUTPUTS, "tui", OverrideOutput)

    assert builtin_inputs.TUIInput is TUIInput
    assert builtin_outputs.TUIOutput is TUIOutput
    assert isinstance(create_builtin_input("tui"), OverrideInput)
    assert isinstance(create_builtin_output("tui"), OverrideOutput)


def test_tui_io_remains_listed_constructible_and_exported():
    assert "tui" in list_builtin_inputs()
    assert "tui" in list_builtin_outputs()
    assert builtin_inputs.TUIInput is TUIInput
    assert builtin_outputs.TUIOutput is TUIOutput
    assert isinstance(create_builtin_input("tui"), TUIInput)
    assert isinstance(create_builtin_output("tui"), TUIOutput)
