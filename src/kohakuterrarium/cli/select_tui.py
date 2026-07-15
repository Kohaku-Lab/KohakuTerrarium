"""Textual startup picker for ``kt-tui`` (full-screen mode).

A small standalone Textual ``App`` shown before the engine TUI mounts: an
expandable package→creature ``Tree`` with a filter box.  Selecting a leaf
exits the app with that runnable's ``ref``; Esc/Ctrl+C exit with ``None``.

The grouping/filtering data comes from the UI-free :mod:`.select` module;
this module only renders it with Textual.  Two Textual apps run in
sequence (this picker, then the session TUI) — the picker fully exits
before the engine is built.
"""

from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, Input, Tree

from kohakuterrarium.cli.select import RunnableGroup, _matches


def _leaf_label(runnable) -> str:
    """Format one runnable for the picker tree."""
    tag = "[team] " if runnable.kind == "terrarium" else ""
    bits = [f"{tag}{runnable.name}"]
    if runnable.kind == "terrarium" and runnable.members:
        bits.append(f"({', '.join(runnable.members)})")
    elif runnable.description:
        desc = runnable.description
        bits.append(desc if len(desc) <= 60 else desc[:57] + "…")
    if runnable.model:
        bits.append(f"·{runnable.model}")
    return "  ".join(bits)


class AgentSelectScreen(App):
    """Pre-launch agent picker; ``run()`` returns the chosen ref or None."""

    CSS = """
    Tree { height: 1fr; }
    Input { dock: top; }
    """
    BINDINGS = [
        ("escape", "cancel", "Cancel"),
    ]
    TITLE = "Select an agent"

    def __init__(self, groups: list[RunnableGroup]) -> None:
        """Initialize the picker with grouped runnable metadata."""
        super().__init__()
        self._groups = groups

    def compose(self) -> ComposeResult:
        """Compose the picker header, filter, tree, and footer."""
        yield Header()
        yield Input(placeholder="type to filter…", id="filter")
        yield Tree("Agents", id="tree")
        yield Footer()

    def on_mount(self) -> None:
        """Populate the tree when the picker mounts."""
        self._rebuild("")

    def _rebuild(self, flt: str) -> None:
        """Rebuild the tree using the current filter text."""
        tree = self.query_one("#tree", Tree)
        tree.clear()
        needle = flt.strip().lower()
        for group in self._groups:
            items = group.items()
            if needle:
                items = [r for r in items if _matches(r, needle)]
                if not items:
                    continue
            node = tree.root.add(group.source, expand=True)
            for runnable in items:
                node.add_leaf(_leaf_label(runnable), data=runnable)
        tree.root.expand()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Apply filter changes to the tree."""
        self._rebuild(event.value)

    def on_tree_node_selected(self, event: "Tree.NodeSelected") -> None:
        """Exit with the selected runnable reference."""
        data = event.node.data
        if data is not None:  # Group headers carry no runnable data.
            self.exit(data.ref)

    def action_cancel(self) -> None:
        """Exit without selecting a runnable."""
        self.exit(None)


def run_tui_picker(groups: list[RunnableGroup]) -> str | None:
    """Run the Textual picker; return the chosen ``ref`` or ``None``."""
    return AgentSelectScreen(groups).run()
