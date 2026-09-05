"""Goal creation modal and graph-member helpers for the TUI drive panel."""

from typing import Any

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label


class GoalCreateModal(ModalScreen[str | None]):
    """Collect the objective for a new manual goal."""

    DEFAULT_CSS = """
    GoalCreateModal { align: center middle; }
    #goal-create-box {
        width: 70; height: auto; padding: 1 2;
        border: thick #5A4FCF 60%; background: $surface;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, creature_label: str) -> None:
        super().__init__()
        self._creature_label = creature_label

    def compose(self) -> ComposeResult:
        with Vertical(id="goal-create-box"):
            yield Label(
                f"New goal for {escape(self._creature_label)} "
                "(manual autonomy; resume wakes the next turn)"
            )
            yield Input(placeholder="objective…", id="goal-create-input")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = (event.value or "").strip()
        self.dismiss(text or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


def graph_members(engine: Any, creature_id: str) -> list[tuple[str, str]]:
    """List ``(creature_id, name)`` for the graph that holds ``creature_id``."""
    lister = getattr(engine, "list_creatures", None)
    if not callable(lister) or not creature_id:
        return []
    try:
        creatures = list(lister())
    except Exception:  # pragma: no cover - engine without a creature listing
        return []
    graph_id = next(
        (c.graph_id for c in creatures if c.creature_id == creature_id), None
    )
    members = [
        (c.creature_id, c.name or c.creature_id)
        for c in creatures
        if graph_id is None or c.graph_id == graph_id
    ]
    return sorted(members, key=lambda m: (m[1], m[0]))


def next_member(members: list[tuple[str, str]], current: str) -> str | None:
    """Return the member after ``current``, or ``None`` when there is no other."""
    ids = [m[0] for m in members]
    if len(ids) < 2:
        return None
    idx = ids.index(current) if current in ids else -1
    return ids[(idx + 1) % len(ids)]


__all__ = ["GoalCreateModal", "graph_members", "next_member"]
