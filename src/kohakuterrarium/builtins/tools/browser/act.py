"""``browser_act``: run an ordered batch of page actions and report what changed."""

from typing import Any

from kohakuterrarium.builtins.tools.browser.common import (
    BODY_ARG,
    TAB_ARG,
    ArgumentError,
    BrowserTool,
    files_list,
    guarded_path,
    tab_arg,
)
from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.modules.tool.base import ToolContext

FILES_ARG = "files"


@register_builtin("browser_act")
class BrowserActTool(BrowserTool):
    """Run click / type / select / … actions in order, stopping at the first failure.

    ``actions`` arrives as a list (native calls), a JSON string, or the call
    body (text formats); ``files`` lists the paths ``upload`` actions index.
    """

    path_arguments = {FILES_ARG: "read"}
    arguments = ("actions", FILES_ARG, TAB_ARG)
    failure = "an action did not succeed; the statuses below say which and why"

    @property
    def tool_name(self) -> str:
        return "browser_act"

    @property
    def description(self) -> str:
        return (
            "Run a batch of browser actions (click, type, select, press, wait, "
            "dialog, upload...) by ref. Use it to interact with a page; returns "
            "statuses and what changed."
        )

    def parse(self, args: dict[str, Any], context: ToolContext | None) -> dict:
        actions = args.get("actions")
        if actions is None or actions == "":
            actions = args.get(BODY_ARG)
        if actions is None or actions == "":
            raise ArgumentError(
                'actions is required: a list of action objects, e.g. [{"kind": '
                '"click", "ref": "e5"}]'
            )
        files = []
        for index, raw in enumerate(files_list(args.get(FILES_ARG))):
            path = guarded_path(raw, context, f"files[{index}]")
            if not path.is_file():
                raise ArgumentError(f"files[{index}]: {path} is not a file")
            files.append(str(path))
        return {"actions": actions, "files": files, "tab_id": tab_arg(args)}

    async def run(self, session: Any, call: dict, context: ToolContext | None) -> Any:
        return await session.act(
            call["actions"], files=call["files"], tab_id=call["tab_id"]
        )
