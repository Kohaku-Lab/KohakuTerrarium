"""``browser_snapshot``: the current page as an accessibility snapshot with refs."""

from typing import Any

from kohakuterrarium.builtins.tools.browser.common import (
    TAB_ARG,
    BrowserTool,
    bool_arg,
    int_arg,
    tab_arg,
    tab_schema,
    text_arg,
)
from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.modules.tool.base import ToolContext


@register_builtin("browser_snapshot")
class BrowserSnapshotTool(BrowserTool):
    """Read the page (or a subtree, a text match, or its controls) as snapshot lines."""

    arguments = ("ref", "depth", "query", "interactive", "boxes", TAB_ARG)
    neutral = {
        "ref": "",
        "depth": 0,
        "query": "",
        "interactive": False,
        "boxes": False,
        TAB_ARG: "",
    }

    @property
    def tool_name(self) -> str:
        return "browser_snapshot"

    @property
    def description(self) -> str:
        return (
            "Read the current browser page as an accessibility snapshot with "
            "element refs. Use it to reread a page, or narrow it with ref, query "
            "or interactive."
        )

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "ref": {
                    "type": "string",
                    "description": 'Only this element\'s subtree; "" = whole page',
                },
                "depth": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Levels below the root; 0 = no limit",
                },
                "query": {
                    "type": "string",
                    "description": 'Only lines matching this text; "" = all',
                },
                "interactive": {
                    "type": "boolean",
                    "description": "Only interactive elements",
                },
                "boxes": {
                    "type": "boolean",
                    "description": "Add [box=x,y,w,h] to each element",
                },
                TAB_ARG: tab_schema(),
            },
        }

    def parse(self, args: dict[str, Any], context: ToolContext | None) -> dict:
        return {
            "ref": text_arg(args, "ref"),
            "depth": int_arg(args, "depth"),
            "query": text_arg(args, "query"),
            "interactive": bool_arg(args, "interactive"),
            "boxes": bool_arg(args, "boxes"),
            "tab_id": tab_arg(args),
        }

    async def run(self, session: Any, call: dict, context: ToolContext | None) -> Any:
        return await session.snapshot(**call)
