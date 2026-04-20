"""SelectorOverlay — modal arrow-key selector/confirm for the rich CLI.

Lives inside the single RichCLIApp Application (one render loop, one tree).
Exposes two ConditionalContainer Floats that the app embeds in a
FloatContainer, plus a key-binding set that the app merges at the
Application level (gated by ``visible`` so composer keys yield while
the overlay is up).

Async API: ``show_select(title, options, current)`` returns the picked
value (or ``None`` on cancel); ``show_confirm(message)`` returns bool.
Both use an ``asyncio.Future`` set from within the key handlers.

Single-instance: the overlay supports one pending interaction at a time.
Callers serialize through the app's ``_handle_slash`` task.
"""

import asyncio
from typing import Any

from prompt_toolkit.application import Application
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Window
from prompt_toolkit.layout.containers import ConditionalContainer, Float
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Frame


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
            width=Dimension(min=40, max=80, preferred=64),
            height=Dimension(min=3),
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

    # ── Rendering ──

    def _render_select(self) -> StyleAndTextTuples:
        lines: StyleAndTextTuples = []
        title = self._title or "Select"
        lines.append(("class:selector.title", f"{title}\n\n"))
        if not self._options:
            lines.append(("class:selector.hint", "(no options)\n"))
        for i, opt in enumerate(self._options):
            label = str(opt.get("label") or opt.get("value", ""))
            model = str(opt.get("model", ""))
            provider = str(opt.get("provider", ""))
            context = str(opt.get("context", ""))
            extras: list[str] = []
            if model and model != label:
                extras.append(model)
            if provider:
                extras.append(f"({provider})")
            if context:
                extras.append(context)
            extra = "  ".join(extras)

            is_active = i == self._highlight
            is_current = bool(opt.get("selected"))
            pointer = "▶ " if is_active else "  "
            dot = " ●" if is_current else ""

            row_style = (
                "class:selector.row.highlight" if is_active else "class:selector.row"
            )
            lines.append((row_style, f"{pointer}{label}{dot}"))
            if extra:
                lines.append(
                    (
                        (
                            "class:selector.row.extra.highlight"
                            if is_active
                            else "class:selector.row.extra"
                        ),
                        f"  {extra}",
                    )
                )
            lines.append(("", "\n"))
        lines.append(
            (
                "class:selector.hint",
                "\n↑↓ navigate   enter select   esc cancel",
            )
        )
        return lines

    def _render_confirm(self) -> StyleAndTextTuples:
        message = self._message or "Confirm?"
        return [
            ("class:selector.title", "Confirm\n\n"),
            ("", f"{message}\n\n"),
            ("class:selector.hint", "y confirm   n/esc cancel"),
        ]

    # ── Key bindings ──

    def _build_key_bindings(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add("up")
        def _up(event) -> None:
            if self.is_select and self._options:
                self._highlight = (self._highlight - 1) % len(self._options)
                event.app.invalidate()

        @kb.add("down")
        def _down(event) -> None:
            if self.is_select and self._options:
                self._highlight = (self._highlight + 1) % len(self._options)
                event.app.invalidate()

        @kb.add("c-p")
        def _ctrl_p(event) -> None:
            if self.is_select and self._options:
                self._highlight = (self._highlight - 1) % len(self._options)
                event.app.invalidate()

        @kb.add("c-n")
        def _ctrl_n(event) -> None:
            if self.is_select and self._options:
                self._highlight = (self._highlight + 1) % len(self._options)
                event.app.invalidate()

        @kb.add("home")
        def _home(event) -> None:
            if self.is_select and self._options:
                self._highlight = 0
                event.app.invalidate()

        @kb.add("end")
        def _end(event) -> None:
            if self.is_select and self._options:
                self._highlight = len(self._options) - 1
                event.app.invalidate()

        @kb.add("enter")
        def _enter(event) -> None:
            if not self.is_select:
                return
            if not self._future or self._future.done():
                return
            if not self._options:
                self._future.set_result(None)
                return
            self._future.set_result(self._options[self._highlight].get("value"))

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

        @kb.add("y")
        def _y(event) -> None:
            if self.is_confirm and self._future and not self._future.done():
                self._future.set_result(True)

        @kb.add("Y")
        def _y_upper(event) -> None:
            if self.is_confirm and self._future and not self._future.done():
                self._future.set_result(True)

        @kb.add("n")
        def _n(event) -> None:
            if self.is_confirm and self._future and not self._future.done():
                self._future.set_result(False)

        @kb.add("N")
        def _n_upper(event) -> None:
            if self.is_confirm and self._future and not self._future.done():
                self._future.set_result(False)

        return kb
