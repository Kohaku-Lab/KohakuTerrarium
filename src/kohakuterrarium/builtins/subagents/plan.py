"""
Plan sub-agent - Implementation planning.

Creates detailed implementation plans without executing changes.
"""

from kohakuterrarium.modules.subagent.config import SubAgentConfig

PLAN_SYSTEM_PROMPT = """\
You are in read-only planning mode. You may NOT make any changes.
- Analyze the codebase and construct a well-formed plan
- Ask clarifying questions rather than making assumptions
- Your plan should be concrete and actionable
- Break complex tasks into logical steps with dependencies
- Identify risks and edge cases
- This constraint is absolute -- no edits, no writes, no commits
"""

PLAN_CONFIG = SubAgentConfig(
    name="plan",
    description="Create implementation plans (read-only)",
    tools=["glob", "grep", "read", "tree", "bash"],
    system_prompt=PLAN_SYSTEM_PROMPT,
    can_modify=False,
    stateless=True,
)
