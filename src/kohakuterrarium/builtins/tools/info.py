"""Info tool - load full documentation for a tool or sub-agent on demand."""

from pathlib import Path
from typing import Any

from kohakuterrarium.builtin_skills import (
    get_builtin_subagent_doc,
    get_builtin_tool_doc,
    read_skill_body,
)
from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.commands.read import _render_skill_info
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolContext,
    ToolResult,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


@register_builtin("info")
class InfoTool(BaseTool):
    """Load full documentation for a tool or sub-agent."""

    needs_context = True

    @property
    def tool_name(self) -> str:
        return "info"

    @property
    def description(self) -> str:
        return (
            "Get full documentation for a tool, sub-agent, or procedural skill by name"
        )

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(
        self, args: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        """Load documentation for the named tool or sub-agent."""
        name = args.get("name", args.get("content", "")).strip()

        if not name:
            return ToolResult(
                error="Provide the name of a tool, sub-agent, or procedural skill."
            )

        # 1. Try builtin tool docs
        doc = get_builtin_tool_doc(name)
        if doc:
            logger.debug("Loaded tool doc", tool_name=name)
            self._mark_manual_read(name, context)
            return ToolResult(output=doc, exit_code=0)

        # 2. Try builtin sub-agent docs
        doc = get_builtin_subagent_doc(name)
        if doc:
            logger.debug("Loaded subagent doc", subagent_name=name)
            return ToolResult(output=doc, exit_code=0)

        # 3. Try agent-local docs
        if context and hasattr(context, "working_dir"):
            for subdir in ["prompts/tools", "prompts/subagents"]:
                doc_path = Path(context.working_dir) / subdir / f"{name}.md"
                if doc_path.exists():
                    body = read_skill_body(doc_path)
                    if body is not None:
                        return ToolResult(output=body, exit_code=0)

        # 4. Try tool's own get_full_documentation from the agent's registry
        if context and context.agent:
            registry = context.agent.registry
            tool = registry.get_tool(name)
            if tool and hasattr(tool, "get_full_documentation"):
                fmt = context.tool_format or "native"
                doc = tool.get_full_documentation(tool_format=fmt)
                if doc:
                    logger.debug("Loaded doc from tool instance", tool_name=name)
                    self._mark_manual_read(name, context)
                    return ToolResult(output=doc, exit_code=0)

            # 5. Try sub-agent config
            subagent = registry.get_subagent(name)
            if subagent:
                desc = getattr(subagent, "description", "") or f"Sub-agent: {name}"
                return ToolResult(output=desc, exit_code=0)

        # 6. Try runtime procedural skills.
        skill_doc = _render_skill_info(context, name)
        if skill_doc is not None:
            logger.debug("Loaded procedural skill doc", skill_name=name)
            return ToolResult(output=skill_doc, exit_code=0)

        return ToolResult(
            error=f"No documentation found for '{name}'. "
            "Check the tool/sub-agent/skill name and try again."
        )

    @staticmethod
    def _mark_manual_read(name: str, context: ToolContext | None) -> None:
        """Mark a tool as having its manual read (unlocks require_manual_read)."""
        if not context or not context.agent:
            return
        tool = context.agent.registry.get_tool(name)
        if tool and isinstance(tool, BaseTool) and tool.require_manual_read:
            tool._manual_read = True
            logger.debug("Manual read unlocked", tool_name=name)
