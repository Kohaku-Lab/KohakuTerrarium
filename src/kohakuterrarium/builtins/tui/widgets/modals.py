from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option


class SelectionModal(ModalScreen[str | None]):
    """Collect one option through keyboard navigation."""

    DEFAULT_CSS = """
    SelectionModal {
        align: center middle;
    }
    #select-container {
        width: 60;
        max-height: 24;
        border: thick #0F52BA 60%;
        border-title-color: #0F52BA;
        border-title-align: left;
        background: $surface;
        padding: 1 2;
    }
    SelectionModal OptionList {
        height: auto;
        max-height: 18;
    }
    SelectionModal .hint {
        height: 1;
        color: $text-muted;
        text-align: center;
    }
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(
        self,
        title: str,
        options: list[dict],
        current: str = "",
    ):
        super().__init__()
        self._title = title
        self._options = options
        # Selection is driven by each option's selected flag; current is retained
        # for compatibility with command renderers using this modal contract.
        self._current = current

    def compose(self):
        items = []
        self._highlight_idx = 0
        for i, opt in enumerate(self._options):
            label = opt.get("label", opt.get("value", ""))
            extra = opt.get("provider", "")
            marker = " \u25cf" if opt.get("selected") else ""
            line = f"{label}  ({extra}){marker}" if extra else f"{label}{marker}"
            items.append(Option(line, id=opt.get("value", label)))
            if opt.get("selected"):
                self._highlight_idx = i
        yield Vertical(
            OptionList(*items, id="select-list"),
            Static("\u2191\u2193 navigate  Enter select  Esc cancel", classes="hint"),
            id="select-container",
        )

    def on_mount(self) -> None:
        container = self.query_one("#select-container", Vertical)
        container.border_title = self._title
        ol = self.query_one("#select-list", OptionList)
        ol.highlighted = self._highlight_idx

    def on_option_list_option_selected(self, event: OptionList.OptionSelected):
        self.dismiss(event.option.id)

    def action_cancel(self):
        self.dismiss(None)


class ConfirmModal(ModalScreen[bool]):
    """Collect a yes-or-no confirmation."""

    DEFAULT_CSS = """
    ConfirmModal {
        align: center middle;
    }
    #confirm-container {
        width: 50;
        height: auto;
        border: thick #D4920A 60%;
        border-title-color: #D4920A;
        border-title-align: left;
        background: $surface;
        padding: 1 2;
    }
    ConfirmModal .hint {
        height: 1;
        color: $text-muted;
        text-align: center;
        margin-top: 1;
    }
    """

    BINDINGS = [
        ("y", "confirm", "Yes"),
        ("n", "cancel", "No"),
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(self, message: str):
        super().__init__()
        self._message = message

    def compose(self):
        yield Vertical(
            Static(self._message),
            Static("y confirm  n/Esc cancel", classes="hint"),
            id="confirm-container",
        )

    def on_mount(self) -> None:
        self.query_one("#confirm-container", Vertical).border_title = "Confirm"

    def action_confirm(self):
        self.dismiss(True)

    def action_cancel(self):
        self.dismiss(False)
