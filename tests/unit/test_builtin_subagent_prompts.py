"""Built-in sub-agent prompt and budget default coverage."""

from kohakuterrarium.builtins.subagent_catalog import (
    BUILTIN_SUBAGENTS,
    get_builtin_subagent_config,
)
from kohakuterrarium.builtins.subagents.response import INTERACTIVE_RESPONSE_CONFIG

_EXPECTED_BUDGET = {
    "turn_budget": [40, 60],
    "tool_call_budget": [75, 100],
}

_EXPECTED_MODEL = {
    "explore": "subagent-default",
    "research": "subagent-default",
    "plan": None,
    "coordinator": None,
    "critic": None,
    "memory_read": "subagent-default",
    "memory_write": "subagent-default",
    "response": "subagent-default",
    "summarize": "subagent-default",
    "worker": None,
}


def test_all_builtin_prompts_render_core_sections():
    for name in BUILTIN_SUBAGENTS:
        config = get_builtin_subagent_config(name)
        assert config is not None
        assert "# Operating Constraints" in config.system_prompt
        assert "# Operating Principles" in config.system_prompt
        assert "# Communication" in config.system_prompt
        assert "# Response Shape" in config.system_prompt
        assert "{{" not in config.system_prompt


def test_read_only_builtin_prompts_include_read_only_marker():
    for name in BUILTIN_SUBAGENTS:
        config = get_builtin_subagent_config(name)
        assert config is not None
        if not config.can_modify:
            assert "You are read-only" in config.system_prompt


def test_builtin_budget_defaults_are_opt_in_plugin_entries():
    """Every built-in sub-agent ships budget as a ``plugins:`` entry,
    not as a core config field — and pulls compaction via the
    ``auto-compact`` pack rather than a budget-bundling mega-pack.
    """
    assert set(BUILTIN_SUBAGENTS) == set(_EXPECTED_MODEL)
    for name, expected_model in _EXPECTED_MODEL.items():
        config = get_builtin_subagent_config(name)
        assert config is not None
        assert config.default_plugins == ["auto-compact"]
        budget_entries = [p for p in config.plugins if p.get("name") == "budget"]
        assert len(budget_entries) == 1
        assert budget_entries[0].get("options") == _EXPECTED_BUDGET
        assert config.model == expected_model


def test_catalog_returns_defensive_copies():
    first = get_builtin_subagent_config("explore")
    second = get_builtin_subagent_config("explore")
    assert first is not None and second is not None

    first.default_plugins.append("changed")
    assert second.default_plugins == ["auto-compact"]


def test_interactive_response_config_has_runtime_defaults():
    assert INTERACTIVE_RESPONSE_CONFIG.default_plugins == ["auto-compact"]
    budget_entries = [
        p for p in INTERACTIVE_RESPONSE_CONFIG.plugins if p.get("name") == "budget"
    ]
    assert len(budget_entries) == 1
    assert budget_entries[0].get("options") == _EXPECTED_BUDGET
    assert INTERACTIVE_RESPONSE_CONFIG.model == "subagent-default"
