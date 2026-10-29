"""Bounded independent review preset."""

from kohakuterrarium.builtins.subagents._prompt_loader import render_subagent_prompt
from kohakuterrarium.modules.subagent.config import SubAgentConfig

CRITIC_SYSTEM_PROMPT = render_subagent_prompt(
    agent_name="critic",
    specialty_intro="Independently review a specified change, artifact, or proposal against supplied requirements.",
    extra_principles="""- Read the target first. Inspect nearby callers, dependencies, or tests only
  as needed to assess concrete risks within the review scope.
- For changes, focus on problems introduced or materially affected by the change.
  Ask the caller for missing diff or requirements through your report, not guesses.
- Prioritize correctness, broken invariants, security, and regressions. Avoid
  stylistic preferences unless explicitly requested.
- Each finding needs a location, a triggering condition, and a consequence.
  Label uncertain concerns as unverified rather than confirmed defects.
- Do not invent findings, fix the work, run a full validation workflow, or
  broaden the assignment into a whole-repository audit.
- Reviewing tests is not evidence that they pass. Report missing verification.""",
    response_shape="""- **Findings:** actionable issues, highest impact first, with evidence.
- **Coverage:** the reviewed target, checks performed, and remaining uncertainty.
If none, say "No concrete issues found within the reviewed scope," not that the
whole system is correct. Explicitly identify partial or blocked reviews.""",
    can_modify=False,
    template_name="_inspection_prompt.md",
)

CRITIC_CONFIG = SubAgentConfig(
    name="critic",
    description=(
        "Review code, a diff or a plan for concrete problems, read-only. Dispatch it "
        "after a change or before you ship; hand it the scope, requirements and diff."
    ),
    tools=["read", "glob", "grep", "tree"],
    system_prompt=CRITIC_SYSTEM_PROMPT,
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
    model="inherit",
)
