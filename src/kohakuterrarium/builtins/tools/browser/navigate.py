"""``browser_navigate``: open a URL or step through the tab's history."""

from typing import Any

from kohakuterrarium.browser.session.types import HISTORY_STEPS
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


@register_builtin("browser_navigate")
class BrowserNavigateTool(BrowserTool):
    """Load a page (or go back / forward / reload) and return its interactive elements."""

    url_arguments = ("url", BODY_ARG)
    arguments = ("url", "action", TAB_ARG)
    neutral = {"url": "", "action": "goto", TAB_ARG: ""}
    failure = "the page did not load; the result below says why"

    @property
    def tool_name(self) -> str:
        return "browser_navigate"

    @property
    def description(self) -> str:
        return (
            "Open a URL in the browser, or go back, forward or reload. Use it to "
            "start browsing or change page; returns the page text with element "
            "refs."
        )

    def parse(self, args: dict[str, Any], context: ToolContext | None) -> dict:
        action = text_arg(args, "action")
        if action is not None and action not in HISTORY_STEPS:
            raise ArgumentError(
                f"action must be one of goto, {', '.join(HISTORY_STEPS)}; got {action!r}"
            )
        url = None
        if action is None:
            url = text_arg(args, "url") or text_arg(args, BODY_ARG)
            if url is None:
                raise ArgumentError(
                    "url is required for action goto; give a url, or set action "
                    "to back, forward or reload"
                )
        return {"url": url, "action": action, "tab_id": tab_arg(args)}

    async def run(self, session: Any, call: dict, context: ToolContext | None) -> Any:
        return await session.navigate(
            call["url"], action=call["action"], tab_id=call["tab_id"]
        )
