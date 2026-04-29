"""Built-in sub-agent prompt and budget default coverage."""

from kohakuterrarium.builtins.subagent_catalog import (
    BUILTIN_SUBAGENTS,
    get_builtin_subagent_config,
)
from kohakuterrarium.builtins.subagents.response import INTERACTIVE_RESPONSE_CONFIG

_EXPECTED = {
    "explore": ((40, 60), None, (75, 100), "subagent-default"),
    "research": ((40, 60), None, (75, 100), "subagent-default"),
    "plan": ((40, 60), None, (75, 100), None),
    "coordinator": ((40, 60), None, (75, 100), None),
    "critic": ((40, 60), None, (75, 100), None),
    "memory_read": ((40, 60), None, (75, 100), "subagent-default"),
    "memory_write": ((40, 60), None, (75, 100), "subagent-default"),
    "response": ((40, 60), None, (75, 100), "subagent-default"),
    "summarize": ((40, 60), None, (75, 100), "subagent-default"),
    "worker": ((40, 60), None, (75, 100), None),
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


def test_builtin_budget_defaults_use_minimal_runtime_pack():
    assert set(BUILTIN_SUBAGENTS) == set(_EXPECTED)
    for name, expected in _EXPECTED.items():
        config = get_builtin_subagent_config(name)
        assert config is not None
        turn, walltime, tool_call, model = expected
        assert config.default_plugins == ["default-runtime"]
        assert config.turn_budget == turn
        assert config.walltime_budget == walltime
        assert config.tool_call_budget == tool_call
        assert config.model == model


def test_catalog_returns_defensive_copies():
    first = get_builtin_subagent_config("explore")
    second = get_builtin_subagent_config("explore")
    assert first is not None and second is not None

    first.default_plugins.append("changed")
    assert second.default_plugins == ["default-runtime"]


def test_interactive_response_config_has_runtime_defaults():
    assert INTERACTIVE_RESPONSE_CONFIG.default_plugins == ["default-runtime"]
    assert INTERACTIVE_RESPONSE_CONFIG.turn_budget == (40, 60)
    assert INTERACTIVE_RESPONSE_CONFIG.walltime_budget is None
    assert INTERACTIVE_RESPONSE_CONFIG.tool_call_budget == (75, 100)
    assert INTERACTIVE_RESPONSE_CONFIG.model == "subagent-default"
