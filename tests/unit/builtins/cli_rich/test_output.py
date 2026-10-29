"""Tests for :mod:`kohakuterrarium.builtins.cli_rich.output`."""

import asyncio

from kohakuterrarium.builtins.cli_rich.output import RichCLIOutput
from kohakuterrarium.modules.output.event import OutputEvent, UIReply
from kohakuterrarium.modules.output.router import OutputRouter
from kohakuterrarium.testing.output import OutputRecorder


class _RecordingOverlay:
    def __init__(self):
        self.superseded = []
        self.pending_event_ids = {"prompt-1"}

    def on_supersede(self, event_id):
        self.superseded.append(event_id)
        self.pending_event_ids.discard(event_id)


class _RecordingApp:
    def __init__(self):
        self.notifications = []
        self.errors = []
        self.bus_overlay = _RecordingOverlay()
        self.invalidations = 0
        self.cards = []
        self.progress = []

    def _invalidate(self):
        self.invalidations += 1

    def on_notification_event(self, payload: dict) -> None:
        self.notifications.append(payload)

    def on_processing_error(self, error_type: str, error: str) -> None:
        self.errors.append((error_type, error))

    def on_card_event(self, payload: dict) -> None:
        self.cards.append(payload)

    def on_progress_event(self, event_id, update_target, payload) -> None:
        self.progress.append((event_id, update_target, payload))


def test_supersede_closes_the_bus_overlay() -> None:
    app = _RecordingApp()
    output = RichCLIOutput(app)

    output.on_supersede("prompt-1")

    assert app.bus_overlay.superseded == ["prompt-1"]
    assert app.bus_overlay.pending_event_ids == set()
    assert app.invalidations == 1


def test_command_result_is_rendered_as_notification() -> None:
    app = _RecordingApp()
    output = RichCLIOutput(app)

    output.on_activity_with_metadata(
        "command_result",
        "Available commands",
        {"command": "/help", "source": "cli"},
    )

    assert app.notifications == [
        {"title": "help", "text": "Available commands", "level": "info"}
    ]


def test_command_error_is_rendered_as_processing_error() -> None:
    app = _RecordingApp()
    output = RichCLIOutput(app)

    output.on_activity_with_metadata(
        "command_error",
        "Unknown command",
        {"command": "/nope arg", "source": "cli"},
    )

    assert app.errors == [("nope", "Unknown command")]


async def test_router_delivers_legacy_kinds_through_on_widget_unchanged() -> None:
    app = _RecordingApp()
    output = RichCLIOutput(app)
    router = OutputRouter(output)

    await router.emit(OutputEvent(type="card", id="c1", payload={"title": "T"}))
    await router.emit(OutputEvent(type="progress", id="p1", payload={"label": "x"}))
    await router.emit(
        OutputEvent(type="progress", update_target="p1", payload={"value": 2})
    )

    assert app.cards == [{"title": "T"}]
    assert app.progress == [("p1", None, {"label": "x"}), (None, "p1", {"value": 2})]


async def test_reply_closes_the_overlay_like_on_supersede() -> None:
    app = _RecordingApp()
    output = RichCLIOutput(app)
    router = OutputRouter(output)
    event = OutputEvent(type="confirm", id="prompt-1", interactive=True)
    task = asyncio.create_task(router.emit_and_wait(event))
    await asyncio.sleep(0)
    assert router.submit_reply(UIReply(event_id="prompt-1", action_id="yes"))
    await task
    assert app.bus_overlay.superseded == ["prompt-1"]
    assert app.invalidations >= 1


UI_SPEC = {
    "title": "Count",
    "state": {"n": 1},
    "root": {
        "type": "stack",
        "children": [
            {"type": "stat", "label": "n", "value": "{{ n }}"},
            {"type": "button", "label": "Add", "set": {"n": "n + 1"}},
        ],
    },
}


class _OpeningOverlay(_RecordingOverlay):
    def __init__(self):
        super().__init__()
        self.opened = []

    def open(self, event, *, router=None):
        self.opened.append((event, router))


def test_reports_the_kt_ui_text_profile_only_with_an_overlay() -> None:
    assert RichCLIOutput(_RecordingApp()).ui_renderers() == {"kt.ui": 1}
    app = _RecordingApp()
    app.bus_overlay = None
    assert RichCLIOutput(app).ui_renderers() == {}


def test_replacing_the_default_output_exposes_kt_ui_to_the_router() -> None:
    router = OutputRouter(OutputRecorder())
    assert router.ui_renderers() == {}
    router.default_output = RichCLIOutput(_RecordingApp())
    assert router.ui_renderers() == {"kt.ui": 1}


async def test_kt_ui_widget_opens_the_overlay_and_a_reply_saves_state() -> None:
    app = _RecordingApp()
    app.bus_overlay = _OpeningOverlay()
    output = RichCLIOutput(app)
    router = OutputRouter(output)
    handle = await router.widgets.open(
        "kt.ui", UI_SPEC, state={"n": 1}, title="Count", interactive=False
    )
    card, reply_router = app.bus_overlay.opened[-1]
    assert card.type == "card" and "n: 1" in card.payload["body"]
    assert [a["label"] for a in card.payload["actions"]] == ["Add"]
    assert reply_router.submit_reply_with_status(
        UIReply(event_id=card.id, action_id="c0")
    ) == (True, "accepted")
    assert handle.state == {"n": 2}
    assert "n: 2" in app.bus_overlay.opened[-1][0].payload["body"]

    await handle.close()
    assert app.cards[-1]["footer"] == "closed", "a closed widget is a plain card"
