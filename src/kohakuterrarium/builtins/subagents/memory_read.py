"""Built-in memory_read sub-agent."""

from kohakuterrarium.builtins.subagents._prompt_loader import render_subagent_prompt
from kohakuterrarium.modules.subagent.config import SubAgentConfig

MEMORY_READ_SYSTEM_PROMPT = render_subagent_prompt(
    agent_name="memory_read",
    specialty_intro="You are a memory retrieval specialist. Find relevant persisted knowledge without guessing file names.",
    extra_principles="Discover available memory files first, then search and read only what is relevant.",
    response_shape="Return relevant memories grouped by relevance with file paths.",
    can_modify=False,
)

MEMORY_READ_CONFIG = SubAgentConfig(
    name="memory_read",
    description="Search and retrieve from memory",
    tools=["tree", "read", "grep"],
    system_prompt=MEMORY_READ_SYSTEM_PROMPT,
    can_modify=False,
    stateless=True,
    memory_path="./memory",
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
