"""SelectorOverlay — modal arrow-key selector/confirm for the rich CLI.

Lives inside the single RichCLIApp Application (one render loop, one tree).
Exposes two ConditionalContainer Floats that the app embeds in a
FloatContainer, plus a key-binding set that the app merges at the
Application level (gated by ``visible`` so composer keys yield while
the overlay is up).

Async API: ``show_select(title, options, current)`` returns the picked
value (or ``None`` on cancel); ``show_confirm(message)`` returns bool.
Both use an ``asyncio.Future`` set from within the key handlers.

Select mode supports live type-to-filter: any printable key appends
to the query, Backspace removes, Ctrl+U clears. Filtering is a
case-insensitive substring match across label / value / model /
provider — highlight clamps to the filtered view so Enter always
picks a visible row.

Single-instance: the overlay supports one pending interaction at a
time. Callers serialize through the app's ``_handle_slash`` task.
"""

import asyncio
from typing import Any

from prompt_toolkit.application import Application
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import HSplit, Window
from prompt_toolkit.layout.containers import ConditionalContainer, Float
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Frame

# Column widths (characters). Tuned for typical model lists — long
# labels/models get ellipsized via ``_fit``; the overall Float still
# sizes up to max= in its Dimension if the terminal is wide enough.
_COL_LABEL = 16
_COL_MODEL = 26
_COL_PROVIDER = 12
_COL_CONTEXT = 6


# Pointer-led highlight: calm in the terminal, no full-width reverse bar.
SELECTOR_STYLES: dict[str, str] = {
    "selector.frame": "#5B8DEF",
    "selector.frame.label": "#5B8DEF bold",
    "selector.frame.confirm": "#D4920A",
    "selector.frame.confirm.label": "#D4920A bold",
    "selector.title": "#ffffff bold",
    "selector.count": "#666666",
    "selector.search.label": "#888888",
    "selector.search.arrow": "#E85B9F",
    "selector.search.query": "#ffffff bold",
    "selector.search.cursor": "#E85B9F",
    "selector.search.placeholder": "#555555 italic",
    "selector.pointer": "#3a3a3a",
    "selector.pointer.highlight": "#E85B9F bold",
    "selector.row.label": "#b8b8b8",
    "selector.row.label.highlight": "#ffffff bold",
    "selector.row.extra": "#6a6a6a",
    "selector.row.extra.highlight": "#a8a8a8",
    "selector.row.current": "#D4AF37 bold",
    "selector.empty": "#777777 italic",
    "selector.hint": "#666666",
}


def _fit(text: str, width: int, align: str = "left") -> str:
    """Pad or truncate ``text`` to exactly ``width`` chars.

    Truncation replaces the tail with an ellipsis so the row stays
    aligned with its neighbours.
    """
    if len(text) > width:
        if width <= 1:
            return text[:width]
        return text[: width - 1] + "…"
    return text.ljust(width) if align == "left" else text.rjust(width)


