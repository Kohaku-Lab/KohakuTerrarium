"""``/skill`` user command — list + enable/disable + show procedural skills.

The user-facing surface for the skill registry (Qa runtime toggle).
Accepts:

- ``/skill``              — list all discovered skills with status
- ``/skill list``         — alias for bare ``/skill``
- ``/skill enable <name>``
- ``/skill disable <name>``
- ``/skill toggle <name>``
- ``/skill show <name>``  — display frontmatter + body preamble

Triple-invocation (Qd) ALSO lets the user type ``/<skill-name>`` to
run a skill directly; that path is handled by the input module's
wildcard dispatcher (:func:`_dispatch_skill_slash`) rather than this
command so the existing ``/model``, ``/plugin``, … commands still
shadow same-named skills by design.
"""

from kohakuterrarium.builtins.user_commands.registry import register_user_command
from kohakuterrarium.modules.user_command.base import (
    BaseUserCommand,
    CommandLayer,
    UserCommandContext,
    UserCommandResult,
    ui_notify,
    ui_select,
)
from kohakuterrarium.skills import SkillRegistry


def _get_registry(context: UserCommandContext) -> SkillRegistry | None:
    agent = context.agent
    if agent is None:
        return None
    return getattr(agent, "skills", None)


@register_user_command("skill")
class SkillUserCommand(BaseUserCommand):
    name = "skill"
    aliases = ["skills"]
    description = "List / enable / disable / show procedural skills"
    layer = CommandLayer.AGENT

    async def _execute(
        self, args: str, context: UserCommandContext
    ) -> UserCommandResult:
        registry = _get_registry(context)
        if registry is None:
            return UserCommandResult(error="No skill registry on this agent.")

        parts = args.strip().split(None, 1)
        sub = parts[0].lower() if parts else ""
        rest = parts[1].strip() if len(parts) > 1 else ""

        if sub in ("", "list"):
            return self._list(registry)
        if sub == "enable":
            return self._toggle(registry, rest, enable=True)
        if sub == "disable":
            return self._toggle(registry, rest, enable=False)
        if sub == "toggle":
            return self._toggle(registry, rest, enable=None)
        if sub == "show":
            return self._show(registry, rest)

        return UserCommandResult(
            error=(
                f"Unknown /skill subcommand: {sub!r}. "
                "Use list / enable / disable / toggle / show."
            )
        )

    # ------------------------------------------------------------------
    # Subcommand implementations
    # ------------------------------------------------------------------

    def _list(self, registry: SkillRegistry) -> UserCommandResult:
        skills = registry.all()
        if not skills:
            return UserCommandResult(
                output=(
                    "No procedural skills discovered.\n"
                    "Drop a SKILL.md under .kt/skills/<name>/ or "
                    "~/.kohakuterrarium/skills/<name>/ to get started."
                )
            )
        lines = []
        options = []
        for s in skills:
            status = "enabled" if s.enabled else "disabled"
            hidden = " (hidden)" if s.invocation_blocked else ""
            desc_lines = (s.description or "").splitlines()
            desc = desc_lines[0][:120] if desc_lines else ""
            lines.append(
                f"{status:>8}{hidden:>9}  " f"{s.name:<24} [{s.origin}]  {desc}"
            )
            options.append(
                {
                    "value": f"toggle {s.name}",
                    "label": s.name,
                    "description": desc,
                    "status": status,
                    "origin": s.origin,
                    "selected": s.enabled,
                }
            )
        lines.append("")
        lines.append(
            "Toggle with /skill enable|disable|toggle <name>. "
            "Show body with /skill show <name>."
        )
        return UserCommandResult(
            output="\n".join(lines),
            data=ui_select("Skills", options, action="skill"),
        )

    def _toggle(
        self, registry: SkillRegistry, name: str, *, enable: bool | None
    ) -> UserCommandResult:
        if not name:
            return UserCommandResult(error="Usage: /skill enable|disable <name>")
        skill = registry.get(name)
        if skill is None:
            return UserCommandResult(error=f"Unknown skill: {name}")
        if enable is None:
            enable = not skill.enabled
        ok = registry.enable(name) if enable else registry.disable(name)
        if not ok:
            return UserCommandResult(error=f"Unknown skill: {name}")
        verb = "enabled" if enable else "disabled"
        return UserCommandResult(
            output=f"Skill '{name}' {verb}.",
            data=ui_notify(f"Skill '{name}' {verb}.", level="success"),
        )

    def _show(self, registry: SkillRegistry, name: str) -> UserCommandResult:
        if not name:
            return UserCommandResult(error="Usage: /skill show <name>")
        skill = registry.get(name)
        if skill is None:
            return UserCommandResult(error=f"Unknown skill: {name}")
        header = (
            f"# {skill.name}\n"
            f"Origin: {skill.origin}\n"
            f"Enabled: {skill.enabled}\n"
            f"Description: {skill.description}\n"
        )
        if skill.paths:
            header += f"Paths: {', '.join(skill.paths)}\n"
        if skill.allowed_tools:
            header += f"Allowed tools: {', '.join(skill.allowed_tools)}\n"
        body = skill.body or "(empty body)"
        return UserCommandResult(output=header + "\n" + body)
