"""Unit tests for :mod:`kohakuterrarium.studio.attach.widget_frames`."""

import asyncio
from types import SimpleNamespace

from kohakuterrarium.builtins.kt_ui.inline_state import InlineUiStates, block_ids
from kohakuterrarium.modules.output.router.router import OutputRouter
from kohakuterrarium.modules.output.widgets.service import WidgetRecord
from kohakuterrarium.studio.attach._event_stream import StreamOutput
from kohakuterrarium.studio.attach.widget_frames import (
    WIDGET_CLIENT_FRAMES,
    apply_client_hello,
    handle_widget_frame,
    handle_widget_msg,
    handle_widget_state,
)
from kohakuterrarium.testing.output import OutputRecorder


class _Agent:
    def __init__(self):
        self.injected = []
        self.session_store = None
        self.config = SimpleNamespace(name="alice")
        self.output_router = OutputRouter(OutputRecorder())
        self.output_router.widgets.bind_agent(self)
        self.output_router.widgets.registry.add(
            WidgetRecord(widget_id="w1", renderer="kt.ui")
        )

    async def inject_event(self, event):
        self.injected.append(event)


def test_hello_reaches_every_stream_output_and_tolerates_bad_maps():
    a = StreamOutput("a", asyncio.Queue(), [])
    b = StreamOutput("b", asyncio.Queue(), [])
    apply_client_hello({"renderers": {"kt.app": 1}}, [a, b, object()])
    assert a.ui_renderers() == b.ui_renderers() == {"kt.app": 1}
    apply_client_hello({"renderers": ["kt.app"]}, [a])
    assert a.ui_renderers() == {}


async def test_message_routes_to_agent_and_acks():
    agent = _Agent()
    sent = []
    handle_widget_msg(
        {"event_id": "w1", "name": "go", "data": {"x": 1}, "origin": "app", "ts": 5},
        agent,
        "alice",
        sent.append,
    )
    await agent.output_router.widgets.drain()
    assert sent[0]["type"] == "widget_ack" and sent[0]["kind"] == "message"
    assert sent[0]["status"] == "accepted" and sent[0]["source"] == "alice"
    (event,) = agent.injected
    assert event.context["origin"] == "app" and event.context["data"] == {"x": 1}


async def test_message_edge_cases():
    agent = _Agent()
    sent = []
    handle_widget_msg({"name": "go"}, agent, "alice", sent.append)
    assert sent == [], "no event id, no ack"
    handle_widget_msg({"event_id": "w1", "name": 3}, agent, "alice", sent.append)
    handle_widget_msg(
        {"event_id": "w1", "name": "go"}, SimpleNamespace(), "x", sent.append
    )
    handle_widget_msg(
        {"event_id": "w1", "name": "go", "data": "notadict", "origin": "evil"},
        agent,
        "alice",
        sent.append,
    )
    await agent.output_router.widgets.drain()
    assert [s["status"] for s in sent] == ["unknown", "unknown", "accepted"]
    assert agent.injected[0].context["origin"] == "renderer"
    assert agent.injected[0].context["data"] == {}


async def test_state_acks_only_failures_and_skips_the_source():
    agent = _Agent()
    source = StreamOutput("src", asyncio.Queue(), [])
    agent.output_router.add_secondary(source)
    sent = []
    handle_widget_state(
        {"event_id": "w1", "state": {"a": 1}}, agent, source, "alice", sent.append
    )
    handle_widget_state(
        {"event_id": "w1", "state": "x"}, agent, source, "alice", sent.append
    )
    handle_widget_state(
        {"event_id": "w9", "state": {}}, agent, source, "alice", sent.append
    )
    handle_widget_state(
        {"event_id": "w1", "state": {}}, SimpleNamespace(), None, "x", sent.append
    )
    handle_widget_state({"state": {}}, agent, source, "alice", sent.append)
    await agent.output_router.widgets.drain()
    assert [(s["kind"], s["status"]) for s in sent] == [
        ("state", "dropped"),
        ("state", "unknown"),
        ("state", "unknown"),
    ]
    assert agent.output_router.widgets.registry.get("w1").state == {"a": 1}
    assert source._q.empty(), "the sending client gets no echo"


class _Message:
    role = "assistant"
    content = '<ui><state n={1}/><number bind="n"/></ui>'


async def test_inline_ui_state_frames_merge_broadcast_and_ack_failures():
    agent = _Agent()
    agent.controller = SimpleNamespace(
        conversation=SimpleNamespace(get_messages=lambda: [_Message()])
    )
    agent.output_router.inline_ui = InlineUiStates(agent.output_router)
    agent.output_router.inline_ui.bind_agent(agent)
    source = StreamOutput("src", asyncio.Queue(), [])
    other = StreamOutput("other", asyncio.Queue(), [])
    agent.output_router.add_secondary(source)
    agent.output_router.add_secondary(other)
    ((block, _),) = block_ids(_Message.content)
    frame = {"ui_turn": 1, "ui_branch": 1, "block": block, "state": {"n": 4}}
    sent = []
    assert "inline_ui_state" in WIDGET_CLIENT_FRAMES
    handle_widget_frame("inline_ui_state", frame, agent, source, "alice", sent.append)
    handle_widget_frame(
        "inline_ui_state",
        {**frame, "block": "deadbeef.0"},
        agent,
        source,
        "alice",
        sent.append,
    )
    handle_widget_frame(
        "inline_ui_state",
        {**frame, "state": {"x": 1}},
        agent,
        source,
        "alice",
        sent.append,
    )
    handle_widget_frame(
        "inline_ui_state", frame, SimpleNamespace(), None, "x", sent.append
    )
    await asyncio.sleep(0)
    assert [(s["kind"], s["status"], s["event_id"]) for s in sent] == [
        ("inline_ui_state", "unknown", "1:1:deadbeef.0"),
        ("inline_ui_state", "dropped", f"1:1:{block}"),
        ("inline_ui_state", "unknown", f"1:1:{block}"),
    ]
    assert agent.output_router.inline_ui.get(1, 1, block) == {"n": 4}
    assert source._q.empty(), "the sending client gets no echo"
    forwarded = other._q.get_nowait()
    assert forwarded["source"] == "other"
    assert {
        k: forwarded[k] for k in ("type", "ui_turn", "ui_branch", "block", "state")
    } == {
        "type": "inline_ui_state",
        "ui_turn": 1,
        "ui_branch": 1,
        "block": block,
        "state": {"n": 4},
    }
