"""``browser_tabs``: list, open, select or close tabs, or close the browser."""

from typing import Any

from kohakuterrarium.builtins.tools.browser.common import (
    BODY_ARG,
    TAB_ARG,
    ArgumentError,
    BrowserTool,
    tab_arg,
    text_arg,
)
from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.modules.tool.base import ToolContext

ACTIONS = ("list", "new", "select", "close", "close_browser")


@register_builtin("browser_tabs")
class BrowserTabsTool(BrowserTool):
    """Manage the session's tabs; ``close_browser`` ends the browser until the next call."""

    url_arguments = ("url",)
    arguments = ("action", TAB_ARG, "url")
    neutral = {"action": "list", TAB_ARG: "", "url": ""}

    @property
    def tool_name(self) -> str:
        return "browser_tabs"

    @property
    def description(self) -> str:
        return (
            "List, open, select or close browser tabs, or close the browser. Use "
            "it to manage pages a site opened or to start over with a fresh "
            "browser."
        )

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(ACTIONS),
                    "description": "Default list",
                },
                TAB_ARG: {
                    "type": "string",
                    "description": 'Tab id for select or close; else ""',
                },
                "url": {
                    "type": "string",
                    "description": 'URL for new; else ""',
                },
            },
        }

    def parse(self, args: dict[str, Any], context: ToolContext | None) -> dict:
        action = text_arg(args, "action") or text_arg(args, BODY_ARG) or "list"
        if action not in ACTIONS:
            raise ArgumentError(
                f"action must be one of {', '.join(ACTIONS)}; got {action!r}"
            )
        tab, url = tab_arg(args), text_arg(args, "url")
        if action == "select" and tab is None:
            raise ArgumentError(
                "select needs tab, a tab id such as t2; call browser_tabs with "
                "action list to see them"
            )
        if url is not None and action != "new":
            raise ArgumentError(
                'url applies to action new; set url to "" or action to new'
            )
        if tab is not None and action not in ("select", "close"):
            raise ArgumentError(
                'tab applies to action select or close; set tab to "" or action '
                "to select or close"
            )
        return {"action": action, "tab": tab, "url": url}

    async def run(self, session: Any, call: dict, context: ToolContext | None) -> Any:
        match call["action"]:
            case "new":
                return await session.tab_new(call["url"])
            case "select":
                return await session.tab_select(call["tab"])
            case "close":
                return await session.tab_close(call["tab"])
            case "close_browser":
                return await session.close_browser()
        return await session.tabs_list()
