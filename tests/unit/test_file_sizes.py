"""Guard: no source file exceeds 600 lines (soft) or 1000 lines (hard)."""

from pathlib import Path
import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "kohakuterrarium"

# Files allowed to exceed 600 lines (with justification)
ALLOWLIST_600 = {
    # Single cohesive class with many small uniform methods
    "builtins/tui/session.py",
    # TUI output with many render methods
    "builtins/tui/output.py",
    # Facade with many short delegation methods
    "serving/manager.py",
    # State machine parser, necessarily complex
    "parsing/state_machine.py",
    # Controller loop, high internal cohesion
    "core/controller.py",
    # Agent class, orchestrates all subsystems
    "core/agent.py",
    # CLI runner with argparse (barely over)
    "terrarium/cli.py",
    # Prompt aggregation pipeline (barely over)
    "prompt/aggregator.py",
    # Pure data (model presets)
    "llm/presets.py",
    # Rich CLI orchestrator — same shape as core/agent.py + manager.py
    # (top-level class owning lifecycle + layout + many small delegation
    # methods). Output-event handlers already extracted to AppOutputMixin.
    "builtins/cli_rich/app.py",
    # Settings overlay state machine — list/form/confirm modes + 4 tabs of
    # data loaders and action handlers. Rendering already split into
    # settings_render.py; splitting further would fragment a cohesive
    # state machine.
    "builtins/cli_rich/dialogs/settings.py",
}


def _all_py_files():
    for p in SRC.rglob("*.py"):
        yield p


@pytest.mark.parametrize(
    "path", list(_all_py_files()), ids=lambda p: str(p.relative_to(SRC))
)
def test_file_under_600_lines(path):
    rel = str(path.relative_to(SRC)).replace("\\", "/")
    lines = len(path.read_text(encoding="utf-8").splitlines())
    if rel in ALLOWLIST_600:
        assert lines <= 1000, f"{rel} is {lines} lines (allowlisted but max 1000)"
    else:
        assert lines <= 600, f"{rel} is {lines} lines (max 600)"


@pytest.mark.parametrize(
    "path", list(_all_py_files()), ids=lambda p: str(p.relative_to(SRC))
)
def test_file_under_1000_lines(path):
    """Hard max: no file should ever exceed 1000 lines."""
    lines = len(path.read_text(encoding="utf-8").splitlines())
    assert lines <= 1000, f"{path.relative_to(SRC)} is {lines} lines (hard max 1000)"
