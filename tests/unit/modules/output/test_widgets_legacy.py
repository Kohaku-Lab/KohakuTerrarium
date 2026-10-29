"""Legacy UI kinds as widgets: widget-path delivery, replies and helpers."""

import asyncio
from collections import deque
from pathlib import Path
from types import SimpleNamespace

from kohakuterrarium.builtins.tools.show_card import ShowCardTool
from kohakuterrarium.modules.output import widgets as widgets_mod
from kohakuterrarium.modules.output.event import OutputEvent, UIReply
from kohakuterrarium.modules.output.router import OutputRouter
from kohakuterrarium.modules.output.widgets import WidgetMessage, WidgetRecord
from kohakuterrarium.modules.output.widgets_legacy import (
    legacy_event_from_widget,
    legacy_fallback,
    render_widget_as_legacy,
)
from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.studio.attach._event_stream import StreamOutput
from kohakuterrarium.studio.attach.widget_frames import handle_widget_msg
from kohakuterrarium.testing.output import OutputRecorder

CARD_ARGS = {"title": "Deploy?", "actions": [{"id": "yes", "label": "Yes"}]}


def _web() -> tuple[OutputRouter, StreamOutput, asyncio.Queue]:
    queue: asyncio.Queue = asyncio.Queue()
    web = StreamOutput("web", queue, deque(maxlen=50))
    return OutputRouter(web), web, queue


def _frames(queue: asyncio.Queue) -> list[dict]:
    frames = []
    while not queue.empty():
        frames.append(queue.get_nowait())
    return frames


async def _pending_id(router: OutputRouter) -> str:
    for _ in range(400):
        if router._pending_replies:
            return next(iter(router._pending_replies))
        await asyncio.sleep(0.005)
    raise AssertionError("no pending interactive event")


def _ctx(router: OutputRouter) -> ToolContext:
    agent = SimpleNamespace(output_router=router)
    return ToolContext(agent_name="t", session=None, working_dir=Path("."), agent=agent)


class TestWebWidgetPath:
    async def test_show_card_over_widget_frames_and_widget_msg(self):
        router, _web_out, queue = _web()
        agent = SimpleNamespace(output_router=router)
        acks: list[dict] = []
        task = asyncio.create_task(ShowCardTool().execute(CARD_ARGS, _ctx(router)))
        event_id = await _pending_id(router)
        (opened,) = _frames(queue)
        assert opened["type"] == "widget" and opened["event_id"] == event_id
        assert opened["payload"]["renderer"] == "kt.card"
        assert opened["payload"]["spec"]["title"] == "Deploy?"
        assert opened["payload"]["routing"] == "drop"

        frame = {"event_id": event_id, "name": "yes", "data": {"n": 1}}
        handle_widget_msg(frame, agent, "web", acks.append)
        handle_widget_msg(frame, agent, "web", acks.append)
        result = await task
        assert result.output == "action: yes\nvalues: {'n': 1}"
        assert [a["status"] for a in acks] == ["accepted", "superseded"]
        (closed,) = _frames(queue)
        assert closed == {
            **closed,
            "type": "widget_close",
            "event_id": event_id,
            "payload": {"reason": "replied", "action_id": "yes"},
        }
        handle_widget_msg(frame, agent, "web", acks.append)
        assert acks[-1]["status"] == "unknown"

    async def test_timeout_closes_with_reason(self):
        router, _web_out, queue = _web()
        result = await ShowCardTool().execute(
            {**CARD_ARGS, "timeout_s": 0.02}, _ctx(router)
        )
        assert result.output == "card timed out without reply"
        closed = _frames(queue)[-1]
        assert closed["type"] == "widget_close"
        assert closed["payload"] == {"reason": "timeout"}

    async def test_old_ui_reply_resolves_a_widget_path_wait(self):
        router, _web_out, _queue = _web()
        event = OutputEvent(type="confirm", interactive=True, id="c1")
        task = asyncio.create_task(router.emit_and_wait(event))
        await _pending_id(router)
        assert router.submit_reply_with_status(
            UIReply(event_id="c1", action_id="ok")
        ) == (True, "accepted")
        assert (await task).action_id == "ok"

    async def test_custom_interactive_type_answers_through_widget_msg(self):
        router, _web_out, _queue = _web()
        event = OutputEvent(type="form_x", interactive=True, id="f1")
        task = asyncio.create_task(router.emit_and_wait(event))
        await _pending_id(router)
        status = router.widgets.submit_message(
            WidgetMessage(widget_id="f1", name="send any name", data={"a": 1})
        )
        assert status == "accepted"
        reply = await task
        assert reply.action_id == "send any name" and reply.values == {"a": 1}


