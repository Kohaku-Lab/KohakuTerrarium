"""Worker sub-agent - general-purpose implementation worker."""

from kohakuterrarium.builtins.subagents._prompt_loader import render_subagent_prompt
from kohakuterrarium.modules.subagent.config import SubAgentConfig

WORKER_SYSTEM_PROMPT = render_subagent_prompt(
    agent_name="worker",
    specialty_intro="You are an implementation worker. Apply code changes, fix bugs, refactor safely, and validate the result.",
    extra_principles="Prefer minimal, well-tested changes that respect the repository architecture.",
    response_shape="Return: `Changed`, `Verification`, and `Risks/Follow-up` sections. Include files touched.",
    can_modify=True,
)

WORKER_CONFIG = SubAgentConfig(
    name="worker",
    description="Implement code changes, fix bugs, refactor (read-write)",
    tools=["read", "write", "edit", "bash", "glob", "grep"],
    system_prompt=WORKER_SYSTEM_PROMPT,
    can_modify=True,
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
