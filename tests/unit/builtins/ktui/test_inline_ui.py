"""Inline ``<ui>`` blocks in a streamed reply: split, held back, drawn, and every component
either mapped to a widget or explicitly drawn as its text fallback."""

import json
import threading
import time
from pathlib import Path

import pytest
from kohakutui import App
from kohakutui.ui import Cell, Composer, Stack, Text

from kohakuterrarium.builtins.kt_ui.components import COMPONENTS
from kohakuterrarium.builtins.kt_ui.inline_state import block_ids
from kohakuterrarium.builtins.plugins.ui_declarative.reference import EXAMPLES
from kohakuterrarium.builtins.ktui.inline_ui import ReplyStream
from kohakuterrarium.builtins.ktui.ui_block import UiContext, UiHost
from kohakuterrarium.builtins.ktui.ui_slot import Slot
from kohakuterrarium.builtins.ktui.ui_map import FALLBACK_TYPES, HANDLERS

REPLY = """Here is the plan.

<ui title="Plan cost">
<state seats={8} plan="team"/>
<let price={plan == "team" ? 29 : 49}/>
<stat label="Monthly" hint="billed monthly">${fmt(seats * price)}</stat>
<steps items={[{title:"Pick a plan", status:"done"}, {title:"Invite", status:"current"}, "Ship"]}/>
<table columns={["plan", {key:"price", label:"Price", format:"currency"}]} rows={[["team", 29], ["business", 49]]}/>
</ui>

<callout tone="warning" title="Note">Prices exclude **tax**.</callout>

That's all."""


@pytest.fixture
def app():
    app = App(backend="test", size=(70, 40), interrupt="event").start()
    yield app
    app.stop()


def _reply(app: App) -> ReplyStream:
    cell = app.transcript.add(Cell(role="assistant", stream_flush=True))
    return ReplyStream(app, cell)


def _played(text: str, chunk: int) -> list[str]:
    app = App(backend="test", size=(70, 40), interrupt="event").start()
    try:
        reply = _reply(app)
        for i in range(0, len(text), chunk):
            reply.feed(text[i : i + chunk])
        reply.close()
        reply.cell.seal()
        app.sync()
        return app.history()
    finally:
        app.stop()


def _live(app: App) -> str:
    app.sync()
    return "\n".join(app.screen())


class TestReplyStream:
    def test_a_reply_draws_the_same_however_it_is_chunked(self):
        whole = _played(REPLY, len(REPLY))
        assert whole == _played(REPLY, 1) == _played(REPLY, 3)
        assert whole[:16] == [
            "● Here is the plan.",
            "",
            "  Plan cost",
            "  Monthly  $232",
            "  billed monthly",
            "",
            "  ✓ Pick a plan",
            "  ● Invite",
            "  ○ Ship",
            "",
            "  plan     │ Price",
            "  ─────────┼───────",
            "  team     │ $29.00",
            "  business │ $49.00",
            "",
            "  ⚠ Note",
        ], whole
        assert whole[16:19] == ["    Prices exclude tax.", "", "  That's all."], whole

    def test_a_line_that_may_open_a_block_is_held_until_decided(self, app):
        reply = _reply(app)
        reply.feed("Intro line\n<cal")
        screen = _live(app)
        assert "Intro line" in screen and "<cal" not in screen, screen
        # not a component after all: the held line is text
        reply.feed("ifornia> is not a tag\n")
        assert "<california> is not a tag" in _live(app)

    def test_a_streaming_block_shows_what_arrived_and_a_broken_one_says_why(self, app):
        reply = _reply(app)
        reply.feed('<ui>\n<stat label="Users">42</stat>\n<kv fields={[{label: "a"')
        screen = _live(app)
        assert "Users  42" in screen and "building UI" in screen, screen
        reply.feed(", value: 1}]}/>\n<nope/>\n</ui>")
        reply.close()
        screen = _live(app)
        assert "UI block could not be shown" in screen, screen
        assert "building UI" not in screen

    def test_ui_markup_in_a_code_fence_stays_text(self, app):
        reply = _reply(app)
        reply.feed('```html\n<ui>\n<stat label="x">1</stat>\n</ui>\n```\n')
        reply.close()
        assert '<stat label="x">1</stat>' in _live(app)


