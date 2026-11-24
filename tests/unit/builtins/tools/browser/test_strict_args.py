"""Strict-mode-safe arguments: a call with every schema property filled in at
its neutral value must mean exactly what the minimal call means.

The filled call is derived from ``llm/tools/schemas.py``, so a property added
to a schema later is covered here, and a property without a declared neutral
value fails the test.
"""

import pytest

from kohakuterrarium.browser.actions import ACTION_NEUTRALS, parse_actions
from kohakuterrarium.builtins.tools.browser.act import BrowserActTool
from kohakuterrarium.builtins.tools.browser.common import ArgumentError
from kohakuterrarium.builtins.tools.browser.eval import BrowserEvalTool
from kohakuterrarium.builtins.tools.browser.navigate import BrowserNavigateTool
from kohakuterrarium.builtins.tools.browser.screenshot import BrowserScreenshotTool
from kohakuterrarium.builtins.tools.browser.snapshot import BrowserSnapshotTool
from kohakuterrarium.builtins.tools.browser.tabs import BrowserTabsTool
from kohakuterrarium.llm.tools.schemas import _BUILTIN_SCHEMAS as BUILTIN_SCHEMAS

TOOLS = {
    tool.tool_name: tool
    for tool in (
        BrowserNavigateTool(),
        BrowserSnapshotTool(),
        BrowserActTool(),
        BrowserScreenshotTool(),
        BrowserTabsTool(),
        BrowserEvalTool(),
    )
}
FILES = ["/a"]

MINIMAL_CALLS = {
    "browser_navigate": [
        {"url": "http://shop.test/"},
        {"action": "back"},
        {"action": "forward"},
        {"action": "reload"},
    ],
    "browser_snapshot": [
        {},
        {"ref": "e3", "depth": 2},
        {"query": "Buy"},
        {"interactive": True},
        {"boxes": True},
    ],
    "browser_act": [{"actions": [{"kind": "click", "ref": "e1"}]}],
    "browser_screenshot": [{}, {"ref": "e2"}, {"full_page": True}],
    "browser_tabs": [
        {},
        {"action": "new", "url": "http://shop.test/"},
        {"action": "new"},
        {"action": "select", "tab": "t2"},
        {"action": "close", "tab": "t2"},
        {"action": "close"},
        {"action": "close_browser"},
    ],
    "browser_eval": [
        {"expression": "document.title"},
        {"expression": "e => e.id", "ref": "e4"},
    ],
}

MINIMAL_ACTIONS = [
    {"kind": "click", "ref": "e1"},
    {"kind": "click", "x": 5, "y": 6},
    {"kind": "click", "ref": "e1", "button": "right", "double": True},
    {"kind": "type", "ref": "e1", "text": "hi"},
    {"kind": "type", "ref": "e1", "secret": "pw"},
    {"kind": "type", "ref": "e1"},
    {"kind": "select", "ref": "e1", "values": ["Red"]},
    {"kind": "check", "ref": "e1"},
    {"kind": "check", "ref": "e1", "checked": False},
    {"kind": "press", "key": "Enter"},
    {"kind": "press", "key": "a", "ref": "e2"},
    {"kind": "hover", "ref": "e1"},
    {"kind": "hover", "x": 5, "y": 6},
    {"kind": "scroll"},
    {"kind": "scroll", "ref": "e1"},
    {"kind": "scroll", "dy": -300},
    {"kind": "scroll", "dx": 200},
    {"kind": "drag", "ref": "e1", "to": "e2"},
    {"kind": "drag", "x": 1, "y": 2, "to_x": 3, "to_y": 4},
    {"kind": "upload", "ref": "e1", "files": [0]},
    {"kind": "wait", "text": "Done"},
    {"kind": "wait", "text_gone": "Loading"},
    {"kind": "wait", "seconds": 2},
    {"kind": "dialog"},
    {"kind": "dialog", "accept": False},
    {"kind": "dialog", "text": "Orion"},
]


