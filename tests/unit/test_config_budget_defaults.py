"""Config parsing for runtime default plugin and budget fields."""

from pathlib import Path

from kohakuterrarium.core.config import build_agent_config
from kohakuterrarium.modules.subagent.config import SubAgentConfig


def test_agent_config_parses_budget_and_retry_policy_fields():
    config = build_agent_config(
        {
            "name": "budgeted",
            "controller": {
                "retry_policy": {"max_retries": 1, "base_delay": 0},
            },
            "default_plugins": ["default-runtime"],
            "turn_budget": [2, 4],
            "walltime_budget": {"soft": 10, "hard": 20},
            "tool_call_budget": {"soft": 3, "limit": 5},
        },
        Path("."),
    )

    assert config.default_plugins == ["default-runtime"]
    assert config.turn_budget == (2, 4)
    assert config.walltime_budget == (10.0, 20.0)
    assert config.tool_call_budget == (3, 5)
    assert config.retry_policy == {"max_retries": 1, "base_delay": 0}


def test_agent_config_parses_string_and_inline_subagent_entries():
    config = build_agent_config(
        {
            "name": "inline-subagents",
            "subagents": [
                "explore",
                {
                    "name": "inline_specialist",
                    "type": "custom",
                    "system_prompt": "Answer briefly.",
                    "tools": ["read"],
                    "default_plugins": ["budget"],
                    "turn_budget": [40, 60],
                    "tool_call_budget": [75, 100],
                },
            ],
        },
        Path("."),
    )

    assert config.subagents[0].name == "explore"
    assert config.subagents[0].type == "builtin"
    inline = config.subagents[1]
    assert inline.name == "inline_specialist"
    assert inline.type == "custom"
    assert inline.tools == ["read"]
    assert inline.options["system_prompt"] == "Answer briefly."
    assert inline.options["default_plugins"] == ["budget"]
    assert inline.options["turn_budget"] == [40, 60]
    assert inline.options["tool_call_budget"] == [75, 100]


def test_subagent_config_maps_legacy_limits_to_budget_fields():
    config = SubAgentConfig.from_dict(
        {
            "name": "legacy",
            "max_turns": 7,
            "timeout": 12,
            "tool_call_budget": {"soft": 2, "hard": 4},
        }
    )

    assert config.turn_budget == (0, 7)
    assert config.walltime_budget == (0.0, 12.0)
    assert config.tool_call_budget == (2, 4)
