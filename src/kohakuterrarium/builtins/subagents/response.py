"""Built-in response sub-agent."""

from kohakuterrarium.builtins.subagents._prompt_loader import render_subagent_prompt
from kohakuterrarium.modules.subagent.config import OutputTarget, SubAgentConfig

RESPONSE_SYSTEM_PROMPT = render_subagent_prompt(
    agent_name="response",
    specialty_intro="You are a response-generation specialist. Convert controller context into concise user-facing prose.",
    extra_principles="Match the requested tone and stay silent only when explicitly appropriate.",
    response_shape="Output only the final user-facing response, or `[SILENCE]` when silence is correct.",
    can_modify=False,
)

_BUDGET_PLUGIN_OPTS = {
    "turn_budget": [40, 60],
    "tool_call_budget": [75, 100],
}

RESPONSE_CONFIG = SubAgentConfig(
    name="response",
    description="Generate user-facing responses",
    tools=["read"],
    system_prompt=RESPONSE_SYSTEM_PROMPT,
    can_modify=False,
    stateless=True,
    interactive=False,
    output_to=OutputTarget.EXTERNAL,
    default_plugins=["auto-compact"],
    plugins=[{"name": "budget", "options": dict(_BUDGET_PLUGIN_OPTS)}],
    model="subagent-default",
)

INTERACTIVE_RESPONSE_CONFIG = SubAgentConfig(
    name="response_interactive",
    description="Interactive response agent (stays alive)",
    tools=["read"],
    system_prompt=RESPONSE_SYSTEM_PROMPT,
    can_modify=False,
    stateless=False,
    interactive=True,
    output_to=OutputTarget.EXTERNAL,
    default_plugins=["auto-compact"],
    plugins=[{"name": "budget", "options": dict(_BUDGET_PLUGIN_OPTS)}],
    model="subagent-default",
)
