"""Tests for Rich CLI output event formatting."""

from rich.text import Text

from kohakuterrarium.builtins.cli_rich.app_output import AppOutputMixin

HOSTILE_TEXT = "[/red] [done] [/etc/passwd] [] [b]x"


class _Committer:
    def __init__(self) -> None:
        self.markup: list[str] = []
        self.renderables: list[object] = []

    def text(self, markup: str) -> None:
        self.markup.append(markup)

    def commit(self, renderable: object) -> None:
        self.renderables.append(renderable)


class _OutputHost(AppOutputMixin):
    def __init__(self) -> None:
        self.committer = _Committer()

    def _flush_assistant_message(self) -> None:
        pass

    def _invalidate(self) -> None:
        pass


def test_dynamic_notice_fragments_are_escaped_without_losing_fixed_styles() -> None:
    host = _OutputHost()

    host.on_processing_error(HOSTILE_TEXT, HOSTILE_TEXT)
    host.on_background_result(HOSTILE_TEXT, HOSTILE_TEXT)
    host.on_progress_event(None, None, {"label": HOSTILE_TEXT})
    host.on_notification_event({"title": HOSTILE_TEXT, "text": HOSTILE_TEXT})

    parsed = [Text.from_markup(markup) for markup in host.committer.markup]
    assert all(HOSTILE_TEXT in text.plain for text in parsed)
    assert any(span.style == "red" for span in parsed[0].spans)
    assert any(span.style == "cyan" for span in parsed[1].spans)
    assert any(span.style == "cyan" for span in parsed[2].spans)
    assert any(span.style == "cyan" for span in parsed[3].spans)


def test_dynamic_panel_fragments_are_literal_with_fixed_styles() -> None:
    host = _OutputHost()

    host.on_ui_event_panel(
        "selection",
        {
            "prompt": HOSTILE_TEXT,
            "options": [{"label": HOSTILE_TEXT, "description": HOSTILE_TEXT}],
        },
    )
    host.on_card_event(
        {
            "title": HOSTILE_TEXT,
            "subtitle": HOSTILE_TEXT,
            "body": HOSTILE_TEXT,
            "fields": [{"label": HOSTILE_TEXT, "value": HOSTILE_TEXT}],
            "actions": [{"style": "danger", "label": HOSTILE_TEXT}],
            "footer": HOSTILE_TEXT,
        }
    )

    selection_panel, card_panel = host.committer.renderables
    selection_body = Text.from_markup(selection_panel.renderable)
    card_title = Text.from_markup(card_panel.title)
    card_body = Text.from_markup(card_panel.renderable)
    assert selection_body.plain.count(HOSTILE_TEXT) == 3
    assert HOSTILE_TEXT in card_title.plain
    assert card_body.plain.count(HOSTILE_TEXT) == 5
    assert any(span.style == "bold" for span in card_body.spans)
    assert any(span.style == "red" for span in card_body.spans)