def test_every_component_is_mapped_or_explicitly_a_text_fallback():
    unknown = set(COMPONENTS) - set(HANDLERS) - FALLBACK_TYPES
    assert not unknown, f"decide how ktui draws: {sorted(unknown)}"
    assert not set(HANDLERS) & FALLBACK_TYPES


FORM = """Pick a size.

<ui title="Plan">
<state seats={8}/>
<let total={seats * 29}/>
<slider label="Seats" bind="seats" min={1} max={50}/>
<stat label="Monthly">${fmt(total)}</stat>
<button send="Go with {seats} seats">Choose</button>
</ui>"""


class _States:
    """InlineUiStates' get / submit, recording what was saved."""

    def __init__(self, stored=None):
        self.stored = stored or {}
        self.saved = []

    def get(self, turn, branch, block):
        return self.stored.get((turn, branch, block))

    def submit(self, turn, branch, block, state):
        self.saved.append(((turn, branch, block), dict(state)))
        return "accepted"


def _until(predicate, what, budget=5.0):
    deadline = time.monotonic() + budget
    while not predicate():
        assert time.monotonic() < deadline, what
        time.sleep(0.02)


class TestInteractiveBlocks:
    def test_slots_update_nodes_in_place_and_drop_what_is_gone(self, app):
        box = app.transcript.add(Stack())
        first = Slot(box, None)
        kept = first.add(Text("a")).node
        gone = first.add(Text("b")).node
        first.finish()
        second = Slot(box, None, first.drawn)
        assert second.add(Text("A")).node.id == kept.id, "same place and kind: updated"
        second.finish()
        app.sync()
        screen = "\n".join(app.screen())
        assert "A" in screen and "b" not in screen, screen
        assert gone.id not in {d.node.id for d in second.drawn}

    def test_a_form_takes_input_sends_saves_and_freezes(self, app):
        host, sent, states = UiHost(), [], _States()
        ctx = UiContext(host, sent.append, lambda: True, lambda: (3, 1), states)
        threading.Thread(
            target=lambda: [host.handle(ev) for ev in app.events()], daemon=True
        ).start()
        composer = app.bottom.add(Composer())
        composer.focus()
        reply = ReplyStream(app, app.transcript.add(Cell(role="assistant")), ctx)
        reply.feed(FORM)
        reply.close()
        assert reply.interactive and reply.turn == (3, 1)
        assert "tab to use · ctrl+t full screen" in _live(app)

        app.inject({"key": "tab"}, {"key": "right"}, {"key": "right"})
        _until(
            lambda: "Monthly  $290" in _live(app), "the let value follows the slider"
        )
        block = next(v for v in reply.views if hasattr(v, "state"))
        block._save_now()  # the debounced save, without waiting for it
        assert states.saved[-1][0][:2] == (3, 1) and states.saved[-1][1] == {
            "seats": 10
        }

        app.inject({"key": "down"}, {"key": "enter"})
        _until(lambda: sent == ["Go with 10 seats"], "the button sends its message")

        reply.freeze()
        screen = _live(app)
        assert "tab to use" not in screen and "Seats" in screen, screen
        app.inject({"key": "tab"}, {"key": "right"})
        time.sleep(0.2)
        assert "Monthly  $290" in _live(app), "frozen: input no longer changes it"
        assert not reply.interactive

    def test_saved_state_is_restored_for_the_replys_own_turn(self, app):
        ((block_id, _),) = block_ids(FORM)
        states = _States({(2, 1, block_id): {"seats": 20, "unknown": 1}})
        ctx = UiContext(
            UiHost(), lambda text: None, lambda: True, lambda: (9, 9), states
        )
        reply = ReplyStream(
            app, app.transcript.add(Cell(role="assistant")), ctx, (2, 1)
        )
        reply.feed(FORM)
        reply.close()
        assert "Monthly  $580" in _live(
            app
        ), "restored from turn 2, not the agent's turn 9"


