"""Modal screens for Phase B interactive OutputEvents.

Each modal returns a typed result dict that the calling code wraps
into a :class:`UIReply` and submits to the agent's output router.

Distinct from the legacy ``ConfirmModal`` / ``SelectionModal`` in
``modals.py`` (which serve specialised in-app flows like the model
picker). The bus modals here accept the full Phase B event payload
schemas and support multi-button confirms, multi-select selections,
multi-line ask_text inputs, etc.
"""

from typing import Any

from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    Input,
    Label,
    RadioButton,
    RadioSet,
    Static,
)

# Result is a dict so callers can reuse the same submit_reply path
# regardless of which modal produced the value.
ModalResult = dict[str, Any] | None


class BusConfirmModal(ModalScreen[ModalResult]):
    """N-button confirm modal. Returns ``{"action_id": "..."}`` or
    ``None`` on cancel/escape (which the caller treats as the
    ``"cancel"`` action).
    """

    DEFAULT_CSS = """
    BusConfirmModal {
        align: center middle;
    }
    #bus-confirm-container {
        width: 60;
        height: auto;
        max-width: 90;
        border: thick $warning 60%;
        border-title-color: $warning;
        border-title-align: left;
        background: $surface;
        padding: 1 2;
    }
    BusConfirmModal #bus-confirm-detail {
        margin-top: 1;
        color: $text-muted;
    }
    BusConfirmModal #bus-confirm-actions {
        height: auto;
        margin-top: 1;
        align: center middle;
    }
    BusConfirmModal Button {
        margin-left: 1;
        margin-right: 1;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, prompt: str, detail: str, options: list[dict]):
        super().__init__()
        self._prompt = prompt
        self._detail = detail
        self._options = options or []

    def compose(self):
        children = [Label(self._prompt, id="bus-confirm-prompt")]
        if self._detail:
            children.append(Static(self._detail, id="bus-confirm-detail"))
        button_row = []
        for opt in self._options:
            variant_map = {
                "primary": "primary",
                "secondary": "default",
                "danger": "error",
            }
            variant = variant_map.get(opt.get("style", "secondary"), "default")
            btn = Button(
                opt.get("label", opt.get("id", "?")),
                id=f"act-{opt.get('id', '')}",
                variant=variant,
            )
            button_row.append(btn)
        children.append(Horizontal(*button_row, id="bus-confirm-actions"))
        yield Vertical(*children, id="bus-confirm-container")

    def on_mount(self) -> None:
        try:
            self.query_one("#bus-confirm-container", Vertical).border_title = "Confirm"
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id.startswith("act-"):
            self.dismiss({"action_id": btn_id[len("act-") :]})

    def action_cancel(self) -> None:
        self.dismiss(None)


class BusAskTextModal(ModalScreen[ModalResult]):
    """Free-text input modal. Returns
    ``{"action_id": "submit", "values": {"text": "..."}}`` or
    ``None`` on cancel.
    """

    DEFAULT_CSS = """
    BusAskTextModal {
        align: center middle;
    }
    #bus-asktext-container {
        width: 70;
        height: auto;
        max-width: 100;
        border: thick $primary 60%;
        border-title-color: $primary;
        border-title-align: left;
        background: $surface;
        padding: 1 2;
    }
    BusAskTextModal Input {
        margin-top: 1;
    }
    BusAskTextModal #bus-asktext-actions {
        height: auto;
        margin-top: 1;
        align: center middle;
    }
    BusAskTextModal Button {
        margin-left: 1;
        margin-right: 1;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(
        self,
        prompt: str,
        placeholder: str = "",
        default: str = "",
        multiline: bool = False,
    ):
        super().__init__()
        self._prompt = prompt
        self._placeholder = placeholder
        self._default = default
        # multiline kept for future extension; v1 uses single-line Input
        self._multiline = multiline

    def compose(self):
        yield Vertical(
            Label(self._prompt, id="bus-asktext-prompt"),
            Input(
                value=self._default,
                placeholder=self._placeholder,
                id="bus-asktext-input",
            ),
            Horizontal(
                Button("Submit", id="act-submit", variant="primary"),
                Button("Cancel", id="act-cancel"),
                id="bus-asktext-actions",
            ),
            id="bus-asktext-container",
        )

    def on_mount(self) -> None:
        try:
            self.query_one("#bus-asktext-container", Vertical).border_title = "Input"
            self.query_one("#bus-asktext-input", Input).focus()
        except Exception:
            pass

    def _submit(self) -> None:
        try:
            text = self.query_one("#bus-asktext-input", Input).value
        except Exception:
            text = ""
        self.dismiss({"action_id": "submit", "values": {"text": text}})

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "act-submit":
            self._submit()
        else:
            self.action_cancel()

    def action_cancel(self) -> None:
        self.dismiss(None)


class BusSelectionModal(ModalScreen[ModalResult]):
    """Single- or multi-pick selection modal. Returns
    ``{"action_id": "submit", "values": {"selected": ...}}`` or
    ``None`` on cancel. ``selected`` is a single id (single-pick) or a
    list of ids (multi-pick).
    """

    DEFAULT_CSS = """
    BusSelectionModal {
        align: center middle;
    }
    #bus-selection-container {
        width: 70;
        max-height: 30;
        max-width: 100;
        border: thick $primary 60%;
        border-title-color: $primary;
        border-title-align: left;
        background: $surface;
        padding: 1 2;
    }
    BusSelectionModal #bus-selection-options {
        max-height: 18;
        overflow-y: auto;
    }
    BusSelectionModal #bus-selection-actions {
        height: auto;
        margin-top: 1;
        align: center middle;
    }
    BusSelectionModal Button {
        margin-left: 1;
        margin-right: 1;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(
        self,
        prompt: str,
        options: list[dict],
        multi: bool = False,
        default: Any = None,
    ):
        super().__init__()
        self._prompt = prompt
        self._options = options or []
        self._multi = multi
        self._default = default

    def compose(self):
        prompt = Label(self._prompt, id="bus-selection-prompt")
        if self._multi:
            checkbox_widgets = []
            defaults = set(self._default) if isinstance(self._default, list) else set()
            for opt in self._options:
                opt_id = str(opt.get("id", ""))
                label = opt.get("label", opt_id)
                desc = opt.get("description")
                disp = f"{label} — {desc}" if desc else label
                checkbox_widgets.append(
                    Checkbox(disp, value=(opt_id in defaults), id=f"opt-{opt_id}")
                )
            options_widget = Vertical(*checkbox_widgets, id="bus-selection-options")
        else:
            radio_buttons = []
            for opt in self._options:
                opt_id = str(opt.get("id", ""))
                label = opt.get("label", opt_id)
                desc = opt.get("description")
                disp = f"{label} — {desc}" if desc else label
                value = opt_id == self._default
                radio_buttons.append(RadioButton(disp, value=value, id=f"opt-{opt_id}"))
            options_widget = RadioSet(*radio_buttons, id="bus-selection-options")
        yield Vertical(
            prompt,
            options_widget,
            Horizontal(
                Button("Submit", id="act-submit", variant="primary"),
                Button("Cancel", id="act-cancel"),
                id="bus-selection-actions",
            ),
            id="bus-selection-container",
        )

    def on_mount(self) -> None:
        try:
            self.query_one("#bus-selection-container", Vertical).border_title = (
                "Selection"
            )
        except Exception:
            pass

    def _selected_ids(self) -> Any:
        if self._multi:
            chosen: list[str] = []
            for opt in self._options:
                opt_id = str(opt.get("id", ""))
                try:
                    cb = self.query_one(f"#opt-{opt_id}", Checkbox)
                    if cb.value:
                        chosen.append(opt_id)
                except Exception:
                    continue
            return chosen
        try:
            radio_set = self.query_one("#bus-selection-options", RadioSet)
            pressed = radio_set.pressed_button
            if pressed is not None and pressed.id and pressed.id.startswith("opt-"):
                return pressed.id[len("opt-") :]
        except Exception:
            pass
        return ""

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "act-submit":
            self.dismiss(
                {"action_id": "submit", "values": {"selected": self._selected_ids()}}
            )
        else:
            self.action_cancel()

    def action_cancel(self) -> None:
        self.dismiss(None)
