"""Explore sub-agent - read-only codebase search."""

from kohakuterrarium.builtins.subagents._prompt_loader import render_subagent_prompt
from kohakuterrarium.modules.subagent.config import SubAgentConfig

EXPLORE_SYSTEM_PROMPT = render_subagent_prompt(
    agent_name="explore",
    specialty_intro="You are a read-only codebase exploration specialist. Find precise files, symbols, behaviours, and relationships without modifying anything.",
    extra_principles="Use glob/grep/tree to narrow the search before reading files in detail.",
    response_shape="Return: answer first, then `Evidence` bullets with file references, then `Open questions` if any.",
    can_modify=False,
)

EXPLORE_CONFIG = SubAgentConfig(
    name="explore",
    description="Search and explore codebase (read-only)",
    tools=["glob", "grep", "read", "tree", "bash"],
    system_prompt=EXPLORE_SYSTEM_PROMPT,
    can_modify=False,
    stateless=True,
    default_plugins=["auto-compact"],
    plugins=[
        {
            "name": "budget",
            "options": {
                "turn_budget": [40, 60],
                "tool_call_budget": [75, 100],
            },
        },
    ],
    model="subagent-default",
)
