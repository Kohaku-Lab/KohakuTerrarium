"""Slash command hint bar — inline one-line list of matching commands.

Renders below the composer whenever the buffer text starts with ``/``.
Shows up to 8 matching commands with their one-line descriptions. The
full dropdown with argument completions is still driven by
``SlashCommandCompleter`` via prompt_toolkit's standard completion
menu — this hint bar is the *always-visible* hint that tells users
which commands exist at all, without making them type a character
and hope the dropdown pops.

Mirrors the pattern used in the Textual TUI at
``builtins/tui/widgets/input.py`` (CommandHint message).
"""

from io import StringIO
from typing import Any

from rich.console import Console
from rich.text import Text

MAX_VISIBLE = 8


class SlashHintBar:
    """Render a one-line ANSI list of matching slash commands."""

    def __init__(self) -> None:
        self._registry: dict = {}
        self._console = Console(
            file=StringIO(),
            force_terminal=True,
            color_system="truecolor",
            legacy_windows=False,
            soft_wrap=True,
            emoji=False,
            width=120,
        )

    def set_registry(self, registry: dict) -> None:
        self._registry = registry

    def is_active(self, buffer_text: str) -> bool:
        """Return whether input is still in the slash-command name phase."""
        if not buffer_text.startswith("/"):
            return False
        return " " not in buffer_text

    def _matches(self, prefix: str) -> list[tuple[str, str]]:
        """Return (name, description) pairs matching *prefix*.

        Ranking: exact match first, then prefix matches (alphabetical),
        then substring matches (alphabetical). Each kind capped at
        ``MAX_VISIBLE`` total.
        """
        exact: list[tuple[str, str]] = []
        prefixed: list[tuple[str, str]] = []
        substring: list[tuple[str, str]] = []

        for name, cmd in self._registry.items():
            desc = getattr(cmd, "description", "") or ""
            if name == prefix:
                exact.append((name, desc))
            elif prefix and name.startswith(prefix):
                prefixed.append((name, desc))
            elif prefix and prefix in name:
                substring.append((name, desc))
            elif not prefix:
                prefixed.append((name, desc))

        prefixed.sort(key=lambda item: item[0])
        substring.sort(key=lambda item: item[0])
        combined = exact + prefixed + substring
        return combined[:MAX_VISIBLE]

    def render(self, buffer_text: str, width: int) -> str:
        """Return the hint bar as an ANSI-colored string, or empty."""
        if not self.is_active(buffer_text):
            return ""

        prefix = buffer_text[1:].lower()
        matches = self._matches(prefix)
        if not matches:
            return ""

        text = Text()
        for i, (name, desc) in enumerate(matches):
            if i:
                text.append("  ·  ", style="bright_black")
            # Highlight the matching fragment that caused this result.
            if prefix and name.startswith(prefix):
                text.append(f"/{name[:len(prefix)]}", style="bold bright_cyan")
                text.append(name[len(prefix) :], style="cyan")
            elif prefix and prefix in name:
                idx = name.find(prefix)
                text.append(f"/{name[:idx]}", style="cyan")
                text.append(name[idx : idx + len(prefix)], style="bold bright_cyan")
                text.append(name[idx + len(prefix) :], style="cyan")
            else:
                text.append(f"/{name}", style="cyan")

        # The layout reserves one line, so overflow must clip rather than wrap.
        self._console.width = max(20, width)
        self._console.file = StringIO()
        self._console.print(text, overflow="ellipsis", no_wrap=True, end="")
        return self._console.file.getvalue()

    def render_selected_description(self, buffer_text: str) -> str:
        """Return the top matching command's description."""
        if not self.is_active(buffer_text):
            return ""
        matches = self._matches(buffer_text[1:].lower())
        if not matches:
            return ""
        _, desc = matches[0]
        return desc

    def list_registered(self) -> list[str]:
        return sorted(self._registry.keys())


def _render_text_to_ansi(text: Any, width: int, console: Console | None = None) -> str:
    """Render a Rich ``Text`` / renderable to ANSI with given width."""
    buf = StringIO()
    c = console or Console(
        file=buf,
        force_terminal=True,
        color_system="truecolor",
        width=max(20, width),
        legacy_windows=False,
        soft_wrap=False,
        emoji=False,
    )
    c.file = buf
    c.print(text, end="")
    return buf.getvalue()
