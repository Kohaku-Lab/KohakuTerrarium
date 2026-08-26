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


def test_web_module_import_defers_api_application_graph():
    loaded = _loaded_modules(
        "import json, sys; import kohakuterrarium.serving.web; "
        "print(json.dumps(sorted(sys.modules)))"
    )

    assert "kohakuterrarium.api.app" not in loaded
    assert "kohakuterrarium.terrarium.engine" not in loaded
    assert "kohakuterrarium.llm.anthropic_provider" not in loaded
    assert "kohakuterrarium.builtins.tools.web_fetch" not in loaded


def test_engine_pool_import_defers_terrarium_runtime():
    loaded = _loaded_modules(
        "import json, sys; import kohakuterrarium.api.auth.engine_pool; "
        "print(json.dumps(sorted(sys.modules)))"
    )

    assert "kohakuterrarium.terrarium.engine" not in loaded
    assert "kohakuterrarium.core.agent" not in loaded
    assert "kohakuterrarium.llm.anthropic_provider" not in loaded


def test_api_app_import_defers_provider_and_tool_implementations():
    loaded = _loaded_modules(
        "import json, sys; import kohakuterrarium.api.app; "
        "print(json.dumps(sorted(sys.modules)))"
    )

    assert "kohakuterrarium.llm.anthropic_provider" not in loaded
    assert "kohakuterrarium.llm.openai" not in loaded
    assert "kohakuterrarium.builtins.tools.web_fetch" not in loaded
    assert "kohakuterrarium.builtins.tools.web_search" not in loaded
