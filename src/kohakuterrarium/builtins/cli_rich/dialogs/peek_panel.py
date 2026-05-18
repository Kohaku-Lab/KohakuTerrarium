"""Peek panel — recent-events preview for the agent overlay.

Mounted as the right pane of the overlay when the user hits Space.
Read-only by design — the reply textarea is the existing composer;
the app routes its submit to the peeked creature instead of the
focus when peek is active.

Pulls from
:class:`~kohakuterrarium.builtins.cli_rich.live_state.LiveRegionState`
— specifically its ``recent_event_payloads(seconds=...)`` helper.
"""

from collections.abc import Callable

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.text import Text

from kohakuterrarium.builtins.cli_rich.live_state import LiveRegionState

_PEEK_WINDOW_SECONDS = 30.0
_MAX_PEEK_LINES = 60


def _summarize_event(event) -> str:
    """Best-effort one-line label for whatever event shape we got."""
    if isinstance(event, str):
        return f"[text] {event}"
    kind = getattr(event, "type", None) or getattr(event, "kind", None) or "event"
    payload = getattr(event, "payload", None) or getattr(event, "content", "")
    if isinstance(payload, dict):
        # Pick a short field if present.
        for key in ("detail", "message", "name", "tool", "title"):
            if key in payload and payload[key]:
                return f"[{kind}] {payload[key]}"
    if isinstance(payload, str) and payload:
        return f"[{kind}] {payload}"
    return f"[{kind}]"


def render_peek(
    state: LiveRegionState | None, *, creature_name: str = ""
) -> RenderableType:
    """Build a Rich renderable from a creature's live state."""
    if state is None:
        return Panel(
            Text("No state for the selected creature.", style="dim"),
            title=f"{creature_name or '?'} (peek)",
            border_style="dim",
        )
    payloads = state.recent_event_payloads(seconds=_PEEK_WINDOW_SECONDS)
    rows: list[RenderableType] = []
    rows.append(Text(f"Last {int(_PEEK_WINDOW_SECONDS)}s of output:", style="dim"))
    rows.append(Text(""))
    if not payloads:
        rows.append(Text("(no recent activity)", style="dim"))
    else:
        for event in payloads[-_MAX_PEEK_LINES:]:
            rows.append(Text("  " + _summarize_event(event)))
    if state.text_buffer:
        rows.append(Text(""))
        rows.append(Text("Text buffer:", style="dim"))
        rows.append(
            Text(
                "  "
                + (
                    state.text_buffer
                    if len(state.text_buffer) < 400
                    else "…" + state.text_buffer[-400:]
                )
            )
        )
    rows.append(Text(""))
    rows.append(
        Text(
            "[Reply: type below — routes to this creature while peek is open]",
            style="dim italic",
        )
    )
    return Panel(
        Group(*rows),
        title=(
            f"{state.creature_id} (peek)"
            if not creature_name
            else f"{creature_name} (peek)"
        ),
        border_style="cyan",
    )


class PeekPanel:
    """Stateless render wrapper around ``LiveRegionState``."""

    def __init__(
        self,
        get_state: Callable[[str | None], LiveRegionState | None],
        get_name: Callable[[str | None], str] | None = None,
    ) -> None:
        self.get_state = get_state
        self.get_name = get_name

    def render(self, creature_id: str | None) -> RenderableType | None:
        if not creature_id:
            return None
        state = self.get_state(creature_id)
        name = self.get_name(creature_id) if self.get_name else ""
        return render_peek(state, creature_name=name or "")


__all__ = ["PeekPanel", "render_peek"]
