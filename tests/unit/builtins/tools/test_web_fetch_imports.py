import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SRC = ROOT / "src"


def test_web_fetch_import_does_not_load_optional_extractors():
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json, sys; import kohakuterrarium.builtins.tools.web_fetch; "
            "print(json.dumps(sorted(sys.modules)))",
        ],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    loaded = set(json.loads(result.stdout))

    assert "crawl4ai" not in loaded
    assert "trafilatura" not in loaded
