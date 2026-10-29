"""Unit tests for :mod:`kohakuterrarium.builtins.kt_ui.terminal`.

A ``kt.ui`` widget drawn as terminal cards and prompts: the text form, one
action per control, choice / typed / flip inputs saved as widget state,
buttons and forms sent as widget messages, updates, other clients' state,
and close.
"""

import asyncio

from kohakuterrarium.builtins.kt_ui.terminal import TerminalUiRenderer
from kohakuterrarium.modules.output.event import UIReply
from kohakuterrarium.modules.output.router import OutputRouter
from kohakuterrarium.testing.output import OutputRecorder

SPEC = {
    "title": "Bill splitter",
    "state": {"total": 120, "people": 3, "tip": 10, "rounded": False},
    "computed": {"each": "round(total * (1 + tip / 100) / people, 2)"},
    "root": {
        "type": "stack",
        "children": [
            {"type": "number", "label": "Total", "bind": "total"},
            {
                "type": "select",
                "label": "Tip",
                "bind": "tip",
                "options": [
                    {"value": 0, "label": "none"},
                    {"value": 10, "label": "10"},
                    {"value": 20, "label": "20"},
                ],
            },
            {"type": "toggle", "label": "Round", "bind": "rounded"},
            {"type": "stat", "label": "Each pays", "value": "{{ fmt(each, 2) }}"},
            {"type": "button", "label": "One more", "set": {"people": "people + 1"}},
            {
                "type": "button",
                "label": "Send",
                "send": "result",
                "data": {"each": "{{ each }}"},
            },
        ],
    },
}


class WidgetOutput(OutputRecorder):
    """Default output that hands widget events to a terminal renderer."""

    def __init__(self):
        super().__init__()
        self.presented = []
        self.retracted = []
        self.renderer = TerminalUiRenderer(
            get_service=lambda: self._router.widgets,
            present=lambda event, rr: self.presented.append((event, rr)),
            retract=self.retracted.append,
            source=self,
        )

    def ui_renderers(self):
        return {"kt.ui": 1}

    async def on_widget(self, event):
        if self.renderer.handles(event):
            self.renderer.on_widget(event)


class Agent:
    def __init__(self):
        self.injected = []

    async def inject_event(self, event):
        self.injected.append(event)


async def _open(spec=SPEC, interactive=True):
    out = WidgetOutput()
    router = OutputRouter(out)
    agent = Agent()
    router.widgets.bind_agent(agent)
    handle = await router.widgets.open(
        "kt.ui",
        spec,
        state=dict(spec["state"]),
        title=spec["title"],
        interactive=interactive,
    )
    return out, router, agent, handle


def _card(out):
    event, rr = out.presented[-1]
    assert event.type == "card"
    return event, rr


def _choose(out, label):
    event, rr = _card(out)
    action = next(a for a in event.payload["actions"] if a["label"].startswith(label))
    assert rr.submit_reply(UIReply(event_id=event.id, action_id=action["id"]))
    return rr


class TestDraw:
    async def test_card_shows_text_form_and_one_action_per_control(self):
        out, *_ = await _open()
        event, _ = _card(out)
        assert event.interactive is True
        assert event.payload["title"] == "Bill splitter"
        assert "Each pays: 44.00" in event.payload["body"]
        labels = [a["label"] for a in event.payload["actions"]]
        assert labels == [
            "Total: 120 ✎",
            "Tip: 10 ✎",
            "Round: false ✎",
            "One more",
            "Send",
        ]

    async def test_display_only_widget_keeps_inputs_but_hides_sends(self):
        out, *_ = await _open(interactive=False)
        labels = [a["label"] for a in _card(out)[0].payload["actions"]]
        assert "Send" not in labels and "One more" in labels

    async def test_each_items_contribute_controls_with_their_scope(self):
        spec = {
            "title": "Pick",
            "state": {"chosen": ""},
            "root": {
                "type": "each",
                "items": "['a', 'b']",
                "as": "it",
                "child": {
                    "type": "button",
                    "label": "Take {{ it }}",
                    "set": {"chosen": "it"},
                },
            },
        }
        out, router, _, handle = await _open(spec)
        _choose(out, "Take b")
        assert handle.state["chosen"] == "b"


