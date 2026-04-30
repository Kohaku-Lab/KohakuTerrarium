"""Built-in coordinator sub-agent."""

from kohakuterrarium.builtins.subagents._prompt_loader import render_subagent_prompt
from kohakuterrarium.modules.subagent.config import SubAgentConfig

COORDINATOR_SYSTEM_PROMPT = render_subagent_prompt(
    agent_name="coordinator",
    specialty_intro="You are a coordination specialist. Decompose complex work, delegate clearly, track progress, and synthesize results.",
    extra_principles="Do not do specialist work yourself when delegation is available; make handoffs explicit.",
    response_shape="Return: `Task Breakdown`, `Dispatches`, `Results`, and `Final Summary` sections.",
    can_modify=False,
)

COORDINATOR_CONFIG = SubAgentConfig(
    name="coordinator",
    description="Coordinate multiple agents via channels",
    tools=["send_message", "scratchpad"],
    system_prompt=COORDINATOR_SYSTEM_PROMPT,
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
