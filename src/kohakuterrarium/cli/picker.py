"""Startup-picker dispatcher: enumerate runnables, route to a renderer.

:func:`pick_runnable` is the single entry the run path calls when no
creature was named.  It enumerates the catalog once, then dispatches to
the prompt_toolkit picker (cli) or the Textual picker (tui).  Empty-catalog
handling lives here; the interactive-terminal precondition is checked by
the caller (``resolve_then_run``) before we get here.
"""

from kohakuterrarium.cli.select import enumerate_runnables
from kohakuterrarium.utils.logging import get_logger
from kohakuterrarium.utils.startup_trace import mark as mark_startup

logger = get_logger(__name__)


def pick_runnable(io_mode: str) -> str | None:
    """Run the startup picker for ``io_mode`` and return a ref or ``None``.

    Returns the chosen creature/recipe ``ref`` (``@pkg/...`` or a path),
    or ``None`` when the catalog is empty or the user cancelled.
    """
    groups = enumerate_runnables()
    mark_startup(
        "picker_catalog_scanned",
        surface=io_mode,
        groups=len(groups),
        entries=sum(len(group.items()) for group in groups),
    )
    if not groups:
        print(
            "No creatures or terrariums found.\n"
            "  Install one:  kt install @<name>\n"
            "  Or create one: ./creatures/<name>/config.yaml"
        )
        return None
    if io_mode == "tui":
        from kohakuterrarium.cli.select_tui import run_tui_picker

        return run_tui_picker(groups)
    from kohakuterrarium.cli.select_cli import run_cli_picker

    return run_cli_picker(groups)
