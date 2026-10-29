"""Bounded workspace exploration preset."""

from kohakuterrarium.builtins.subagents._prompt_loader import render_subagent_prompt
from kohakuterrarium.modules.subagent.config import SubAgentConfig

EXPLORE_SYSTEM_PROMPT = render_subagent_prompt(
    agent_name="explore",
    specialty_intro="Resolve a specific question about the existing workspace using evidence, without changing it.",
    extra_principles="""- Locate relevant entry points, then inspect the smallest set of files needed
  to establish the answer. Narrow searches before reading files in detail.
- For behavior questions, trace the relevant call or data path rather than
  relying only on names, declarations, documentation, or comments.
- Reuse supplied findings as leads. Check decisive evidence without repeating
  broad investigation already performed by the caller.
- Do not turn exploration into implementation, redesign, or a full project plan.
- A failed search is not proof of absence. State the search scope when reporting
  that something was not found.""",
    response_shape="""- **Answer:** the direct conclusion to the caller's question.
- **Evidence:** relevant paths, symbols, line references, and relationships.
- **Limits:** material uncertainties, unchecked paths, or partial coverage.""",
    can_modify=False,
    template_name="_inspection_prompt.md",
)

EXPLORE_CONFIG = SubAgentConfig(
    name="explore",
    description=(
        "Investigate a workspace question and return file evidence, read-only. "
        "Dispatch it whenever an answer means reading across many files; hand it the "
        "scope."
    ),
    tools=["glob", "grep", "read", "tree"],
    system_prompt=EXPLORE_SYSTEM_PROMPT,
    can_modify=False,
    stateless=True,
    max_turns=14,
    timeout=600,
    plugins=[
        {
            "name": "budget",
            "options": {
                "turn_budget": [8, 12],
                "tool_call_budget": [24, 40],
            },
        },
    ],
    model="subagent-default",
)
