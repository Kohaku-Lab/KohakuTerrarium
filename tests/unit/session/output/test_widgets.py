"""Unit tests for :mod:`kohakuterrarium.session.output.widgets`.

Widget events land as typed session rows, and those rows rebuild a
:class:`WidgetRegistry` with the same state.
"""

from kohakuterrarium.modules.output.event import OutputEvent
from kohakuterrarium.modules.output.widgets.service import WidgetRegistry
from kohakuterrarium.session.output.output import SessionOutput
from kohakuterrarium.session.store import SessionStore


def _sink(tmp_path, *, capture_activity=True):
    store = SessionStore(str(tmp_path / "w.kohakutr"))
    return store, SessionOutput("alice", store, None, capture_activity=capture_activity)


EVENTS = [
    OutputEvent(
        type="widget",
        id="w1",
        surface="canvas",
        interactive=True,
        payload={
            "renderer": "kt.app",
            "version": 1,
            "title": "Planner",
            "spec": {"document_url": "a"},
            "state": {"a": 1},
            "fallback": "Planner (web)",
            "routing": "agent",
        },
    ),
    OutputEvent(
        type="widget", id="w1", update_target="w1", payload={"state": {"b": 2}}
    ),
    # A wait flag is live-only and is never part of the history.
    OutputEvent(type="widget", id="w1", update_target="w1", payload={"awaiting": True}),
    OutputEvent(
        type="widget_state",
        id="w1",
        update_target="w1",
        payload={"state": {"a": 1, "b": 2, "c": 3}},
    ),
    OutputEvent(
        type="widget_message",
        id="w1",
        update_target="w1",
        payload={"name": "go", "data": {"x": 1}, "origin": "app", "routed_to": "agent"},
    ),
    OutputEvent(type="widget_close", id="w1", update_target="w1"),
]


async def test_widget_events_are_recorded_as_typed_rows(tmp_path):
    store, out = _sink(tmp_path)
    for event in EVENTS:
        await out.on_widget(event)
    await out.drain()
    rows = [e for e in store.get_events("alice") if e["type"].startswith("widget")]
    assert [r["type"] for r in rows] == [
        "widget",
        "widget_update",
        "widget_state",
        "widget_message",
        "widget_close",
    ]
    opened = rows[0]
    assert opened["widget_id"] == "w1" and opened["renderer"] == "kt.app"
    assert opened["surface"] == "canvas" and opened["interactive"] is True
    assert opened["spec"] == {"document_url": "a"} and opened["routing"] == "agent"
    assert rows[1]["patch"] == {"state": {"b": 2}}
    assert rows[2]["state"] == {"a": 1, "b": 2, "c": 3}
    assert rows[3]["origin"] == "app" and rows[3]["routed_to"] == "agent"
    assert all(r["widget_id"] == "w1" for r in rows)


async def test_recorded_rows_rebuild_the_registry(tmp_path):
    store, out = _sink(tmp_path)
    for event in EVENTS[:4]:
        await out.on_widget(event)
    await out.drain()
    registry = WidgetRegistry()
    assert registry.restore(store.get_events("alice")) == 1
    record = registry.get("w1")
    assert record.renderer == "kt.app" and record.title == "Planner"
    assert record.state == {"a": 1, "b": 2, "c": 3} and record.closed is False


async def test_nothing_is_recorded_without_activity_capture(tmp_path):
    store, out = _sink(tmp_path, capture_activity=False)
    for event in EVENTS:
        await out.on_widget(event)
    await out.drain()
    assert not [e for e in store.get_events("alice") if e["type"].startswith("widget")]


async def test_inline_ui_state_is_recorded_with_its_block_address(tmp_path):
    store, out = _sink(tmp_path)
    payload = {"ui_turn": 2, "ui_branch": 1, "block": "0a1b2c3d.0", "state": {"n": 4}}
    await out.on_widget(
        OutputEvent(type="inline_ui_state", id="2:1:0a1b2c3d.0", payload=payload)
    )
    await out.drain()
    (row,) = [e for e in store.get_events("alice") if e["type"] == "inline_ui_state"]
    assert {k: row[k] for k in payload} == payload
    store.close()
