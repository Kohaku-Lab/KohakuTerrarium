"""Build a deterministic, byte-bounded index of model-invocable skills."""

from kohakuterrarium.skills.registry import Skill, SkillRegistry

DEFAULT_SKILL_INDEX_BUDGET_BYTES = 4096


def build_skill_index(
    registry: SkillRegistry | None,
    *,
    budget_bytes: int = DEFAULT_SKILL_INDEX_BUDGET_BYTES,
) -> str:
    """List enabled model-invocable skills within the configured byte budget."""
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
        # A non-empty registry must expose at least one discoverable skill.
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
    lines = (skill.description or "").splitlines()
    desc = lines[0].strip() if lines else ""
    suffix = f" — {desc}" if desc else ""
    return f"- `{skill.name}`{suffix}\n"
