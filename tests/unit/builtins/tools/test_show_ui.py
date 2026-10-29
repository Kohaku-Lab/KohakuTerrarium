"""show_ui: open, update and wait over a real router; gate and fallback."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from kohakuterrarium.builtins.plugin_catalog import lookup_plugin
from kohakuterrarium.builtins.plugins.ui_declarative.plugin import UiDeclarativePlugin
from kohakuterrarium.builtins.tool_catalog import get_builtin_tool
from kohakuterrarium.builtins.tools.show_ui import ShowUiTool
from kohakuterrarium.core.tool_gate import tool_available
from kohakuterrarium.modules.output.router import OutputRouter
from kohakuterrarium.modules.output.widgets import WidgetMessage
from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.testing.output import OutputRecorder

SPEC = {
    "title": "Split {{ people }} ways",
    "state": {"total": 120, "people": 3},
    "computed": {"each": "total / people"},
    "root": {
        "type": "stack",
        "children": [
            {"type": "number", "label": "People", "bind": "people"},
            {"type": "stat", "label": "Each", "value": "{{ fmt(each, 2) }}"},
            {"type": "button", "label": "Send", "send": "result"},
        ],
    },
}


class UiOutput(OutputRecorder):
    def __init__(self, renderers=None):
        super().__init__()
        self.renderers = {"kt.ui": 1} if renderers is None else renderers
        self.widget_events = []

    def ui_renderers(self):
        return dict(self.renderers)

    async def on_widget(self, event):
        self.widget_events.append(event)


def _context(output=None):
    output = output or UiOutput()
    router = OutputRouter(output)
    agent = SimpleNamespace(output_router=router)
    ctx = ToolContext(agent_name="a", session=None, working_dir=Path("."), agent=agent)
    return ctx, router, output


async def _run(tool, args, ctx):
    return await tool.execute(args, context=ctx)


class TestOpen:
    async def test_open_emits_validated_spec_with_fallback_and_title(self):
        ctx, router, out = _context()
        result = await _run(ShowUiTool(), {"spec": SPEC}, ctx)
        assert result.error is None
        (event,) = out.widget_events
        wid = event.id
        assert result.output == f'ui {wid} "Split 3 ways" shown in chat'
        payload = event.payload
        assert payload["renderer"] == "kt.ui" and payload["spec"]["version"] == 1
        assert payload["state"] == {"total": 120, "people": 3}
        assert payload["routing"] == "agent" and event.interactive is True
        assert "Each: 40.00" in payload["fallback"]
        assert payload["fallback"].startswith("Split 3 ways")

    async def test_page_layout_defaults_to_canvas_and_display_only_is_static(self):
        ctx, _, out = _context()
        spec = {"layout": "page", "root": {"type": "text", "value": "hi"}}
        result = await _run(ShowUiTool(), {"spec": spec}, ctx)
        assert result.output.endswith("shown in canvas")
        assert out.widget_events[0].interactive is False
        await _run(ShowUiTool(default_surface="canvas"), {"spec": SPEC}, ctx)
        assert out.widget_events[1].surface == "canvas"
        await _run(ShowUiTool(), {"spec": spec, "surface": "chat"}, ctx)
        assert out.widget_events[2].surface == "chat"

    async def test_invalid_spec_lists_paths_and_emits_nothing(self):
        ctx, _, out = _context()
        bad = {"root": {"type": "number", "bind": "ghost"}}
        result = await _run(ShowUiTool(), {"spec": bad}, ctx)
        assert result.error.startswith("invalid spec:")
        assert "- root.bind:" in result.error
        assert out.widget_events == []
        result = await _run(ShowUiTool(), {}, ctx)
        assert "needs 'spec'" in result.error
        result = await _run(ShowUiTool(), {"spec": SPEC, "surface": "modal"}, ctx)
        assert "surface" in result.error

    async def test_no_renderer_or_no_router_returns_text(self):
        ctx, _, out = _context(UiOutput(renderers={}))
        result = await _run(ShowUiTool(), {"spec": SPEC}, ctx)
        assert result.error is None and "no UI can show this widget" in result.output
        assert "Each: 40.00" in result.output and out.widget_events == []
        result = await _run(ShowUiTool(), {"spec": SPEC}, None)
        assert "no UI is attached" in result.output and "People: 3" in result.output


class TestTextFormatArgs:
    async def test_json_text_arguments_are_decoded(self):
        ctx, router, out = _context()
        tool = ShowUiTool()
        result = await _run(
            tool,
            {
                "spec": json.dumps(SPEC),
                "wait_for": "result, other",
                "timeout_s": "0.01",
            },
            ctx,
        )
        assert "no button or form sends: other" in result.error
        result = await _run(
            tool,
            {"spec": json.dumps(SPEC), "wait_for": '["result"]', "timeout_s": "0.01"},
            ctx,
        )
        assert result.output.endswith("no message before the timeout")
        wid = out.widget_events[0].id
        result = await _run(tool, {"update": wid, "state": '{"people": 5}'}, ctx)
        assert result.output == f"ui {wid} updated"

    async def test_bad_text_arguments_are_errors(self):
        ctx, _, _ = _context()
        tool = ShowUiTool()
        assert (
            "spec is not valid JSON" in (await _run(tool, {"spec": "{oops"}, ctx)).error
        )
        bad_timeout = await _run(tool, {"spec": "{}", "timeout_s": "soon"}, ctx)
        assert "timeout_s must be a number" in bad_timeout.error
        empty = await _run(tool, {"spec": ""}, ctx)
        assert "must be an object" in empty.error


class TestWait:
    async def test_wait_returns_the_message(self):
        ctx, router, out = _context()
        tool = ShowUiTool()
        task = asyncio.create_task(
            _run(tool, {"spec": SPEC, "wait_for": ["result"], "timeout_s": 5}, ctx)
        )
        while not out.widget_events:
            await asyncio.sleep(0)
        wid = out.widget_events[0].id
        status = router.widgets.submit_message(
            WidgetMessage(widget_id=wid, name="result", data={"each": 40})
        )
        assert status == "accepted"
        result = await task
        assert result.output.endswith('message result: {"each": 40}')

    async def test_wait_timeout_close_and_unknown_names(self):
        ctx, router, out = _context()
        tool = ShowUiTool()
        result = await _run(
            tool, {"spec": SPEC, "wait_for": ["result"], "timeout_s": 0.01}, ctx
        )
        assert result.output.endswith("no message before the timeout")
        result = await _run(tool, {"spec": SPEC, "wait_for": ["nope"]}, ctx)
        assert "no button or form sends: nope" in result.error
        result = await _run(tool, {"spec": SPEC, "wait_for": 3}, ctx)
        assert "list" in result.error

        task = asyncio.create_task(
            _run(tool, {"spec": SPEC, "wait_for": ["result"]}, ctx)
        )
        while len(out.widget_events) < 2:
            await asyncio.sleep(0)
        await router.widgets.close(out.widget_events[-1].id)
        assert (await task).output.endswith("widget closed")


class TestUpdate:
    async def test_state_update_merges_and_refreshes_fallback(self):
        ctx, router, out = _context()
        tool = ShowUiTool()
        await _run(tool, {"spec": SPEC}, ctx)
        wid = out.widget_events[0].id
        result = await _run(tool, {"update": wid, "state": {"people": 4}}, ctx)
        assert result.output == f"ui {wid} updated"
        patch = out.widget_events[-1].payload
        assert patch["state"] == {"people": 4}
        assert "Each: 30.00" in patch["fallback"]
        assert router.widgets.registry.get(wid).state["people"] == 4

    async def test_spec_update_keeps_user_values_for_kept_keys(self):
        ctx, router, out = _context()
        tool = ShowUiTool()
        await _run(tool, {"spec": SPEC}, ctx)
        wid = out.widget_events[0].id
        router.widgets.submit_state(wid, {"people": 6})
        new_spec = {**SPEC, "state": {"people": 2, "total": 300, "extra": 1}}
        await _run(tool, {"update": wid, "spec": new_spec}, ctx)
        patch = out.widget_events[-1].payload
        assert patch["spec"]["state"]["total"] == 300
        assert patch["state"] == {"people": 6, "total": 120, "extra": 1}
        assert patch["title"] == "Split 2 ways"

    async def test_update_errors(self):
        ctx, router, out = _context()
        tool = ShowUiTool()
        assert (
            "no show_ui widget" in (await _run(tool, {"update": "w_nope"}, ctx)).error
        )
        await _run(tool, {"spec": SPEC}, ctx)
        wid = out.widget_events[0].id
        assert (
            "needs 'spec' or 'state'" in (await _run(tool, {"update": wid}, ctx)).error
        )
        bad = await _run(tool, {"update": wid, "spec": {"root": {}}}, ctx)
        assert bad.error.startswith("invalid spec:")
        assert "object" in (await _run(tool, {"update": wid, "state": 3}, ctx)).error
        await router.widgets.close(wid)
        closed = await _run(tool, {"update": wid, "state": {"people": 1}}, ctx)
        assert "closed" in closed.error

    async def test_update_can_wait(self):
        ctx, router, out = _context()
        tool = ShowUiTool()
        await _run(tool, {"spec": SPEC}, ctx)
        wid = out.widget_events[0].id
        task = asyncio.create_task(
            _run(
                tool,
                {"update": wid, "state": {"people": 2}, "wait_for": ["result"]},
                ctx,
            )
        )
        while len(out.widget_events) < 2:
            await asyncio.sleep(0)
        router.widgets.submit_message(WidgetMessage(widget_id=wid, name="result"))
        assert (await task).output == f"ui {wid} updated; message result: {{}}"


class TestGateAndPlugin:
    def test_requires_kt_ui_and_is_a_documented_builtin(self):
        tool = get_builtin_tool("show_ui")
        assert isinstance(tool, ShowUiTool)
        assert tool_available(tool, {"kt.ui": 1}) is True
        assert tool_available(tool, {"kt.app": 1}) is False
        assert "Not for" in tool.description
        assert len(tool.prompt_contribution()) <= 600

    def test_plugin_contributes_the_tool_with_its_options(self):
        assert lookup_plugin("ui.declarative") is not None
        (tool,) = UiDeclarativePlugin(default_surface="canvas").contribute_tools()
        assert isinstance(tool, ShowUiTool) and tool.default_surface == "canvas"
        (fallback_tool,) = UiDeclarativePlugin(
            default_surface="moon"
        ).contribute_tools()
        assert fallback_tool.default_surface == "chat"
