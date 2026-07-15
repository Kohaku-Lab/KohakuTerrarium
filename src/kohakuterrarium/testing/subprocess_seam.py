"""Install a scripted LLM seam inside real subprocess workers for tests.

``KT_TEST_LLM_SCRIPT`` points to a ``{"script": [...]}`` file read whenever a
provider is created, allowing tests to change responses without restarting the
worker. Production processes do not activate this seam.
"""

import json
import os
from pathlib import Path
from typing import Any

from kohakuterrarium.bootstrap import llm as _bootstrap_llm
from kohakuterrarium.testing.llm import ScriptedLLM

_INSTALLED = False


def _load_script(path: Path) -> list[Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["OK"]
    script = data.get("script") if isinstance(data, dict) else None
    if not isinstance(script, list):
        return ["OK"]
    return script


def maybe_install_test_llm_seam() -> bool:
    """Install the environment-controlled seam once and report activation."""
    global _INSTALLED
    if _INSTALLED:
        return True
    script_path_str = os.environ.get("KT_TEST_LLM_SCRIPT")
    if not script_path_str:
        return False
    script_path = Path(script_path_str)

    def _fake_create(config, llm=None):
        return ScriptedLLM(_load_script(script_path))

    def _fake_from_profile(name):
        return ScriptedLLM(_load_script(script_path))

    _bootstrap_llm.create_llm_provider = _fake_create
    _bootstrap_llm.create_llm_from_profile_name = _fake_from_profile

    # Patch modules that may already hold imported factory references.
    try:
        from kohakuterrarium.bootstrap import agent_init as _agent_init

        _agent_init.create_llm_provider = _fake_create
    except ImportError:
        pass
    try:
        from kohakuterrarium.core import agent_model as _agent_model

        _agent_model.create_llm_from_profile_name = _fake_from_profile
    except ImportError:
        pass
    try:
        from kohakuterrarium.core import agent_compact as _agent_compact

        _agent_compact.create_llm_from_profile_name = _fake_from_profile
    except ImportError:
        pass

    _INSTALLED = True
    return True


__all__ = ["maybe_install_test_llm_seam"]
