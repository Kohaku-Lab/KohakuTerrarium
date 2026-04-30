"""Built-in research sub-agent."""

from kohakuterrarium.builtins.subagents._prompt_loader import render_subagent_prompt
from kohakuterrarium.modules.subagent.config import SubAgentConfig

RESEARCH_SYSTEM_PROMPT = render_subagent_prompt(
    agent_name="research",
    specialty_intro="You are a research specialist. Gather reliable local and web evidence and synthesize an accurate answer.",
    extra_principles="Distinguish verified facts from speculation and keep source citations attached to claims.",
    response_shape="Return: `Answer`, `Sources`, `Details`, and `Uncertainties` sections.",
    can_modify=False,
)

RESEARCH_CONFIG = SubAgentConfig(
    name="research",
    description="Research topics using files and web access",
    tools=["web_search", "web_fetch", "read", "write", "scratchpad"],
    system_prompt=RESEARCH_SYSTEM_PROMPT,
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
