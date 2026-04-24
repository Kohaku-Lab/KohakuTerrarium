"""Build the auto-invoke "Skills" section for the system prompt.

Spec D.2: each *enabled, non-disable-model-invocation* skill
contributes its ``name`` + ``description`` to a ``## Skills`` section.
Overflow past a soft byte budget (default 4 KB) is truncated — those
skills remain reachable via explicit ``info(name=...)`` /
``skill(name=...)`` tool calls.

Ordering: alphabetical by name for determinism.
"""

from kohakuterrarium.skills.registry import Skill, SkillRegistry

DEFAULT_SKILL_INDEX_BUDGET_BYTES = 4096


def build_skill_index(
    registry: SkillRegistry | None,
    *,
    budget_bytes: int = DEFAULT_SKILL_INDEX_BUDGET_BYTES,
) -> str:
    """Return the ``## Skills`` markdown section, or ``""`` if none apply.

    Skills past the byte budget are silently omitted (spec 4.3).
    Skills with ``disable-model-invocation: true`` never appear in the
    index (spec 4.4) but remain callable via the explicit skill tool.
    """
    if registry is None or len(registry) == 0:
        return ""
    eligible = [s for s in registry.list_enabled() if not s.invocation_blocked]
    if not eligible:
        return ""
    eligible.sort(key=lambda s: s.name)

    header = "## Skills\n\n"
    preamble = (
        "Procedural skills loaded for this session. Invoke explicitly with "
        "the `skill` tool (`name`, optional `arguments`) or read full docs "
        "via the `info` tool.\n\n"
    )
    footer = "\nRun `info` for the full body before executing a skill.\n"

    lines: list[str] = [header, preamble]
    used = len(header) + len(preamble) + len(footer)
    omitted = 0
    for skill in eligible:
        line = _format_entry(skill)
        # Always include at least one skill, even if it breaks the budget.
        if used + len(line) > budget_bytes and (len(lines) > 2):
            omitted += 1
            continue
        lines.append(line)
        used += len(line)
    if omitted:
        overflow = (
            f"\n*({omitted} more skill(s) omitted to stay within the "
            f"{budget_bytes}-byte skill-index budget; call them with "
            "the `skill` tool directly.)*\n"
        )
        lines.append(overflow)
    lines.append(footer)
    return "".join(lines).rstrip() + "\n"


def _format_entry(skill: Skill) -> str:
    desc = (skill.description or "").splitlines()[0].strip()
    suffix = f" — {desc}" if desc else ""
    return f"- `{skill.name}`{suffix}\n"
