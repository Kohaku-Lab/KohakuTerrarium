"""Install a scripted LLM seam inside real subprocess workers and servers for tests.

``KT_TEST_LLM_SCRIPT`` points to a ``{"script": [...]}`` file read whenever a
provider is created, allowing tests to change responses without restarting the
process. An entry is a reply string or a :class:`ScriptEntry` field mapping
(``response``, ``match``, ``delay_per_chunk``, ``chunk_size``). Production
processes do not activate this seam.
"""

import json
import os
from pathlib import Path
from typing import Any

from kohakuterrarium.testing.llm import ScriptedLLM, ScriptEntry
from kohakuterrarium.testing.llm_seam import forbid_real_providers, patch_factories

_INSTALLED = False


def _load_script(path: Path) -> list[Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["OK"]
    script = data.get("script") if isinstance(data, dict) else None
    if not isinstance(script, list):
        return ["OK"]
    return [
        ScriptEntry(**entry) if isinstance(entry, dict) else entry for entry in script
    ]


def maybe_install_test_llm_seam() -> bool:
    """Install the environment-controlled seam once and report activation."""
    global _INSTALLED
    if _INSTALLED:
        return True
    script_path_str = os.environ.get("KT_TEST_LLM_SCRIPT")
    if not script_path_str:
        return False
    script_path = Path(script_path_str)

    def _fake_build(*args, **kwargs):
        return ScriptedLLM(_load_script(script_path))

    patch_factories(setattr, _fake_build)
    forbid_real_providers(setattr)

    _INSTALLED = True
    return True


__all__ = ["maybe_install_test_llm_seam"]
