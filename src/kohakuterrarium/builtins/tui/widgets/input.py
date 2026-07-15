from typing import ClassVar

from textual.events import Key
from textual.message import Message
from textual.widgets import TextArea

class ChatInput(TextArea):
    """Collect multiline chat input with terminal-portable shortcuts."""

    DEFAULT_CSS = """
    ChatInput {
        height: auto;
        min-height: 3;
        max-height: 8;
        border: solid #5A4FCF 30%;
    }
    ChatInput:focus {
        border: solid #5A4FCF;
    }
    """

    # The owning TUI session refreshes this list when runtime commands change.
    command_names: ClassVar[list[str]] = []

    class Submitted(Message):
        """Carry submitted chat text."""

        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    class EditQueued(Message):
        """Request editing of the latest queued message."""

        pass

    class CommandHint(Message):
        """Carry slash-command completion hints."""

        def __init__(self, hint: str) -> None:
            super().__init__()
            self.hint = hint

    def on_text_area_changed(self) -> None:
        """Emit command hints while slash commands are typed."""
        text = self.text.strip()
        if text.startswith("/") and self.command_names:
            partial = text.lstrip("/").split()[0].lower() if text.strip("/") else ""
            matches = [n for n in self.command_names if n.startswith(partial)]
            if matches and partial:
                hint = "  ".join(f"/{m}" for m in matches[:6])
                self.post_message(self.CommandHint(hint))
            elif not partial:
                hint = "  ".join(f"/{n}" for n in self.command_names[:8])
                self.post_message(self.CommandHint(hint))
            else:
                self.post_message(self.CommandHint(""))
        else:
            self.post_message(self.CommandHint(""))

    def _on_key(self, event: Key) -> None:
        # Ctrl+J remains distinguishable from submit across limited terminals and SSH.
        if event.key in ("shift+enter", "ctrl+enter", "ctrl+j"):
            event.prevent_default()
            event.stop()
            self.insert("\n")
            return
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            text = self.text.strip()
            if text:
                self.post_message(self.Submitted(text))
                self.clear()
            return
        if event.key == "up" and not self.text.strip():
            event.prevent_default()
            event.stop()
            self.post_message(self.EditQueued())
            return
        super()._on_key(event)
