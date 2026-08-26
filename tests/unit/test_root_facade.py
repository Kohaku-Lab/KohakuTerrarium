import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import kohakuterrarium as kt

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"


def _fresh_python(code: str) -> dict:
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
    return json.loads(result.stdout)


def test_root_import_defers_public_implementation_modules():
    loaded = _fresh_python(
        "import json, sys; import kohakuterrarium; "
        "print(json.dumps(sorted(name for name in sys.modules "
        "if name.startswith('kohakuterrarium'))))"
    )

    assert "kohakuterrarium.studio" not in loaded
    assert "kohakuterrarium.terrarium" not in loaded
    assert "kohakuterrarium.core.agent" not in loaded
    assert "kohakuterrarium.validate" not in loaded
    assert "kohakuterrarium.session.store" not in loaded


def test_public_terrarium_access_registers_group_hooks():
    state = _fresh_python(
        "import json; from kohakuterrarium import Terrarium; "
        "from kohakuterrarium.terrarium import group_hooks; "
        "print(json.dumps([group_hooks._store_attach is not None, "
        "group_hooks._spawnable is not None, "
        "group_hooks._workspace_resolver is not None]))"
    )

    assert state == [True, True, True]


def test_root_exports_resolve_to_real_objects_and_cache():
    from kohakuterrarium.core.agent import Agent
    from kohakuterrarium.session.store import SessionStore
    from kohakuterrarium.studio.studio import Studio
    from kohakuterrarium.terrarium.engine import Terrarium

    assert kt.Agent is Agent
    assert kt.Studio is Studio
    assert kt.Terrarium is Terrarium
    assert kt.SessionStore is SessionStore
    assert kt.__dict__["Agent"] is Agent


def test_root_dir_and_unknown_attribute():
    assert "Agent" in dir(kt)
    assert "Studio" in dir(kt)
    with pytest.raises(AttributeError, match="no attribute 'missing'"):
        kt.missing