FIXTURES = (
    Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "kt_ui_markup.json"
)
# every component the reference examples do not cover, in one block each
CATALOGUE = [
    '<badge tone="success">live</badge>',
    '<button send="go {n}">Go</button>',
    '<callout tone="warning" title="Careful">Read **this**.</callout>',
    '<card title="Card" subtitle="sub"><text>body</text></card>',
    '<code lang="py">print(1)</code>',
    "<diagram>graph LR\n  A --> B</diagram>",
    "<divider/>",
    '<each items="[1, 2]" as="x"><text>item {x}</text></each>',
    '<form message="sent {n}"><number label="N" bind="n"/></form>',
    "<grid columns={2}><text>a</text><text>b</text></grid>",
    "<heading level={1}>Title</heading>",
    '<kv fields={[{label: "a", value: 1}]}/>',
    '<list items={["x", "y"]} ordered/>',
    '<number label="N" bind="n" min={0} max={9}/>',
    '<radio label="R" bind="pick" options={["a", "b"]}/>',
    '<select label="S" bind="pick" options={[{label: "A", value: "a"}]}/>',
    '<slider label="S" bind="n" min={0} max={10} step={0.5}/>',
    '<stack direction="row"><badge>a</badge><badge>b</badge></stack>',
    '<stat label="Users" hint="today" trend={[1, 3, 2]}>{n}</stat>',
    '<steps items={["a", "b"]} current={1}/>',
    '<tabs><tab label="One"><text>1</text></tab><tab label="Two"><text>2</text></tab></tabs>',
    '<table columns={[{key: "p", label: "Price", format: "currency"}]} rows={[{p: 3}]}/>',
    "<text>plain **text** {n}</text>",
    '<text_input label="Name" bind="name" placeholder="you"/>',
    '<toggle label="On" bind="on"/>',
    '<checkbox label="Ok" bind="on"/>',
] + [
    f'<chart kind="{kind}" title="{kind}" labels={{["a", "b", "c"]}} '
    f'series={{[{{name: "s", data: [1, 3, 2]}}, {{name: "t", data: [2, 1, 3]}}]}}/>'
    for kind in (
        "bar",
        "stacked",
        "line",
        "area",
        "pie",
        "scatter",
        "radar",
        "heatmap",
        "gauge",
    )
]
STATE = '<state n={4} pick="a" name="" on={false}/>'


def _sources() -> list[tuple[str, str, bool]]:
    fixtures = json.loads(FIXTURES.read_text(encoding="utf-8"))
    cases = [
        (f"catalogue {i}", f"<ui>\n{STATE}\n{body}\n</ui>", True)
        for i, body in enumerate(CATALOGUE)
    ]
    cases += [
        (f"example {name}", f"<ui>\n{STATE}\n{body}\n</ui>", True)
        for name, body in EXAMPLES.items()
    ]
    cases += [(c["name"], c["source"], "error" not in c) for c in fixtures["convert"]]
    cases += [(c["name"], c["text"], True) for c in fixtures["split"]]
    return cases


def test_every_component_and_fixture_draws_live_streamed_and_whole():
    app = App(backend="test", size=(70, 400), interrupt="event").start()
    try:
        ctx = UiContext(UiHost(), lambda text: None, lambda: True, lambda: (1, 1))
        for name, source, valid in _sources():
            for chunk in (5, len(source)):
                cell = app.transcript.add(Cell(role="assistant"))
                reply = ReplyStream(app, cell, ctx)
                for i in range(0, len(source), chunk):
                    reply.feed(source[i : i + chunk])
                reply.close()
                app.sync()
                screen = "\n".join(app.screen())
                failed = "could not be shown" in screen
                assert failed == (not valid), f"{name}: {screen[-600:]}"
                if name.startswith(("catalogue", "example")):
                    assert (
                        "</" not in screen and "/>" not in screen
                    ), f"{name}: {screen}"
                reply.freeze()
                cell.remove()
    finally:
        app.stop()
