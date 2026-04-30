"""Bus interactive overlay — Phase B confirm/ask_text/selection in CLI.

Modal-style overlay rendered inside the live region (same pattern as
``ModelPicker``). Captures keyboard input, shows the prompt + options,
and submits a :class:`UIReply` to the agent's output_router when the
user picks / submits / cancels.

Single class handles all three interactive event types — branching on
the OutputEvent's ``type`` field. A small queue lets multiple events
stack: when one is dismissed, the next one (if any) opens
automatically.
"""

from io import StringIO
from typing import Any

from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.text import Text

from kohakuterrarium.modules.output.event import OutputEvent, UIReply
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class BusInteractiveOverlay:
    """Modal overlay for Phase B interactive OutputEvents in the CLI.

    The RichCLIApp owns one instance. ``open(event)`` pushes an event
    onto the queue and makes the overlay ``visible`` if it wasn't
    already. ``handle_key(name)`` consumes input while visible.
    Submission calls ``router.submit_reply(...)``.
    """

    def __init__(
        self,
        get_router: Any,
        get_textarea_text: Any = None,
        clear_textarea: Any = None,
    ) -> None:
        # ``get_router`` returns the OutputRouter; may not be wired at
        # app init time. ``get_textarea_text`` / ``clear_textarea`` are
        # accessors for the composer's textarea — used by ``ask_text``
        # so the user types into the existing input field instead of a
        # separate buffer (avoids fighting prompt_toolkit's key binding
        # priority).
        self._get_router = get_router
        self._get_textarea_text = get_textarea_text
        self._clear_textarea = clear_textarea
        self.visible: bool = False
        self._queue: list[OutputEvent] = []
        self._current: OutputEvent | None = None

        # Per-event ephemeral state.
        self._option_index: int = 0  # for confirm + selection
        self._multi_selected: set[str] = set()  # for selection multi=True

    # ── Lifecycle ───────────────────────────────────────────────

    def open(self, event: OutputEvent) -> None:
        """Queue an event; open if nothing is currently displayed."""
        self._queue.append(event)
        if not self.visible:
            self._activate_next()

    def close(self, *, dismiss_current: bool = True) -> None:
        """Close the overlay. If ``dismiss_current`` and an event is
        active, treat it as a cancel reply.
        """
        if dismiss_current and self._current is not None:
            self._submit("cancel", values={})
        self._current = None
        self.visible = False
        self._reset_state()
        # If more events are queued, open the next one.
        if self._queue:
            self._activate_next()

    def _activate_next(self) -> None:
        if not self._queue:
            self.visible = False
            return
        self._current = self._queue.pop(0)
        self._reset_state()
        # Pre-populate option index from default if any.
        payload = self._current.payload or {}
        default_id = payload.get("default")
        options = self._options()
        if default_id and options:
            for i, opt in enumerate(options):
                if opt.get("id") == default_id:
                    self._option_index = i
                    break
        if self._current.type == "ask_text" and self._clear_textarea:
            # Pre-fill the textarea with the default value if any.
            default_text = str(payload.get("default", "") or "")
            try:
                self._clear_textarea(default_text)
            except Exception:
                pass
        if self._current.type == "selection" and bool(payload.get("multi", False)):
            if isinstance(default_id, list):
                self._multi_selected = {str(x) for x in default_id}
        self.visible = True

    def _reset_state(self) -> None:
        self._option_index = 0
        self._multi_selected = set()

    def _options(self) -> list[dict[str, Any]]:
        """Return the choosable options for the active event.

        ``confirm`` and ``selection`` use ``payload.options``; ``card``
        uses ``payload.actions``. We treat them uniformly so the same
        navigation/submit machinery handles all three. ``link``-styled
        card actions are filtered out — they open URLs externally and
        don't submit a reply.
        """
        if self._current is None:
            return []
        payload = self._current.payload or {}
        if self._current.type == "card":
            actions = payload.get("actions") or []
            return [a for a in actions if a.get("style") != "link"]
        return list(payload.get("options") or [])

    # ── Keyboard ────────────────────────────────────────────────

    def handle_key(self, key: str) -> bool:
        """Consume a named key event. Returns ``True`` if consumed."""
        if not self.visible or self._current is None:
            return False
        et = self._current.type
        if et == "confirm":
            return self._handle_confirm_key(key)
        if et == "ask_text":
            return self._handle_ask_text_key(key)
        if et == "selection":
            return self._handle_selection_key(key)
        if et == "card":
            # Cards-with-actions reuse the confirm key flow: arrow keys
            # move the cursor, digits jump to an option, Enter submits,
            # Esc cancels.
            return self._handle_confirm_key(key)
        return False

    def handle_text(self, char: str) -> bool:
        """No-op — the overlay reuses the composer's textarea for text
        input rather than maintaining a separate buffer. Kept for API
        compatibility with the composer's text-handler pipeline.
        """
        return False

    def captures_input(self) -> bool:
        """The overlay never captures printable characters directly:
        ``ask_text`` reads from the composer's textarea on Enter,
        ``confirm`` and ``selection`` only consume named keys.
        """
        return False

    # ── Per-type key handlers ───────────────────────────────────

    def _handle_confirm_key(self, key: str) -> bool:
        options = self._options()
        if not options:
            return False
        if key in ("left", "up"):
            self._option_index = (self._option_index - 1) % len(options)
            return True
        if key in ("right", "down", "tab"):
            self._option_index = (self._option_index + 1) % len(options)
            return True
        if key == "enter":
            opt = options[self._option_index]
            self._submit(opt.get("id", "cancel"), values={})
            self.close(dismiss_current=False)
            return True
        if key == "escape":
            self.close()
            return True
        # Number-key shortcut: 1, 2, …
        if key.isdigit():
            idx = int(key) - 1
            if 0 <= idx < len(options):
                self._submit(options[idx].get("id", "cancel"), values={})
                self.close(dismiss_current=False)
                return True
        return False

    def _handle_ask_text_key(self, key: str) -> bool:
        if key == "enter":
            # Read the user's answer from the composer's textarea so
            # we don't have to fight prompt_toolkit's key-binding
            # priority. The app's textarea is the user's natural
            # focus point; we just intercept Enter to mean "submit
            # this answer to the awaiting tool."
            text = ""
            if self._get_textarea_text:
                try:
                    text = self._get_textarea_text() or ""
                except Exception:
                    text = ""
            if not text.strip():
                # Empty submit — let the user keep typing.
                return True
            if self._clear_textarea:
                try:
                    self._clear_textarea("")
                except Exception:
                    pass
            self._submit("submit", values={"text": text})
            self.close(dismiss_current=False)
            return True
        if key == "escape":
            if self._clear_textarea:
                try:
                    self._clear_textarea("")
                except Exception:
                    pass
            self.close()
            return True
        # All other named keys (arrows, ctrl-*) — let composer/app
        # handle for normal textarea editing while we're displaying.
        return False

    def _handle_selection_key(self, key: str) -> bool:
        options = self._options()
        if not options:
            return False
        multi = bool((self._current.payload or {}).get("multi", False))
        if key in ("up", "k"):
            self._option_index = (self._option_index - 1) % len(options)
            return True
        if key in ("down", "j"):
            self._option_index = (self._option_index + 1) % len(options)
            return True
        if key == "space" and multi:
            opt_id = str(options[self._option_index].get("id", ""))
            if opt_id in self._multi_selected:
                self._multi_selected.discard(opt_id)
            else:
                self._multi_selected.add(opt_id)
            return True
        if key == "enter":
            if multi:
                # Preserve original option order in the result.
                selected = [
                    str(o.get("id", ""))
                    for o in options
                    if str(o.get("id", "")) in self._multi_selected
                ]
                self._submit("submit", values={"selected": selected})
            else:
                opt = options[self._option_index]
                self._submit("submit", values={"selected": str(opt.get("id", ""))})
            self.close(dismiss_current=False)
            return True
        if key == "escape":
            self.close()
            return True
        return False

    # ── Submission ──────────────────────────────────────────────

    def _submit(self, action_id: str, values: dict[str, Any]) -> None:
        if self._current is None:
            return
        router = self._get_router()
        if router is None:
            return
        try:
            router.submit_reply(
                UIReply(
                    event_id=self._current.id or "",
                    action_id=action_id,
                    values=values,
                )
            )
        except Exception as e:
            logger.debug("BusInteractiveOverlay submit failed", error=str(e))

    # ── Rendering ───────────────────────────────────────────────

    def render(self, width: int) -> str:
        if not self.visible or self._current is None:
            return ""
        et = self._current.type
        panel: RenderableType
        if et == "confirm":
            panel = self._render_confirm()
        elif et == "ask_text":
            panel = self._render_ask_text()
        elif et == "selection":
            panel = self._render_selection()
        elif et == "card":
            panel = self._render_card()
        else:
            return ""
        buf = StringIO()
        console = Console(
            file=buf,
            force_terminal=True,
            color_system="truecolor",
            width=max(40, width),
            legacy_windows=False,
            soft_wrap=False,
            emoji=False,
        )
        console.print(panel, end="")
        return buf.getvalue().rstrip("\n")

    # ── Per-type renderers ──────────────────────────────────────

    def _render_confirm(self) -> RenderableType:
        payload = self._current.payload or {}
        prompt = payload.get("prompt", "")
        detail = payload.get("detail", "")
        options = self._options()
        rows: list[RenderableType] = []
        if prompt:
            rows.append(Text(prompt, style="bold"))
        if detail:
            rows.append(Text(detail, style="dim"))
        rows.append(Text(""))
        for i, opt in enumerate(options):
            label = opt.get("label", opt.get("id", "?"))
            style_map = {
                "primary": "bright_cyan",
                "secondary": "white",
                "danger": "bright_red",
            }
            base = style_map.get(opt.get("style", "secondary"), "white")
            line = Text()
            if i == self._option_index:
                line.append("  › ", style="bold bright_cyan")
            else:
                line.append(f"  {i + 1}  ", style="dim")
            line.append(
                label, style=f"bold {base}" if i == self._option_index else base
            )
            rows.append(line)
        rows.append(Text(""))
        hint = Text()
        hint.append("←→/Tab", style="cyan")
        hint.append(" navigate  ", style="dim")
        hint.append("1-9", style="cyan")
        hint.append(" pick  ", style="dim")
        hint.append("Enter", style="cyan")
        hint.append(" confirm  ", style="dim")
        hint.append("Esc", style="cyan")
        hint.append(" cancel", style="dim")
        rows.append(hint)
        return Panel(
            Group(*rows),
            title=Text("Confirm", style="bold yellow"),
            border_style="yellow",
            padding=(0, 1),
            expand=True,
        )

    def _render_ask_text(self) -> RenderableType:
        payload = self._current.payload or {}
        prompt = payload.get("prompt", "")
        placeholder = payload.get("placeholder", "")
        rows: list[RenderableType] = []
        if prompt:
            rows.append(Text(prompt, style="bold"))
        if placeholder:
            rows.append(Text(f"hint: {placeholder}", style="dim italic"))
        rows.append(Text(""))
        rows.append(
            Text("↓ Type your answer in the input below ↓", style="dim cyan italic")
        )
        rows.append(Text(""))
        hint = Text()
        hint.append("Enter", style="cyan")
        hint.append(" submit  ", style="dim")
        hint.append("Esc", style="cyan")
        hint.append(" cancel", style="dim")
        rows.append(hint)
        return Panel(
            Group(*rows),
            title=Text("Input requested", style="bold cyan"),
            border_style="cyan",
            padding=(0, 1),
            expand=True,
        )

    def _render_selection(self) -> RenderableType:
        payload = self._current.payload or {}
        prompt = payload.get("prompt", "")
        options = self._options()
        multi = bool(payload.get("multi", False))
        rows: list[RenderableType] = []
        if prompt:
            rows.append(Text(prompt, style="bold"))
        rows.append(Text(""))
        for i, opt in enumerate(options):
            opt_id = str(opt.get("id", ""))
            label = opt.get("label", opt_id)
            desc = opt.get("description", "")
            line = Text()
            if i == self._option_index:
                line.append(" › ", style="bold bright_cyan")
            else:
                line.append("   ", style="dim")
            if multi:
                checked = "[x]" if opt_id in self._multi_selected else "[ ]"
                line.append(f"{checked} ", style="cyan")
            line.append(
                label,
                style="bold bright_cyan" if i == self._option_index else "white",
            )
            if desc:
                line.append(f"  — {desc}", style="dim")
            rows.append(line)
        rows.append(Text(""))
        hint = Text()
        hint.append("↑↓", style="cyan")
        hint.append(" navigate  ", style="dim")
        if multi:
            hint.append("Space", style="cyan")
            hint.append(" toggle  ", style="dim")
        hint.append("Enter", style="cyan")
        hint.append(" submit  ", style="dim")
        hint.append("Esc", style="cyan")
        hint.append(" cancel", style="dim")
        rows.append(hint)
        title_text = "Pick options" if multi else "Pick one"
        return Panel(
            Group(*rows),
            title=Text(title_text, style="bold cyan"),
            border_style="cyan",
            padding=(0, 1),
            expand=True,
        )

    def _render_card(self) -> RenderableType:
        """Render an interactive card (title + body + fields + actions).

        Cards with non-empty ``actions`` (excluding ``link`` style)
        route through this overlay. The card body is shown first, then
        the action buttons are listed with the same selection cursor
        as confirm/selection. Number-key shortcuts and arrow keys work
        identically.
        """
        payload = self._current.payload or {}
        title = payload.get("title", "")
        subtitle = payload.get("subtitle", "")
        icon = payload.get("icon", "")
        body = payload.get("body", "")
        fields = payload.get("fields") or []
        footer = payload.get("footer", "")
        accent = payload.get("accent", "neutral")

        accent_map = {
            "primary": "bright_cyan",
            "info": "blue",
            "success": "bright_green",
            "warning": "yellow",
            "danger": "bright_red",
            "neutral": "white",
        }
        border = accent_map.get(accent, "white")

        rows: list[RenderableType] = []
        header = f"{icon} {title}".strip() if icon else title
        if subtitle:
            header_line = Text()
            header_line.append(header, style="bold")
            header_line.append("  ")
            header_line.append(subtitle, style="dim")
            rows.append(header_line)
        elif header:
            rows.append(Text(header, style="bold"))

        if body:
            rows.append(Text(""))
            rows.append(Text(body))

        if fields:
            rows.append(Text(""))
            for f in fields:
                line = Text()
                line.append("  ", style="dim")
                line.append(f.get("label", ""), style="bold")
                line.append(": ")
                line.append(str(f.get("value", "")))
                rows.append(line)

        # Actions (filtered to exclude link-style which doesn't reply).
        actions = self._options()
        if actions:
            rows.append(Text(""))
            for i, opt in enumerate(actions):
                label = opt.get("label", opt.get("id", "?"))
                style_map = {
                    "primary": "bright_cyan",
                    "secondary": "white",
                    "danger": "bright_red",
                }
                base = style_map.get(opt.get("style", "secondary"), "white")
                line = Text()
                if i == self._option_index:
                    line.append("  › ", style="bold bright_cyan")
                else:
                    line.append(f"  {i + 1}  ", style="dim")
                line.append(
                    label,
                    style=f"bold {base}" if i == self._option_index else base,
                )
                rows.append(line)

        # Link-only actions (informational; printed as URLs since CLI
        # can't open browser inline from inside the overlay).
        all_actions = payload.get("actions") or []
        link_actions = [a for a in all_actions if a.get("style") == "link"]
        if link_actions:
            rows.append(Text(""))
            for la in link_actions:
                line = Text()
                line.append("  🔗 ", style="dim")
                line.append(la.get("label", la.get("id", "?")), style="cyan")
                url = la.get("url")
                if url:
                    line.append(f"  {url}", style="dim underline")
                rows.append(line)

        if footer:
            rows.append(Text(""))
            rows.append(Text(footer, style="dim italic"))

        rows.append(Text(""))
        hint = Text()
        hint.append("←→/↑↓", style="cyan")
        hint.append(" navigate  ", style="dim")
        hint.append("1-9", style="cyan")
        hint.append(" pick  ", style="dim")
        hint.append("Enter", style="cyan")
        hint.append(" submit  ", style="dim")
        hint.append("Esc", style="cyan")
        hint.append(" cancel", style="dim")
        rows.append(hint)

        return Panel(
            Group(*rows),
            title=Text(title or "Card", style=f"bold {border}"),
            border_style=border,
            padding=(0, 1),
            expand=True,
        )
