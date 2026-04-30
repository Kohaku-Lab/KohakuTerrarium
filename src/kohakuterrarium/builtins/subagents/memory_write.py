"""Built-in memory_write sub-agent."""

from kohakuterrarium.builtins.subagents._prompt_loader import render_subagent_prompt
from kohakuterrarium.modules.subagent.config import SubAgentConfig

MEMORY_WRITE_SYSTEM_PROMPT = render_subagent_prompt(
    agent_name="memory_write",
    specialty_intro="You are a memory storage specialist. Write durable, organized memory updates.",
    extra_principles="Read existing memory before updating and preserve protected/important content.",
    response_shape="Return what memory files were created or updated and the key facts stored.",
    can_modify=True,
)

MEMORY_WRITE_CONFIG = SubAgentConfig(
    name="memory_write",
    description="Store information to memory (can create files)",
    tools=["tree", "read", "write"],
    system_prompt=MEMORY_WRITE_SYSTEM_PROMPT,
    can_modify=True,
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
