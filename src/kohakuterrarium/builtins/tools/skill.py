"""Skill tool - invoke procedural skills through the normal tool surface."""

from typing import Any

from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolContext,
    ToolResult,
)
from kohakuterrarium.skills.command import SkillCommand
from kohakuterrarium.skills.registry import SkillRegistry


@register_builtin("skill")
class SkillTool(BaseTool):
    """Invoke a procedural skill by name."""

    needs_context = True

    @property
    def tool_name(self) -> str:
        return "skill"

    @property
    def description(self) -> str:
        return "Invoke a procedural skill by name and return its instructions"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(
        self, args: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        registry = _lookup_skill_registry(context)
        if registry is None:
            return ToolResult(error="No procedural skill registry is available.")

        name = str(args.get("name") or args.get("content") or "").strip()
        arguments = str(args.get("arguments") or args.get("args") or "").strip()
        if not name:
            return ToolResult(error="Provide a skill name.")

        command_args = name if not arguments else f"{name} {arguments}"
        result = await SkillCommand(registry).execute(command_args, context)
        if result.error:
            return ToolResult(error=result.error)
        return ToolResult(output=result.content, exit_code=0)


def _lookup_skill_registry(context: ToolContext | None) -> SkillRegistry | None:
    if context is None:
        return None

    agent = getattr(context, "agent", None)
    if agent is not None:
        registry = getattr(agent, "skills", None)
        if registry is not None:
            return registry

    session = getattr(context, "session", None)
    if session is not None:
        extra = getattr(session, "extra", None) or {}
        if isinstance(extra, dict):
            registry = extra.get("skills_registry")
            if registry is not None:
                return registry

    return None