class TestMixedOutputs:
    async def test_widget_output_and_legacy_output_each_get_their_form(self):
        legacy = OutputRecorder()
        router, _web_out, queue = _web()
        router.add_secondary(legacy)
        await router.emit(
            OutputEvent(type="notification", surface="toast", payload={"text": "hi"})
        )
        (frame,) = _frames(queue)
        assert frame["payload"]["renderer"] == "kt.notification"
        assert frame["surface"] == "toast" and frame["payload"]["fallback"] == "hi"
        assert [e.type for e in legacy.events] == ["notification"]


class TestHelpers:
    def test_fallback_text(self):
        assert legacy_fallback("card", {"title": "T", "prompt": "P"}) == "T"
        assert legacy_fallback("ask_text", {"prompt": "Name?"}) == "Name?"
        assert legacy_fallback("progress", {"value": 3}) == "progress"

    def test_legacy_event_comes_back_exactly(self):
        original = OutputEvent(type="progress", update_target="p1", payload={"v": 2})
        widget = OutputEvent(
            type="widget",
            update_target="p1",
            payload={"spec": {"v": 2, "label": "x"}},
            metadata={"legacy_event": original},
        )
        assert legacy_event_from_widget(widget) is original

    def test_legacy_widget_without_metadata_is_rebuilt_from_renderer(self):
        widget = OutputEvent(
            type="widget",
            id="c9",
            interactive=True,
            payload={"renderer": "kt.confirm", "spec": {"prompt": "?"}},
        )
        rebuilt = legacy_event_from_widget(widget)
        assert (rebuilt.type, rebuilt.id, rebuilt.payload) == (
            "confirm",
            "c9",
            {"prompt": "?"},
        )
        assert (
            legacy_event_from_widget(
                OutputEvent(type="widget", id="w", payload={"renderer": "kt.ui"})
            )
            is None
        )

    async def test_render_widget_as_legacy(self):
        calls: list = []

        class Out:
            async def emit(self, event):
                calls.append(("emit", event.type))

            def on_widget_close(self, event):
                calls.append(("close", event.update_target))

            def on_activity(self, kind, detail):
                calls.append((kind, detail))

        out = Out()
        await render_widget_as_legacy(
            out,
            OutputEvent(type="widget", id="c", payload={"renderer": "kt.card"}),
        )
        await render_widget_as_legacy(
            out,
            OutputEvent(
                type="widget",
                id="u",
                payload={"renderer": "kt.ui", "fallback": "UI here"},
            ),
        )
        await render_widget_as_legacy(
            out, OutputEvent(type="widget_close", id="u", update_target="u")
        )
        assert calls == [("emit", "card"), ("widget", "UI here"), ("close", "u")]

    def test_registry_evicts_finished_records_only(self, monkeypatch):
        monkeypatch.setattr(widgets_mod, "MAX_RECORDS", 3)
        registry = widgets_mod.WidgetRegistry()
        registry.add(WidgetRecord("live", "kt.ui"))
        registry.add(WidgetRecord("done", "kt.ui", closed=True))
        registry.add(WidgetRecord("note", "kt.card", legacy="card", interactive=False))
        registry.add(WidgetRecord("ask", "kt.confirm", legacy="confirm"))
        assert "done" not in registry and "live" in registry and len(registry) == 3
        registry.add(WidgetRecord("more", "kt.ui"))
        assert "note" not in registry and "ask" in registry

    def test_restored_legacy_widget_answers_unknown(self):
        router = OutputRouter(OutputRecorder())
        router.widgets.registry.restore(
            [
                {
                    "type": "widget",
                    "widget_id": "old",
                    "renderer": "kt.card",
                    "interactive": True,
                }
            ]
        )
        assert router.widgets.registry.get("old").legacy == "card"
        status = router.widgets.submit_message(WidgetMessage("old", "yes"))
        assert status == "unknown"
