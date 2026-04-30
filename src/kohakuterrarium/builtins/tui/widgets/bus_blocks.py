"""Inline TUI widgets for Phase B display events: card and progress.

Both mount in the chat scroll like the existing tool/sub-agent blocks
and update in place when an OutputEvent with ``update_target`` lands.
``CardBlock`` with non-empty ``actions`` becomes interactive: the
buttons submit a :class:`UIReply` to the agent's output_router via
the ``router`` argument passed on construction.
"""

import webbrowser
from typing import Any, Callable

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Markdown, ProgressBar, Static

_ACCENT_COLOR_MAP = {
    "primary": "#5A4FCF",  # iolite
    "info": "#0F52BA",  # sapphire
    "success": "#4C9989",  # aquamarine
    "warning": "#D4920A",  # amber
    "danger": "#E74C3C",  # coral
    "neutral": "#888888",
}


class CardBlock(Vertical):
    """Inline card widget — Phase B ``card`` event renderer.

    When ``payload.actions`` is non-empty AND ``on_action`` is wired,
    actions render as real Textual Button widgets that submit a reply.
    A ``link``-styled action opens its ``url`` in the default browser
    instead of submitting a reply (no agent round-trip).

    Display-only mode (no actions, or no callback) shows the card with
    no interactive controls.
    """

    DEFAULT_CSS = """
    CardBlock {
        height: auto;
        margin: 1 0;
        padding: 1 2;
        border: round #5A4FCF 60%;
        background: $surface;
    }
    CardBlock .card-header {
        height: auto;
        text-style: bold;
        color: $text;
    }
    CardBlock .card-subtitle {
        height: auto;
        color: $text-muted;
        margin-bottom: 1;
    }
    CardBlock .card-fields {
        height: auto;
        margin-top: 1;
        margin-bottom: 1;
    }
    CardBlock .card-footer {
        height: auto;
        color: $text-muted;
        margin-top: 1;
    }
    CardBlock .card-actions {
        height: auto;
        margin-top: 1;
    }
    CardBlock .card-resolved {
        height: 1;
        color: $text-muted;
        margin-top: 1;
    }
    CardBlock Button {
        margin-right: 1;
    }
    """

    def __init__(
        self,
        payload: dict[str, Any],
        event_id: str | None = None,
        on_action: Callable[[str, str], None] | None = None,
    ):
        super().__init__()
        self._payload = payload
        self._event_id = event_id
        # Callable invoked with (event_id, action_id) when the user
        # clicks a non-link action. The TUI's output module wires this
        # to ``router.submit_reply``.
        self._on_action = on_action
        self._resolved: bool = False
        self._resolved_action: str = ""

    def compose(self) -> ComposeResult:
        title = self._payload.get("title", "")
        subtitle = self._payload.get("subtitle", "")
        icon = self._payload.get("icon", "")
        body = self._payload.get("body")
        fields = self._payload.get("fields") or []
        footer = self._payload.get("footer")
        actions = self._payload.get("actions") or []

        header = f"{icon} {title}".strip() if icon else title
        if header:
            yield Static(header, classes="card-header")
        if subtitle:
            yield Static(subtitle, classes="card-subtitle")
        if body:
            yield Markdown(body)
        if fields:
            field_rows = []
            for f in fields:
                label = f.get("label", "")
                value = f.get("value", "")
                field_rows.append(Static(f"[bold]{label}:[/bold] {value}"))
            yield Vertical(*field_rows, classes="card-fields")
        interactive = bool(actions) and self._on_action is not None
        if interactive:
            buttons = []
            for a in actions:
                label = a.get("label", a.get("id", "?"))
                style = a.get("style", "secondary")
                variant_map = {
                    "primary": "primary",
                    "secondary": "default",
                    "danger": "error",
                    "link": "default",
                }
                variant = variant_map.get(style, "default")
                buttons.append(
                    Button(
                        label,
                        id=f"act-{a.get('id', '')}",
                        variant=variant,
                    )
                )
            yield Horizontal(*buttons, classes="card-actions")
            yield Static("", classes="card-resolved", id="card-resolved-line")
        elif actions:
            # Display-only chips for non-interactive cards.
            chips = []
            for a in actions:
                label = a.get("label", a.get("id", "?"))
                style = a.get("style", "secondary")
                colour_map = {
                    "primary": "#5A4FCF",
                    "secondary": "#888888",
                    "danger": "#E74C3C",
                    "link": "#0F52BA",
                }
                colour = colour_map.get(style, "#888888")
                chips.append(
                    Static(
                        f"[on {colour}] {label} [/]",
                        classes="card-action",
                    )
                )
            yield Horizontal(*chips, classes="card-actions")
        if footer:
            yield Static(footer, classes="card-footer")

    def on_mount(self) -> None:
        accent = self._payload.get("accent", "neutral")
        colour = _ACCENT_COLOR_MAP.get(accent, _ACCENT_COLOR_MAP["neutral"])
        try:
            self.styles.border = ("round", colour)
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if self._resolved or self._on_action is None:
            return
        btn_id = event.button.id or ""
        if not btn_id.startswith("act-"):
            return
        action_id = btn_id[len("act-") :]
        # Find the action definition.
        actions = self._payload.get("actions") or []
        action = next((a for a in actions if a.get("id") == action_id), None)
        if action is None:
            return
        # Link actions open the URL and don't submit a reply.
        if action.get("style") == "link":
            url = action.get("url", "")
            if url:
                try:
                    webbrowser.open(url)
                except Exception:
                    pass
            return
        # Submit the reply. Mark resolved so further clicks don't
        # double-submit; dim the buttons.
        try:
            self._on_action(self._event_id or "", action_id)
        finally:
            self._mark_resolved(action_id)

    def _mark_resolved(self, action_id: str) -> None:
        self._resolved = True
        self._resolved_action = action_id
        try:
            for btn in self.query(Button):
                btn.disabled = True
            line = self.query_one("#card-resolved-line", Static)
            line.update(f"[#888888]→ {action_id}[/]")
        except Exception:
            pass