class TestInputs:
    async def test_choice_prompt_sets_the_option_value_and_saves_state(self):
        out, router, _, handle = await _open()
        rr = _choose(out, "Tip")
        prompt, prompt_rr = out.presented[-1]
        assert prompt.type == "selection" and prompt_rr is rr
        assert [o["label"] for o in prompt.payload["options"]] == ["none", "10", "20"]
        assert prompt.payload["default"] == "1"
        assert rr.submit_reply(
            UIReply(prompt.id, "submit", {"selected": "2"})
        ), "prompt reply accepted"
        await router.widgets.drain()
        assert handle.state["tip"] == 20
        assert "Each pays: 48.00" in _card(out)[0].payload["body"]

    async def test_rating_is_a_choice_of_stars_up_to_max(self):
        spec = {
            "title": "Rate",
            "state": {"score": 2},
            "root": {"type": "rating", "label": "Score", "bind": "score", "max": 4},
        }
        out, router, _, handle = await _open(spec)
        assert "Score: ★★☆☆ (2/4)" in _card(out)[0].payload["body"]
        rr = _choose(out, "Score")
        prompt, _ = out.presented[-1]
        assert [o["label"] for o in prompt.payload["options"]] == [
            "★",
            "★★",
            "★★★",
            "★★★★",
        ]
        assert prompt.payload["default"] == "1"
        assert rr.submit_reply(UIReply(prompt.id, "submit", {"selected": "3"}))
        await router.widgets.drain()
        assert handle.state["score"] == 4

    async def test_typed_number_parses_and_bad_text_changes_nothing(self):
        out, router, _, handle = await _open()
        rr = _choose(out, "Total")
        prompt = out.presented[-1][0]
        assert prompt.type == "ask_text" and prompt.payload["default"] == "120"
        rr.submit_reply(UIReply(prompt.id, "submit", {"text": "abc"}))
        assert handle.state["total"] == 120
        rr = _choose(out, "Total")
        prompt = out.presented[-1][0]
        rr.submit_reply(UIReply(prompt.id, "submit", {"text": "90"}))
        assert handle.state["total"] == 90 and isinstance(handle.state["total"], int)

    async def test_cancelled_prompt_redraws_without_change(self):
        out, _, _, handle = await _open()
        rr = _choose(out, "Total")
        prompt = out.presented[-1][0]
        rr.submit_reply(UIReply(prompt.id, "cancel", {}))
        assert handle.state["total"] == 120 and _card(out)

    async def test_toggle_flips_at_once(self):
        out, _, _, handle = await _open()
        _choose(out, "Round")
        assert handle.state["rounded"] is True
        _choose(out, "Round")
        assert handle.state["rounded"] is False


class TestActions:
    async def test_set_button_updates_state_and_computed(self):
        out, _, agent, handle = await _open()
        _choose(out, "One more")
        assert handle.state["people"] == 4
        assert "Each pays: 33.00" in _card(out)[0].payload["body"]
        assert agent.injected == []

    async def test_send_button_reaches_the_agent_as_widget_event(self):
        out, router, agent, handle = await _open()
        _choose(out, "Send")
        await router.widgets.drain()
        (event,) = agent.injected
        assert event.type == "widget_event"
        assert event.context["data"] == {"each": 44.0}
        assert event.context["origin"] == "renderer"

    async def test_form_sends_bound_values_and_data(self):
        spec = {
            "title": "Form",
            "state": {"name": "kt", "n": 2},
            "root": {
                "type": "form",
                "send": "go",
                "submit": "Go",
                "data": {"extra": "{{ n * 2 }}"},
                "children": [{"type": "text_input", "label": "Name", "bind": "name"}],
            },
        }
        out, router, agent, _ = await _open(spec)
        _choose(out, "Go")
        await router.widgets.drain()
        assert agent.injected[0].context["data"] == {"name": "kt", "extra": 4}

    async def test_dismissing_the_card_closes_the_menu_until_an_update(self):
        out, router, _, handle = await _open()
        event, rr = _card(out)
        assert rr.submit_reply(UIReply(event.id, "cancel"))
        await handle.update(state={"people": 5})
        assert _card(out)[0].payload["actions"], "an update reopens the menu"

    async def test_stale_and_unknown_replies_are_refused(self):
        out, *_ = await _open()
        event, rr = _card(out)
        assert rr.submit_reply_with_status(UIReply("nope", "c0")) == (
            False,
            "unknown",
        )
        _choose(out, "One more")
        assert rr.submit_reply(UIReply(event.id, "c0")) is False, "stale card"


class TestBackendEvents:
    async def test_update_replaces_spec_and_merges_state(self):
        out, router, _, handle = await _open()
        first, _ = _card(out)
        new_spec = {**SPEC, "title": "New"}
        await handle.update(spec=new_spec, state={"people": 6}, title="New")
        event, _ = _card(out)
        assert event.payload["title"] == "New"
        assert "Each pays: 22.00" in event.payload["body"]
        assert first.id in out.retracted, "an unanswered card is retracted on redraw"

    async def test_awaiting_only_updates_do_not_redraw_an_open_menu(self):
        out, router, _, handle = await _open()
        drawn = len(out.presented)
        wait = asyncio.create_task(handle.wait({"result"}, timeout_s=0.05))
        assert await wait is None
        assert len(out.presented) == drawn, "awaiting on/off adds no cards"
        event, rr = _card(out)
        rr.submit_reply(UIReply(event.id, "cancel"))
        wait = asyncio.create_task(handle.wait({"result"}, timeout_s=0.05))
        await asyncio.sleep(0)
        assert len(out.presented) == drawn + 1, "a new wait reopens a closed menu"
        await wait

    async def test_state_from_another_client_redraws(self):
        out, router, _, handle = await _open()
        router.widgets.submit_state(handle.id, {"people": 2}, source="other")
        await router.widgets.drain()
        assert "Each pays: 66.00" in _card(out)[0].payload["body"]

    async def test_close_draws_a_closed_card_without_actions(self):
        out, router, _, handle = await _open()
        await handle.close()
        event, rr = _card(out)
        assert event.payload["actions"] == [] and event.payload["footer"] == "closed"
        assert rr.submit_reply(UIReply(event.id, "c0")) is False
        assert handle.id not in out.renderer.widgets

    async def test_other_renderers_are_not_handled(self):
        out, router, *_ = await _open()
        out.renderers = None
        assert not out.renderer.handles(
            type(
                "E",
                (),
                {
                    "type": "widget",
                    "update_target": None,
                    "id": "x",
                    "payload": {"renderer": "kt.app"},
                },
            )()
        )
