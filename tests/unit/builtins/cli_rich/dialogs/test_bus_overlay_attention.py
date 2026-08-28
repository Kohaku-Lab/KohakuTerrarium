from kohakuterrarium.builtins.cli_rich.dialogs.bus_overlay import BusInteractiveOverlay
from kohakuterrarium.modules.output.event import OutputEvent


class Router:
    def submit_reply_with_status(self, reply):
        return True, "accepted"


class BoolRouter:
    def submit_reply_with_status(self, reply):
        return True


class RejectingRouter:
    def submit_reply_with_status(self, reply):
        return False, "unknown"


def event():
    return OutputEvent(
        type="confirm",
        id="approve",
        interactive=True,
        payload={"options": [{"id": "allow", "label": "Allow"}]},
    )


def test_overlay_deduplicates_pending_events_and_clears_accepted_reply():
    overlay = BusInteractiveOverlay(lambda: Router())

    overlay.open(event())
    overlay.open(event())
    assert overlay.pending_event_ids == {"approve"}
    assert len(overlay._queue) == 0

    overlay.handle_key("enter")
    assert overlay.pending_event_ids == set()


def test_overlay_accepts_bool_status_routers():
    overlay = BusInteractiveOverlay(lambda: BoolRouter())
    overlay.open(event())

    overlay.handle_key("enter")

    assert overlay.pending_event_ids == set()


def test_overlay_keeps_rejected_reply_visible_and_pending():
    overlay = BusInteractiveOverlay(lambda: RejectingRouter())
    overlay.open(event())

    overlay.handle_key("enter")

    assert overlay.visible
    assert overlay.pending_event_ids == {"approve"}


def test_overlay_clears_superseded_pending_event():
    overlay = BusInteractiveOverlay(lambda: Router())
    overlay.open(OutputEvent(type="ask_text", id="ask", interactive=True))

    overlay.on_supersede("ask")

    assert overlay.pending_event_ids == set()
    assert not overlay.visible
