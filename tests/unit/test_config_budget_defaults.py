"""Config parsing for runtime default plugin field.

Budget axes are no longer agent / sub-agent core fields — they live in
the ``budget`` plugin's ``options``. The remaining ``default_plugins``
field stays as a way to opt into plugin packs (e.g. ``auto-compact``).
"""

from pathlib import Path

from kohakuterrarium.core.config import build_agent_config
from kohakuterrarium.core.config_types import AgentConfig
from kohakuterrarium.modules.subagent.config import SubAgentConfig


def test_agent_config_parses_default_plugins_and_retry_policy():
    config = build_agent_config(
        {
            "name": "minimal",
            "controller": {
                "retry_policy": {"max_retries": 1, "base_delay": 0},
            },
            "default_plugins": ["auto-compact"],
        },
        Path("."),
    )

    assert config.default_plugins == ["auto-compact"]
    assert config.retry_policy == {"max_retries": 1, "base_delay": 0}


def test_agent_config_has_no_budget_fields():
    config = AgentConfig(name="x")
    for field in ("turn_budget", "walltime_budget", "tool_call_budget"):
        assert not hasattr(
            config, field
        ), f"{field} should not be on AgentConfig — declare via plugin options."


def test_agent_config_parses_inline_subagent_entries_with_plugin_options():
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
                    "plugins": [
                        {
                            "name": "budget",
                            "options": {
                                "turn_budget": [40, 60],
                                "tool_call_budget": [75, 100],
                            },
                        }
                    ],
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
    assert inline.options["plugins"] == [
        {
            "name": "budget",
            "options": {
                "turn_budget": [40, 60],
                "tool_call_budget": [75, 100],
            },
        }
    ]


def test_subagent_config_has_no_budget_fields():
    config = SubAgentConfig(name="x")
    for field in ("turn_budget", "walltime_budget", "tool_call_budget"):
        assert not hasattr(config, field)
    # ``plugins`` is the new opt-in surface.
    assert config.plugins == []
