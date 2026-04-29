"""Shared prompt renderer for built-in sub-agents."""

from pathlib import Path

from kohakuterrarium.prompt.template import render_template_safe

_TEMPLATE = Path(__file__).with_name("_base_prompt.md")


def render_subagent_prompt(
    *,
    agent_name: str,
    specialty_intro: str,
    extra_principles: str,
    response_shape: str,
    can_modify: bool,
) -> str:
    """Render the shared built-in sub-agent prompt template."""
    template = _TEMPLATE.read_text(encoding="utf-8")
    return render_template_safe(
        template,
        agent_name=agent_name,
        specialty_intro=specialty_intro,
        extra_principles=extra_principles,
        response_shape=response_shape,
        can_modify=can_modify,
    )
