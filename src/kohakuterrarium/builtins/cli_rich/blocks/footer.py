"""Footer block — single-line status displayed at the bottom of the live region.

Shows token usage (cumulative), model, mode hints, compaction state,
and (when the composer is multiline) the cursor's line/column position.

Token accounting matches the TUI + web frontends:
  ↑ = accumulated INPUT tokens across every LLM call in this session
  ↓ = accumulated OUTPUT tokens
  ctx% = last-call prompt tokens / max_context (not cumulative — ctx%
          represents how full the current context window is, NOT the
          total over the session)

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
        # Cumulative counters — accumulated across every LLM call in
        # this session. Match TUI / web frontend semantics.
        self._input_tokens = 0
        self._output_tokens = 0
        self._cached_tokens = 0
        # Last call's prompt size — used for the context-fill % reading
        # (the current context window is what the NEXT call will see,
        # not the session total).
        self._last_prompt_tokens = 0
        self._max_context = 0
        self._model = ""
        self._compacting = False
        self._processing = False
        # Multiline cursor position — set by the app each frame; (0, 0)
        # means "not multiline" (single-line buffers don't need a pos).
        self._cursor_line: int = 0
        self._cursor_col: int = 0
        self._cursor_total_lines: int = 0

    def update_tokens(
        self,
        prompt: int = 0,
        completion: int = 0,
        max_ctx: int = 0,
        cached: int = 0,
    ) -> None:
        """Accumulate the latest LLM call's token deltas.

        Called once per LLM call with that call's prompt / completion /
        cached counts. Each call's prompt is ALSO saved as
        ``_last_prompt_tokens`` for the context-fill % readout.
        """
        self._input_tokens += max(0, prompt)
        self._output_tokens += max(0, completion)
        self._cached_tokens += max(0, cached)
        if prompt > 0:
            self._last_prompt_tokens = prompt
        if max_ctx:
            self._max_context = max_ctx

    def restore_tokens(
        self,
        input_total: int = 0,
        output_total: int = 0,
        cached_total: int = 0,
        last_prompt: int = 0,
    ) -> None:
        """Set cumulative totals from session history (on resume)."""
        self._input_tokens = input_total
        self._output_tokens = output_total
        self._cached_tokens = cached_total
        self._last_prompt_tokens = last_prompt

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

        # Context window fill (based on LAST prompt — reflects the
        # current window, not session-cumulative counts).
        if self._max_context > 0 and self._last_prompt_tokens > 0:
            pct = int(self._last_prompt_tokens / self._max_context * 100)
            _push_sep()
            line.append(
                f"ctx {pct}%/{_fmt_tokens(self._max_context)}",
                style=_context_style(pct),
            )

        # Cumulative in/out tokens for the session. Include cache count
        # inline when present — matches TUI: ``1.2k↑ (cache 800) 3.4k↓``.
        if self._input_tokens or self._output_tokens:
            tok = Text()
            tok.append(f"{_fmt_tokens(self._input_tokens)}↑")
            if self._cached_tokens > 0:
                tok.append(f" (cache {_fmt_tokens(self._cached_tokens)})")
            tok.append(f" {_fmt_tokens(self._output_tokens)}↓")
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
