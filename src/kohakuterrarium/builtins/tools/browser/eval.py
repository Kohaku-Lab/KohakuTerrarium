"""``browser_eval``: run JavaScript in the page and return its JSON result."""

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


@register_builtin("browser_eval")
class BrowserEvalTool(BrowserTool):
    """Evaluate an expression or function in the page; with ``ref`` it receives the element."""

    arguments = ("expression", "ref", TAB_ARG)
    neutral = {"ref": "", TAB_ARG: ""}
    failure = "the script did not succeed; the result below says why"

    @property
    def tool_name(self) -> str:
        return "browser_eval"

    @property
    def description(self) -> str:
        return (
            "Run JavaScript in the browser page and return JSON. Use it to read "
            "state the snapshot does not show."
        )

    def parse(self, args: dict[str, Any], context: ToolContext | None) -> dict:
        expression = args.get("expression")
        if expression is None or expression == "":
            expression = args.get(BODY_ARG)
        if not isinstance(expression, str) or not expression.strip():
            raise ArgumentError(
                "expression is required: a JavaScript expression or function"
            )
        return {
            "expression": expression,
            "ref": text_arg(args, "ref"),
            "tab_id": tab_arg(args),
        }

    async def run(self, session: Any, call: dict, context: ToolContext | None) -> Any:
        return await session.evaluate(
            call["expression"], ref=call["ref"], tab_id=call["tab_id"]
        )
