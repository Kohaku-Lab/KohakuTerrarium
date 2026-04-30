"""Built-in critic sub-agent."""

from kohakuterrarium.builtins.subagents._prompt_loader import render_subagent_prompt
from kohakuterrarium.modules.subagent.config import SubAgentConfig

CRITIC_SYSTEM_PROMPT = render_subagent_prompt(
    agent_name="critic",
    specialty_intro="You are a rigorous reviewer. Find correctness, security, maintainability, and regression risks.",
    extra_principles="Prioritize high-impact findings with evidence over style nits.",
    response_shape="Return findings by severity with file references; if none, say so and list residual risks.",
    can_modify=False,
)

CRITIC_CONFIG = SubAgentConfig(
    name="critic",
    description="Review and critique code, plans, or outputs",
    tools=["read", "glob", "grep", "tree", "bash"],
    system_prompt=CRITIC_SYSTEM_PROMPT,
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
