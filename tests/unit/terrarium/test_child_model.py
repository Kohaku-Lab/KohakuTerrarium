"""Tests for spawned child model selection."""

import subprocess
import sys

from kohakuterrarium.core.config.types import AgentConfig
from kohakuterrarium.terrarium.child_model import choose_child_model, selector_resolves


def test_invalid_table_selector_falls_back_to_caller():
    choice = choose_child_model(
        AgentConfig(name="child"),
        explicit=None,
        caller="caller",
        child_models={"default": "openai/"},
        resolves=selector_resolves,
    )
    assert choice.selector == "caller"
    assert choice.fallback_from == "openai/"
    assert choice.fallback_reason == "selector_unavailable"


def test_lifecycle_module_can_be_imported_first():
    result = subprocess.run(
        [sys.executable, "-c", "import kohakuterrarium.terrarium.tools.lifecycle"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
