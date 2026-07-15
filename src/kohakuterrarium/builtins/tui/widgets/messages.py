from textual.widgets import Collapsible, Static


class UserMessage(Static):
    DEFAULT_CSS = """
    UserMessage {
        height: auto;
        margin: 1 0 0 0;
        padding: 0 1;
        border: round #5A4FCF;
        border-title-color: #5A4FCF;
        border-title-align: left;
    }
    """

    def __init__(self, text: str, **kwargs):
        super().__init__(text, **kwargs)
        self.border_title = "You"


class QueuedMessage(Static):
    """Display user input queued during active processing."""

    DEFAULT_CSS = """
    QueuedMessage {
        height: auto;
        margin: 1 0 0 0;
        padding: 0 1;
        border: dashed #D4920A 50%;
        border-title-color: #D4920A;
        border-title-align: left;
        color: $text-muted;
    }
    """

    def __init__(self, text: str, **kwargs):
        super().__init__(text, **kwargs)
        self.border_title = "Queued"
        self.message_text = text

    def promote(self) -> None:
        """Restyle the queued message as accepted user input."""
        self.border_title = "You"
        self.remove_class("-queued")
        self.styles.border = ("round", "#5A4FCF")
        self.styles.border_title_color = "#5A4FCF"
        self.styles.color = None


class SystemNotice(Static):
    """Display a command or system result outside the conversation."""

    DEFAULT_CSS = """
    SystemNotice {
        height: auto;
        margin: 0;
        padding: 0 1;
        color: $text-muted;
        border-left: thick #0F52BA 40%;
        border-title-color: #0F52BA;
        border-title-align: left;
    }
    SystemNotice.--error {
        color: #E74C3C;
        border-left: thick #E74C3C 40%;
        border-title-color: #E74C3C;
    }
    """

    def __init__(self, text: str, command: str = "", error: bool = False, **kwargs):
        super().__init__(text, **kwargs)
        self.border_title = f"/{command}" if command else "system"
        if error:
            self.add_class("--error")


class TriggerMessage(Collapsible):
    """Display a channel or trigger message in a collapsible block."""

    DEFAULT_CSS = """
    TriggerMessage {
        height: auto;
        margin: 1 0 0 0;
        padding: 0;
    }
    TriggerMessage > Contents {
        height: auto;
        max-height: 12;
        overflow-y: auto;
        padding: 0 1;
    }
    TriggerMessage > CollapsibleTitle {
        color: #D4920A;
        background: transparent;
    }
    TriggerMessage > CollapsibleTitle:hover {
        background: #D4920A 15%;
    }
    TriggerMessage > CollapsibleTitle:focus {
        background: #D4920A 15%;
    }
    .trigger-body {
        height: auto;
        color: $text-muted;
    }
    """

    BUTTON_OPEN = "[-]"
    BUTTON_CLOSED = "[+]"

    def __init__(self, label: str, content: str = "", **kwargs):
        preview = content[:80].replace("\n", " ") if content else ""
        title = f"\u25cf {label}"
        if preview:
            title += f"  {preview}"
        self._body = Static(content, classes="trigger-body")
        super().__init__(
            self._body,
            title=title,
            collapsed=bool(content),
            **kwargs,
        )


class StreamingText(Static):
    """Accumulate plain chunks until the session replaces them with Markdown."""

    DEFAULT_CSS = """
    StreamingText {
        height: auto;
        margin: 0;
        padding: 0;
    }
    """

    def __init__(self, **kwargs):
        super().__init__("", **kwargs)
        self._chunks: list[str] = []

    def append(self, chunk: str) -> None:
        self._chunks.append(chunk)
        self.update("".join(self._chunks))

    def get_text(self) -> str:
        return "".join(self._chunks)
