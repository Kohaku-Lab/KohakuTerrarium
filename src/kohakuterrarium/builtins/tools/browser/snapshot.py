"""``browser_snapshot``: the current page as an accessibility snapshot with refs."""

from typing import Any

from kohakuterrarium.builtins.tools.browser.common import (
    TAB_ARG,
    BrowserTool,
    bool_arg,
    int_arg,
    tab_arg,
    text_arg,
)
from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.modules.tool.base import ToolContext


@register_builtin("browser_snapshot")
class BrowserSnapshotTool(BrowserTool):
    """Read the page (or a subtree, a text match, or its controls) as snapshot lines."""

    arguments = ("ref", "depth", "query", "interactive", TAB_ARG)

    @property
    def tool_name(self) -> str:
        return "browser_snapshot"

    @property
    def description(self) -> str:
        return (
            "Read the current browser page as an accessibility snapshot with "
            "element refs. Use it before acting, or narrow it with ref, query or "
            "interactive."
        )

    def parse(self, args: dict[str, Any], context: ToolContext | None) -> dict:
        return {
            "ref": text_arg(args, "ref"),
            "depth": int_arg(args, "depth"),
            "query": text_arg(args, "query"),
            "interactive": bool_arg(args, "interactive"),
            "tab_id": tab_arg(args),
        }

    async def run(self, session: Any, call: dict, context: ToolContext | None) -> Any:
        return await session.snapshot(**call)