def schema(name: str) -> dict:
    return BUILTIN_SCHEMAS[name]


def item_schema() -> dict:
    return schema("browser_act")["properties"]["actions"]["items"]


def filled(minimal: dict, properties: dict, neutral: dict, required=()) -> dict:
    """``minimal`` plus every other schema property at its declared neutral value."""
    call = dict(minimal)
    for name in properties:
        if name in call or name in required:
            continue
        assert (
            name in neutral
        ), f"schema property {name!r} has no declared neutral value"
        call[name] = neutral[name]
    return call


def session_call(tool_name: str, args: dict):
    call = TOOLS[tool_name].call_for(args, None)
    if tool_name == "browser_act":
        call = dict(call, actions=parse_actions(call["actions"], files=FILES))
    return call


@pytest.mark.parametrize("tool_name", sorted(TOOLS))
def test_every_optional_property_declares_a_neutral_value(tool_name):
    tool = TOOLS[tool_name]
    spec = schema(tool_name)
    required = set(spec.get("required", ()))
    for name, prop in spec["properties"].items():
        if name in required:
            continue
        assert name in tool.neutral, f"{tool_name}.{name} has no neutral value"
        if "enum" in prop:
            assert tool.neutral[name] in prop["enum"], f"{tool_name}.{name}"


def test_every_action_field_declares_a_neutral_value():
    for name, prop in item_schema()["properties"].items():
        if name == "kind":
            continue
        assert name in ACTION_NEUTRALS, f"action field {name!r} has no neutral value"
        if "enum" in prop:
            assert ACTION_NEUTRALS[name] in prop["enum"], name


@pytest.mark.parametrize(
    "tool_name,minimal",
    [(name, call) for name, calls in MINIMAL_CALLS.items() for call in calls],
)
def test_filled_call_means_the_minimal_call(tool_name, minimal):
    spec = schema(tool_name)
    full = filled(minimal, spec["properties"], TOOLS[tool_name].neutral)
    assert session_call(tool_name, full) == session_call(tool_name, minimal)


@pytest.mark.parametrize("action", MINIMAL_ACTIONS, ids=lambda a: str(a))
def test_filled_action_means_the_minimal_action(action):
    full = filled(action, item_schema()["properties"], ACTION_NEUTRALS, ("kind",))
    assert parse_actions([full], files=FILES, secrets=["pw"]) == parse_actions(
        [action], files=FILES, secrets=["pw"]
    )
    tool_call = filled(
        {"actions": [full]}, schema("browser_act")["properties"], BrowserActTool.neutral
    )
    assert session_call("browser_act", tool_call)["actions"] == parse_actions(
        [action], files=FILES
    )


def test_the_refused_smoke_call_is_a_reload():
    call = TOOLS["browser_navigate"].call_for(
        {"url": "http://127.0.0.1:54330/dialogs/", "action": "reload", "tab": ""},
        None,
    )
    assert call == {"url": None, "action": "reload", "tab_id": None}


