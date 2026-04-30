"""Built-in plan sub-agent."""

from kohakuterrarium.builtins.subagents._prompt_loader import render_subagent_prompt
from kohakuterrarium.modules.subagent.config import SubAgentConfig

PLAN_SYSTEM_PROMPT = render_subagent_prompt(
    agent_name="plan",
    specialty_intro="You are a read-only planning specialist. Turn investigation into a concrete, dependency-aware implementation plan.",
    extra_principles="Identify risks, ordering constraints, and validation gates before proposing steps.",
    response_shape="Return: `Goal`, `Plan`, `Risks`, and `Verification` sections.",
    can_modify=False,
)

PLAN_CONFIG = SubAgentConfig(
    name="plan",
    description="Create implementation plans (read-only)",
    tools=["glob", "grep", "read", "tree", "bash"],
    system_prompt=PLAN_SYSTEM_PROMPT,
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
)
