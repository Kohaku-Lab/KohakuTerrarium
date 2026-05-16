"""Test-only LLM seam for subprocess workers.

Tests that boot a real ``kt lab-client`` subprocess cannot reach into
that subprocess to monkey-patch ``bootstrap.llm.create_llm_provider``.
This module gives them a controlled seam they activate via env var
instead: when ``KT_TEST_LLM_SCRIPT`` points at a JSON file, every call
to the LLM factory in the subprocess returns a fresh
:class:`ScriptedLLM` whose script is read from that file.

The seam is a **test-only** entry point — production runs never set
the env var, never import this module, and never wrap the factories.
The wrapper preserves the real factory's ``__name__`` so other
monkeypatch points (e.g. ``agent_init.create_llm_provider``) keep
pointing at the same wrapped callable.

JSON file shape::

    {"script": ["first reply", "second reply", ...]}

The file is read each time a new provider is created (i.e. each new
creature spawn / model switch / compact run), so tests can rewrite
the file between operations to script the next provider's responses
without restarting the worker.
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
    """Install the scripted-LLM seam if ``KT_TEST_LLM_SCRIPT`` is set.

    Returns True if the seam was activated; False otherwise.  Safe to
    call more than once — repeated calls are no-ops after the first
    install.
    """
    global _INSTALLED
    if _INSTALLED:
        return True
    script_path_str = os.environ.get("KT_TEST_LLM_SCRIPT")
    if not script_path_str:
        return False
    script_path = Path(script_path_str)

    def _fake_create(config, llm_override=None):
        return ScriptedLLM(_load_script(script_path))

    def _fake_from_profile(name):
        return ScriptedLLM(_load_script(script_path))

    _bootstrap_llm.create_llm_provider = _fake_create
    _bootstrap_llm.create_llm_from_profile_name = _fake_from_profile

    # Also patch sites that rebind the factory by name at import time.
    # Lazy import — both modules may not be imported yet, and on import
    # they will pick up the already-wrapped symbol via ``from ... import``
    # only if the import happens after this call.  So patch them too,
    # if present.
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