class ProgressBlock(Vertical):
    """Inline progress widget — Phase B ``progress`` event renderer.

    Mounts a Textual ``ProgressBar`` plus a label. Updates mutate
    in place via ``update_progress``.
    """

    DEFAULT_CSS = """
    ProgressBlock {
        height: 3;
        margin: 1 0;
        padding: 0 2;
    }
    ProgressBlock .progress-label {
        height: 1;
        color: $text-muted;
    }
    ProgressBlock ProgressBar {
        height: 1;
    }
    """

    def __init__(
        self,
        widget_id: str,
        label: str,
        value: int | float | None = None,
        max_value: int | float | None = None,
        indeterminate: bool = False,
    ):
        super().__init__(id=f"progress-{widget_id}")
        self._label_text = label
        self._value = value
        self._max = max_value
        self._indeterminate = indeterminate

    def compose(self) -> ComposeResult:
        yield Static(self._label_text, classes="progress-label")
        # Textual ProgressBar accepts total=None for indeterminate.
        total = None if self._indeterminate or self._max is None else float(self._max)
        bar = ProgressBar(total=total, show_eta=False)
        yield bar

    def update_progress(
        self,
        label: str | None,
        value: int | float | None,
        max_value: int | float | None,
        indeterminate: bool,
        complete: bool,
    ) -> None:
        try:
            if label is not None:
                self.query_one(".progress-label", Static).update(label)
                self._label_text = label
            bar = self.query_one(ProgressBar)
            if indeterminate:
                bar.update(total=None)
            elif max_value is not None:
                bar.update(total=float(max_value))
            if value is not None:
                bar.update(progress=float(value))
            if complete:
                # Visually mark done. For determinate bars, fill to total.
                if max_value is not None:
                    bar.update(progress=float(max_value))
                self.query_one(".progress-label", Static).update(
                    f"[#4C9989]✓[/] {self._label_text}"
                )
        except Exception:
            pass
