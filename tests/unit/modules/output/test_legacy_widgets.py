"""Legacy UI kinds on the widget bus: agent-side results and legacy outputs.

The characterization tests pin what ``show_card``, ``ask_user`` and permgate
return and what an output without ``on_widget`` receives; they were recorded
against the pre-widget implementation and must keep passing unchanged.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from kohakuterrarium.builtins.plugins.permgate.plugin import PermGatePlugin
from kohakuterrarium.builtins.tools.ask_user import AskUserTool
from kohakuterrarium.builtins.tools.show_card import ShowCardTool
from kohakuterrarium.modules.output.event import ACTION_TIMEOUT, OutputEvent, UIReply
from kohakuterrarium.modules.output.router import OutputRouter
from kohakuterrarium.modules.plugin.base import PluginBlockError
from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.testing.output import OutputRecorder


class LegacyOutput(OutputRecorder):
    """An output written before widgets: switches on legacy types only."""

    def __init__(self) -> None:
        super().__init__()
        self.legacy: list[OutputEvent] = []
        self.superseded: list[str] = []

    async def emit(self, event: OutputEvent) -> None:
        self.legacy.append(event)
        await super().emit(event)

    def on_supersede(self, event_id: str) -> None:
        self.superseded.append(event_id)


def _router() -> tuple[OutputRouter, LegacyOutput]:
    out = LegacyOutput()
    return OutputRouter(out), out


def _ctx(router: OutputRouter) -> ToolContext:
    agent = SimpleNamespace(output_router=router)
    return ToolContext(agent_name="t", session=None, working_dir=Path("."), agent=agent)


async def _pending_id(router: OutputRouter) -> str:
    for _ in range(400):
        if router._pending_replies:
            return next(iter(router._pending_replies))
        await asyncio.sleep(0.005)
    raise AssertionError("no pending interactive event")


async def _reply(router, action_id, values=None) -> tuple[str, tuple[bool, str]]:
    event_id = await _pending_id(router)
    status = router.submit_reply_with_status(
        UIReply(event_id=event_id, action_id=action_id, values=values or {})
    )
    return event_id, status


CARD_ARGS = {
    "title": "Deploy?",
    "actions": [
        {"id": "yes", "label": "Yes"},
        {"id": "docs", "label": "Docs", "url": "https://example.com"},
    ],
}


class TestShowCardCharacterization:
    async def test_reply_with_values(self):
        router, out = _router()
        task = asyncio.create_task(ShowCardTool().execute(CARD_ARGS, _ctx(router)))
        event_id, status = await _reply(router, "yes", {"note": "go"})
        result = await task
        assert status == (True, "accepted")
        assert result.output == "action: yes\nvalues: {'note': 'go'}"
        assert result.exit_code == 0 and not result.error
        (event,) = [e for e in out.legacy if e.type == "card"]
        assert event.id == event_id and event.interactive is True
        assert event.payload["title"] == "Deploy?"
        assert out.superseded == [event_id]
        assert router._pending_replies == {}

    async def test_reply_without_values_and_late_reply(self):
        router, out = _router()
        task = asyncio.create_task(ShowCardTool().execute(CARD_ARGS, _ctx(router)))
        event_id, _ = await _reply(router, "yes")
        assert (await task).output == "action: yes"
        late = router.submit_reply_with_status(
            UIReply(event_id=event_id, action_id="x")
        )
        assert late == (False, "unknown")
        assert out.superseded == [event_id]

    async def test_timeout(self):
        router, out = _router()
        args = {**CARD_ARGS, "timeout_s": 0.05}
        result = await ShowCardTool().execute(args, _ctx(router))
        assert result.output == "card timed out without reply"
        (event,) = [e for e in out.legacy if e.type == "card"]
        assert out.superseded == [event.id]

    async def test_display_only_card(self):
        router, out = _router()
        result = await ShowCardTool().execute({"title": "FYI"}, _ctx(router))
        assert result.output == "card displayed"
        (event,) = [e for e in out.legacy if e.type == "card"]
        assert event.interactive is False and out.superseded == []


class TestAskUserCharacterization:
    async def test_text_empty_and_timeout(self):
        router, out = _router()
        tool = AskUserTool()
        task = asyncio.create_task(tool.execute({"question": "Name?"}, _ctx(router)))
        await _reply(router, "submit", {"text": "Kohaku"})
        assert (await task).output == "Kohaku"

        task = asyncio.create_task(tool.execute({"question": "Name?"}, _ctx(router)))
        await _reply(router, "submit", {})
        assert (await task).output == "(no response)"

        result = await tool.execute(
            {"question": "Name?", "timeout_s": 0.05}, _ctx(router)
        )
        assert result.output == "(no response within timeout)"
        asks = [e for e in out.legacy if e.type == "ask_text"]
        assert len(asks) == 3 and asks[0].payload["prompt"] == "Name?"
        assert out.superseded == [e.id for e in asks]


class TestPermgateCharacterization:
    def _gate(self, router, **options):
        plugin = PermGatePlugin(gated_tools=["bash"], **options)
        plugin._context = SimpleNamespace(
            host_agent=object(), emit_and_wait=router.emit_and_wait
        )
        return plugin

    async def test_allow_deny_and_timeout(self):
        router, out = _router()
        gate = self._gate(router)
        task = asyncio.create_task(gate.pre_tool_execute({}, tool_name="bash"))
        await _reply(router, "allow_once")
        assert await task is None

        task = asyncio.create_task(gate.pre_tool_execute({}, tool_name="bash"))
        await _reply(router, "deny")
        with pytest.raises(PluginBlockError) as denied:
            await task
        assert str(denied.value) == "permgate: tool bash blocked by user (deny)."

        slow = self._gate(router, timeout_s=0.05)
        with pytest.raises(PluginBlockError) as late:
            await slow.pre_tool_execute({}, tool_name="bash")
        assert str(late.value) == (
            "permgate: no response for bash within 0.05s — denied."
        )
        confirms = [e for e in out.legacy if e.type == "confirm"]
        assert len(confirms) == 3 and confirms[0].surface == "modal"
        assert confirms[0].payload["prompt"] == "Allow tool: bash?"


class TestRouterCharacterization:
    async def test_emit_and_wait_contract(self):
        router, out = _router()
        with pytest.raises(ValueError, match="interactive=True"):
            await router.emit_and_wait(OutputEvent(type="confirm"))
        with pytest.raises(ValueError, match="non-empty event.id"):
            await router.emit_and_wait(OutputEvent(type="confirm", interactive=True))
        event = OutputEvent(type="confirm", interactive=True, id="c1")
        task = asyncio.create_task(router.emit_and_wait(event))
        await _pending_id(router)
        with pytest.raises(ValueError, match="already pending: c1"):
            await router.emit_and_wait(event)
        router.submit_reply(UIReply(event_id="c1", action_id="ok"))
        reply = await task
        assert reply.action_id == "ok" and reply.event_id == "c1"
        timed = await router.emit_and_wait(
            OutputEvent(type="confirm", interactive=True, id="c2"), timeout_s=0.01
        )
        assert timed.action_id == ACTION_TIMEOUT and timed.is_timeout
        assert router.submit_reply_with_status(
            UIReply(event_id="nobody", action_id="x")
        ) == (False, "unknown")
        assert out.superseded == ["c1", "c2"]

    async def test_cancelled_wait_closes(self):
        router, out = _router()
        event = OutputEvent(type="confirm", interactive=True, id="c3")
        task = asyncio.create_task(router.emit_and_wait(event))
        await _pending_id(router)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert out.superseded == ["c3"] and router._pending_replies == {}

    async def test_display_kinds_pass_through_unchanged(self):
        router, out = _router()
        await router.emit(OutputEvent(type="progress", id="p1", payload={"value": 1}))
        await router.emit(
            OutputEvent(type="progress", update_target="p1", payload={"value": 2})
        )
        await router.emit(
            OutputEvent(type="notification", surface="toast", payload={"text": "hi"})
        )
        await router.emit(OutputEvent(type="ui_supersede", payload={"event_id": "p1"}))
        assert [(e.type, e.id, e.update_target) for e in out.legacy] == [
            ("progress", "p1", None),
            ("progress", None, "p1"),
            ("notification", None, None),
            ("ui_supersede", None, None),
        ]
        assert out.legacy[1].payload == {"value": 2}
        assert out.legacy[2].surface == "toast"
