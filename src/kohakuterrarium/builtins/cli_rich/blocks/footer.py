"""Footer block — single-line status displayed at the bottom of the live region.

Shows token usage, model, mode hints, compaction state, and (when the
composer is multiline) the cursor's line/column position.

Colors shift as the context window fills:
  < 60%  dim (default)
  60-79% yellow
  80-89% bright_yellow (stronger warning)
  >= 90% bold red (about-to-compact warning)
"""

from rich.text import Text

from kohakuterrarium.builtins.cli_rich.theme import COLOR_FOOTER


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _context_style(pct: int) -> str:
    """Pick a style for the ``ctx N%`` span based on fill ratio."""
    if pct >= 90:
        return "bold red"
    if pct >= 80:
        return "bright_yellow"
    if pct >= 60:
        return "yellow"
    return COLOR_FOOTER


class FooterBlock:
    """One-line status footer at the bottom of the live region."""

    def __init__(self):
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._max_context = 0
        self._model = ""
        self._compacting = False
        self._processing = False
        # Multiline cursor position — set by the app each frame; (0, 0)
        # means "not multiline" (single-line buffers don't need a pos).
        self._cursor_line: int = 0
        self._cursor_col: int = 0
        self._cursor_total_lines: int = 0

    def update_tokens(self, prompt: int, completion: int, max_ctx: int = 0) -> None:
        self._prompt_tokens = prompt
        self._completion_tokens = completion
        if max_ctx:
            self._max_context = max_ctx

    def update_model(self, model: str) -> None:
        self._model = model

    def set_compacting(self, value: bool) -> None:
        self._compacting = value

    def set_processing(self, value: bool) -> None:
        self._processing = value

    def update_cursor(self, line: int, col: int, total_lines: int) -> None:
        """Report the composer cursor's logical position.

        ``total_lines >= 2`` turns the indicator on (single-line buffers
        don't need it). Values are 1-indexed for display.
        """
        self._cursor_line = line
        self._cursor_col = col
        self._cursor_total_lines = total_lines

    def __rich__(self) -> Text:
        # We build a Text object span-by-span rather than joining strings
        # so the context-percentage span can carry its own warning color
        # independently of the rest of the footer.
        line = Text(style=COLOR_FOOTER)
        sep = "  ·  "
        first = True

        def _push_sep() -> None:
            nonlocal first
            if not first:
                line.append(sep)
            first = False

        # Context window: percentage (color-shifted), raw counts.
        if self._prompt_tokens or self._completion_tokens:
            if self._max_context > 0 and self._prompt_tokens > 0:
                pct = int(self._prompt_tokens / self._max_context * 100)
                _push_sep()
                line.append(
                    f"ctx {pct}%/{_fmt_tokens(self._max_context)}",
                    style=_context_style(pct),
                )
            tok = (
                f"{_fmt_tokens(self._prompt_tokens)}↑ "
                f"{_fmt_tokens(self._completion_tokens)}↓"
            )
            _push_sep()
            line.append(tok)

        if self._model:
            _push_sep()
            line.append(self._model)

        if self._compacting:
            _push_sep()
            line.append("⟳ compacting", style="bold yellow")

        # Multiline cursor-position indicator — only shown when the
        # composer spans more than one line so it doesn't clutter simple
        # single-line prompts.
        if self._cursor_total_lines >= 2:
            _push_sep()
            line.append(
                f"ln {self._cursor_line}/{self._cursor_total_lines} "
                f"col {self._cursor_col}",
                style="dim",
            )

        # Mode hints — always last so wrapping drops them first on narrow
        # terminals.
        _push_sep()
        if self._processing:
            line.append("esc=interrupt  ctrl+b=bg  ctrl+x=cancel-bg")
        else:
            line.append("/help  /exit  shift+enter=newline  ctrl+d=quit")

        if first:
            # No segments at all — shouldn't happen, but be defensive.
            return Text("ready", style=COLOR_FOOTER)
        return line