class SelectorOverlay:
    """Shared select/confirm overlay for RichCLIApp.

    Holds its own mode + highlight state and a single pending future.
    Two ``ConditionalContainer`` floats (select and confirm) are
    rendered only while the matching mode is active. Key bindings are
    centrally registered so the app can merge them with a
    ``selector.visible`` filter.
    """

    SELECT = "select"
    CONFIRM = "confirm"

    def __init__(self) -> None:
        self._mode: str | None = None
        self._title: str = ""
        self._message: str = ""
        self._options: list[dict[str, Any]] = []
        self._query: str = ""
        self._filtered: list[dict[str, Any]] = []
        self._highlight: int = 0
        self._future: asyncio.Future[Any] | None = None
        self._saved_focus: Any | None = None

        self._select_control = FormattedTextControl(
            text=self._render_select,
            focusable=True,
            show_cursor=False,
        )
        self._select_window = Window(
            content=self._select_control,
            width=Dimension(min=52, max=96, preferred=76),
            height=Dimension(min=5),
            dont_extend_height=True,
            wrap_lines=False,
            always_hide_cursor=True,
        )
        self._select_frame = Frame(
            self._select_window,
            title="Select",
            style="class:selector.frame",
        )

        self._confirm_control = FormattedTextControl(
            text=self._render_confirm,
            focusable=True,
            show_cursor=False,
        )
        self._confirm_window = Window(
            content=self._confirm_control,
            width=Dimension(min=30, max=70, preferred=50),
            height=Dimension(min=3),
            dont_extend_height=True,
            wrap_lines=True,
            always_hide_cursor=True,
        )
        self._confirm_frame = Frame(
            self._confirm_window,
            title="Confirm",
            style="class:selector.frame.confirm",
        )

        self._key_bindings = self._build_key_bindings()

    # ── Public state ──

    @property
    def visible(self) -> bool:
        """True while an overlay is open and awaiting user input."""
        return (
            self._mode is not None
            and self._future is not None
            and not self._future.done()
        )

    @property
    def is_select(self) -> bool:
        return self.visible and self._mode == self.SELECT

    @property
    def is_confirm(self) -> bool:
        return self.visible and self._mode == self.CONFIRM

    @property
    def key_bindings(self) -> KeyBindings:
        """Global key bindings to merge into the Application's kb stack.

        The caller must gate these with a ``visible`` filter so they
        only fire while an overlay is active.
        """
        return self._key_bindings

    # ── Layout integration ──

    def build_floats(self) -> list[Float]:
        """Return the Floats to attach to the app's FloatContainer."""
        select_float = Float(
            content=ConditionalContainer(
                content=HSplit([self._select_frame]),
                filter=Condition(lambda: self.is_select),
            ),
        )
        confirm_float = Float(
            content=ConditionalContainer(
                content=HSplit([self._confirm_frame]),
                filter=Condition(lambda: self.is_confirm),
            ),
        )
        return [select_float, confirm_float]

    # ── Async entry points ──

    async def show_select(
        self,
        title: str,
        options: list[dict[str, Any]],
        current: str,
        app: Application | None,
    ) -> str | None:
        """Open the select overlay; resolves to picked value or None."""
        if not options:
            return None
        if self.visible:
            # Refuse concurrent overlays; caller serializes.
            return None

        self._mode = self.SELECT
        self._title = title or "Select"
        self._options = list(options)
        self._query = ""
        self._filtered = list(options)
        self._highlight = self._initial_highlight(options, current)
        self._future = asyncio.get_event_loop().create_future()

        self._claim_focus(app)
        try:
            return await self._future
        finally:
            self._release_focus(app)
            self._reset()

    async def show_confirm(
        self,
        message: str,
        app: Application | None,
    ) -> bool:
        """Open the confirm overlay; resolves to True (y) or False (n/Esc)."""
        if self.visible:
            return False

        self._mode = self.CONFIRM
        self._message = message
        self._future = asyncio.get_event_loop().create_future()

        self._claim_focus(app)
        try:
            result = await self._future
            return bool(result)
        finally:
            self._release_focus(app)
            self._reset()

    # ── Internal state ──

    @staticmethod
    def _initial_highlight(options: list[dict[str, Any]], current: str) -> int:
        """Preselect the option marked ``selected`` or matching ``current``."""
        for i, opt in enumerate(options):
            if opt.get("selected"):
                return i
        if current:
            for i, opt in enumerate(options):
                if opt.get("value") == current or opt.get("model") == current:
                    return i
        return 0

    def _reset(self) -> None:
        self._mode = None
        self._title = ""
        self._message = ""
        self._options = []
        self._query = ""
        self._filtered = []
        self._highlight = 0
        self._future = None

    def _claim_focus(self, app: Application | None) -> None:
        if app is None:
            return
        try:
            self._saved_focus = app.layout.current_window
        except Exception:
            self._saved_focus = None
        try:
            target = (
                self._select_window
                if self._mode == self.SELECT
                else self._confirm_window
            )
            app.layout.focus(target)
        except Exception:
            pass
        app.invalidate()

    def _release_focus(self, app: Application | None) -> None:
        if app is None:
            return
        try:
            if self._saved_focus is not None:
                app.layout.focus(self._saved_focus)
        except Exception:
            pass
        self._saved_focus = None
        app.invalidate()

    # ── Filtering ──

    @staticmethod
    def _option_matches(opt: dict[str, Any], query: str) -> bool:
        """Case-insensitive substring match across the visible fields."""
        if not query:
            return True
        q = query.lower()
        for key in ("label", "value", "model", "provider"):
            val = opt.get(key)
            if val and q in str(val).lower():
                return True
        return False

    def _apply_filter(self) -> None:
        """Rebuild ``_filtered`` from ``_options`` + ``_query`` and clamp."""
        self._filtered = [
            opt for opt in self._options if self._option_matches(opt, self._query)
        ]
        if not self._filtered:
            self._highlight = 0
            return
        if self._highlight >= len(self._filtered):
            self._highlight = len(self._filtered) - 1
        if self._highlight < 0:
            self._highlight = 0

    # ── Rendering ──

    def _render_select(self) -> StyleAndTextTuples:
        lines: StyleAndTextTuples = []
        self._render_title(lines)
        self._render_search(lines)
        self._render_options(lines)
        self._render_hint(lines)
        return lines

    def _render_title(self, lines: StyleAndTextTuples) -> None:
        title = self._title or "Select"
        lines.append(("class:selector.title", f"  {title}"))
        total = len(self._options)
        visible = len(self._filtered)
        if self._query and visible < total:
            lines.append(("class:selector.count", f"   {visible}/{total}"))
        lines.append(("", "\n\n"))

    def _render_search(self, lines: StyleAndTextTuples) -> None:
        lines.append(("class:selector.search.label", "  search "))
        lines.append(("class:selector.search.arrow", "› "))
        if self._query:
            lines.append(("class:selector.search.query", self._query))
            lines.append(("class:selector.search.cursor", "▏"))
        else:
            lines.append(("class:selector.search.placeholder", "type to filter…"))
        lines.append(("", "\n\n"))

    def _render_options(self, lines: StyleAndTextTuples) -> None:
        if not self._filtered:
            lines.append(("class:selector.empty", "  no matches\n"))
            return
        for i, opt in enumerate(self._filtered):
            self._render_row(lines, opt, is_active=(i == self._highlight))

    def _render_row(
        self,
        lines: StyleAndTextTuples,
        opt: dict[str, Any],
        is_active: bool,
    ) -> None:
        is_current = bool(opt.get("selected"))
        label = str(opt.get("label") or opt.get("value", ""))
        model = str(opt.get("model", "") or "")
        provider = str(opt.get("provider", "") or "")
        context = str(opt.get("context", "") or "")

        pointer_style = (
            "class:selector.pointer.highlight"
            if is_active
            else "class:selector.pointer"
        )
        label_style = (
            "class:selector.row.label.highlight"
            if is_active
            else "class:selector.row.label"
        )
        extra_style = (
            "class:selector.row.extra.highlight"
            if is_active
            else "class:selector.row.extra"
        )

        pointer = "▸" if is_active else " "
        lines.append(("", "  "))
        lines.append((pointer_style, pointer))
        lines.append(("", " "))
        lines.append((label_style, _fit(label, _COL_LABEL)))

        if model and model != label:
            lines.append((extra_style, "  " + _fit(model, _COL_MODEL)))
        else:
            lines.append(("", "  " + " " * _COL_MODEL))

        if provider:
            lines.append((extra_style, "  " + _fit(f"({provider})", _COL_PROVIDER)))
        else:
            lines.append(("", "  " + " " * _COL_PROVIDER))

        if context:
            lines.append((extra_style, "  " + _fit(context, _COL_CONTEXT, "right")))
        else:
            lines.append(("", "  " + " " * _COL_CONTEXT))

        if is_current:
            lines.append(("class:selector.row.current", " ●"))
        else:
            lines.append(("", "  "))
        lines.append(("", "\n"))

    def _render_hint(self, lines: StyleAndTextTuples) -> None:
        lines.append(("", "\n"))
        lines.append(
            (
                "class:selector.hint",
                "  ↑↓ navigate   enter select   esc cancel",
            )
        )
        if self._query:
            lines.append(("class:selector.hint", "   ^U clear   ⌫ backspace"))

    def _render_confirm(self) -> StyleAndTextTuples:
        message = self._message or "Confirm?"
        return [
            ("class:selector.title", "  Confirm\n\n"),
            ("", f"  {message}\n\n"),
            ("class:selector.hint", "  y confirm   n/esc cancel"),
        ]

    # ── Key bindings ──

    def _build_key_bindings(self) -> KeyBindings:
        kb = KeyBindings()
        in_select = Condition(lambda: self.is_select)
        in_confirm = Condition(lambda: self.is_confirm)

        @kb.add("up", filter=in_select)
        @kb.add("c-p", filter=in_select)
        def _up(event) -> None:
            if self._filtered:
                self._highlight = (self._highlight - 1) % len(self._filtered)
                event.app.invalidate()

        @kb.add("down", filter=in_select)
        @kb.add("c-n", filter=in_select)
        def _down(event) -> None:
            if self._filtered:
                self._highlight = (self._highlight + 1) % len(self._filtered)
                event.app.invalidate()

        @kb.add("home", filter=in_select)
        def _home(event) -> None:
            if self._filtered:
                self._highlight = 0
                event.app.invalidate()

        @kb.add("end", filter=in_select)
        def _end(event) -> None:
            if self._filtered:
                self._highlight = len(self._filtered) - 1
                event.app.invalidate()

        @kb.add("enter", filter=in_select)
        def _enter(event) -> None:
            if not self._future or self._future.done():
                return
            if not self._filtered:
                # Nothing to pick — treat as cancel to avoid a stuck overlay.
                self._future.set_result(None)
                return
            self._future.set_result(self._filtered[self._highlight].get("value"))

        @kb.add("backspace", filter=in_select)
        def _backspace(event) -> None:
            if not self._query:
                return
            self._query = self._query[:-1]
            self._apply_filter()
            event.app.invalidate()

        @kb.add("c-u", filter=in_select)
        def _clear_query(event) -> None:
            if not self._query:
                return
            self._query = ""
            self._apply_filter()
            event.app.invalidate()

        @kb.add("escape", eager=True)
        def _esc(event) -> None:
            if not self.visible or not self._future or self._future.done():
                return
            self._future.set_result(None if self.is_select else False)

        @kb.add("c-c")
        def _ctrl_c(event) -> None:
            if not self.visible or not self._future or self._future.done():
                return
            self._future.set_result(None if self.is_select else False)

        # Confirm-mode y/n. Gated on ``is_confirm`` so typing 'y' / 'n' in
        # select mode falls through to the Keys.Any handler below and
        # becomes part of the search query.
        @kb.add("y", filter=in_confirm)
        @kb.add("Y", filter=in_confirm)
        def _y(event) -> None:
            if self._future and not self._future.done():
                self._future.set_result(True)

        @kb.add("n", filter=in_confirm)
        @kb.add("N", filter=in_confirm)
        def _n(event) -> None:
            if self._future and not self._future.done():
                self._future.set_result(False)

        # Catch-all for typing into the search box. ``event.data`` is the
        # raw character(s) the terminal emitted; ignore control chars /
        # escape sequences / empty data.
        @kb.add(Keys.Any, filter=in_select)
        def _any(event) -> None:
            data = event.data or ""
            if not data or len(data) != 1:
                return
            if not data.isprintable() or data in ("\r", "\n", "\t"):
                return
            self._query += data
            self._apply_filter()
            event.app.invalidate()

        return kb
