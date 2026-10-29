"""Built-in summarize sub-agent."""

from kohakuterrarium.builtins.subagents._prompt_loader import render_subagent_prompt
from kohakuterrarium.modules.subagent.config import SubAgentConfig

SUMMARIZE_SYSTEM_PROMPT = render_subagent_prompt(
    agent_name="summarize",
    specialty_intro="You are a summarization specialist. Preserve task state for future continuation.",
    extra_principles="Capture decisions, progress, files, blockers, and next steps without answering new questions.",
    response_shape="Return a concise structured continuation summary.",
    can_modify=False,
)

SUMMARIZE_CONFIG = SubAgentConfig(
    name="summarize",
    description=(
        "Condense a long conversation or document into a structured summary. Dispatch "
        "it to compress context or brief others. Not for review - dispatch critic."
    ),
    tools=[],
    system_prompt=SUMMARIZE_SYSTEM_PROMPT,
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
