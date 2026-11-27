"""``browser_act``: run an ordered batch of page actions and report what changed."""

from typing import Any

from kohakuterrarium.browser.actions import BUTTONS, KINDS
from kohakuterrarium.builtins.tools.browser.common import (
    BODY_ARG,
    TAB_ARG,
    ArgumentError,
    BrowserTool,
    files_list,
    guarded_path,
    tab_arg,
    tab_schema,
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
    neutral = {FILES_ARG: [], TAB_ARG: ""}
    failure = "an action did not succeed; the statuses below say which and why"

    @property
    def tool_name(self) -> str:
        return "browser_act"

    @property
    def description(self) -> str:
        return (
            "Run a batch of browser actions by ref. Use it to interact with a page; "
            "returns statuses and what changed."
        )

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "actions": {
                    "type": "array",
                    "description": (
                        "Run in order; stops at the first failure. Unused fields: "
                        '"", 0, [], false'
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string", "enum": list(KINDS)},
                            "ref": {"type": "string"},
                            "text": {"type": "string"},
                            "secret": {"type": "string"},
                            "submit": {"type": "boolean"},
                            "slowly": {"type": "boolean"},
                            "values": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    'For select: visible option labels (e.g. ["Two"]), '
                                    "not HTML value attributes."
                                ),
                            },
                            "checked": {"type": "boolean"},
                            "key": {"type": "string"},
                            "x": {"type": "number"},
                            "y": {"type": "number"},
                            "button": {"type": "string", "enum": list(BUTTONS)},
                            "double": {"type": "boolean"},
                            "modifiers": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "dx": {"type": "number"},
                            "dy": {"type": "number"},
                            "to": {"type": "string"},
                            "to_x": {"type": "number"},
                            "to_y": {"type": "number"},
                            "files": {"type": "array", "items": {"type": "integer"}},
                            "seconds": {"type": "number"},
                            "text_gone": {"type": "string"},
                            "accept": {"type": "boolean"},
                        },
                        "required": ["kind"],
                    },
                },
                FILES_ARG: {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Paths upload actions index into; [] = none",
                },
                TAB_ARG: tab_schema(),
            },
            "required": ["actions"],
        }

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