class TestPrecedence:
    def one(self, **fields):
        return parse_actions([fields], files=FILES)[0]

    def test_a_ref_wins_over_coordinates(self):
        action = self.one(kind="click", ref="e1", x=30, y=40)
        assert (action.ref, action.x, action.y) == ("e1", None, None)
        drag = self.one(kind="drag", ref="e1", x=3, y=4, to="e2", to_x=5, to_y=6)
        assert (drag.ref, drag.to, drag.x, drag.to_x) == ("e1", "e2", None, None)

    def test_coordinates_need_both_values_and_zero_is_a_real_coordinate(self):
        action = self.one(kind="click", ref="", x=0, y=40)
        assert (action.x, action.y) == (0.0, 40.0)

    def test_a_secret_wins_over_text_and_empty_text_clears(self):
        typed = self.one(kind="type", ref="e1", text="ignored", secret="pw")
        assert (typed.secret, typed.text) == ("pw", None)
        cleared = self.one(kind="type", ref="e1", text="", secret="")
        assert (cleared.secret, cleared.text) == (None, "")

    def test_wait_takes_text_then_text_gone_then_seconds(self):
        assert self.one(kind="wait", text="a", text_gone="b", seconds=2).text == "a"
        gone = self.one(kind="wait", text="", text_gone="b", seconds=2)
        assert (gone.text, gone.text_gone, gone.seconds) == (None, "b", None)
        with pytest.raises(Exception, match="'text', 'text_gone' or 'seconds' above 0"):
            self.one(kind="wait", text="", text_gone="", seconds=0)

    def test_scroll_takes_ref_then_offsets(self):
        assert self.one(kind="scroll", ref="e1", dx=0, dy=500).ref == "e1"
        by = self.one(kind="scroll", ref="", dx=0, dy=-50)
        assert (by.ref, by.dx, by.dy) == (None, 0.0, -50.0)

    def test_screenshot_ref_wins_over_full_page(self):
        call = TOOLS["browser_screenshot"].call_for(
            {"ref": "e2", "full_page": True}, None
        )
        assert (call["ref"], call["full_page"]) == ("e2", False)

    @pytest.mark.parametrize(
        "fields,message",
        [
            (
                {"kind": "click", "ref": "e1", "text": "hello"},
                "field 'text' does not apply to click; set it to \"\"",
            ),
            (
                {"kind": "hover", "ref": "e1", "button": "right"},
                "field 'button' does not apply to hover; set it to \"left\"",
            ),
            (
                {"kind": "press", "key": "a", "seconds": 3},
                "field 'seconds' does not apply to press; set it to 0",
            ),
        ],
    )
    def test_a_real_value_on_a_field_of_another_kind_is_an_error(self, fields, message):
        with pytest.raises(Exception) as info:
            self.one(**fields)
        assert message in str(info.value)

    def test_default_true_flags_are_ignored_on_other_kinds(self):
        assert self.one(kind="click", ref="e1", accept=False, checked=False).ref == "e1"


class TestTextFormat:
    def test_string_values_are_neutral_too(self):
        call = TOOLS["browser_snapshot"].call_for(
            {"depth": "0", "interactive": "false", "boxes": "false", "tab": ""}, None
        )
        assert call == TOOLS["browser_snapshot"].call_for({}, None)
        nav = TOOLS["browser_navigate"].call_for(
            {"content": "http://shop.test/", "action": "goto"}, None
        )
        assert nav["url"] == "http://shop.test/" and nav["action"] is None

    def test_the_call_body_still_carries_actions(self):
        call = TOOLS["browser_act"].call_for(
            {"content": '[{"kind": "click", "ref": "e1", "x": "0", "y": "0"}]'}, None
        )
        assert parse_actions(call["actions"])[0].ref == "e1"


class TestWayOut:
    @pytest.mark.parametrize(
        "tool_name,args,message",
        [
            (
                "browser_navigate",
                {"action": "goto"},
                "url is required for action goto; give a url, or set action to "
                "back, forward or reload",
            ),
            (
                "browser_tabs",
                {"action": "list", "tab": "t2"},
                'tab applies to action select or close; set tab to "" or action '
                "to select or close",
            ),
            (
                "browser_tabs",
                {"action": "select"},
                "select needs tab, a tab id such as t2; call browser_tabs with "
                "action list to see them",
            ),
            (
                "browser_tabs",
                {"action": "close", "url": "http://x.test/"},
                'url applies to action new; set url to "" or action to new',
            ),
        ],
    )
    def test_refusals_say_what_to_change(self, tool_name, args, message):
        with pytest.raises(ArgumentError) as info:
            TOOLS[tool_name].call_for(args, None)
        assert message in str(info.value)
