"""Guard: no source file exceeds 600 lines (soft) or 1000 lines (hard)."""

from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "kohakuterrarium"

# Pure-data files exempt from BOTH the 600-line and 1000-line caps.
# These grow linearly with the data they describe (one entry per
# tool / preset / catalogue item) — splitting them would fragment a
# single discoverable map across many small files for no readability
# win. Code logic does NOT belong in any file listed here.
DATA_FILE_UNLIMITED = {
    # Per-builtin-tool JSON-schema map for native function-calling.
    # Imported by ``llm/tools.py:build_tool_schemas``. One entry per
    # tool — adding a new builtin tool always means appending here,
    # so the file grows monotonically with the catalogue.
    "llm/tool_schemas.py",
}

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
    # Terrarium engine public facade with cohesive topology/wiring surface.
    "terrarium/engine.py",
    # CLI runner with argparse (barely over)
    "terrarium/cli.py",
    # Prompt aggregation pipeline (barely over)
    "prompt/aggregator.py",
    # Pure data (model presets)
    "llm/presets.py",
    # Preset + backend resolution module: cohesive registry state +
    # lookup rules (YAML layout, (provider, name) key, variation
    # selector parse, ambiguity handling, list_all). Splitting further
    # would scatter related logic across many small files.
    "llm/profiles.py",
    # Rich CLI orchestrator — same shape as core/agent.py + manager.py
    # (top-level class owning lifecycle + layout + many small delegation
    # methods). Output-event handlers already extracted to AppOutputMixin.
    "builtins/cli_rich/app.py",
    # Settings overlay state machine — list/form/confirm modes + 4 tabs of
    # data loaders and action handlers. Rendering already split into
    # settings_render.py; splitting further would fragment a cohesive
    # state machine.
    "builtins/cli_rich/dialogs/settings.py",
    # Package manager facade — install/uninstall/list + resolvers for
    # every manifest field (tools / plugins / io / triggers / skills /
    # commands / user_commands / prompts / templates). Resolver bodies
    # are short and uniform; splitting further would scatter the
    # top-level function signatures external callers depend on.
    "packages.py",
    # Sub-agent runtime loop: conversation setup, native + text turn
    # paths, tool execution, budget accounting, result building. Each
    # helper is short but they share a lot of instance state, and
    # splitting further would scatter closely-coupled pieces across
    # files without improving comprehension.
    "modules/subagent/base.py",
    # Event-handler mixin: controller loop, event dispatch, processing
    # lifecycle, tool-completion routing, termination checks. Helpers
    # already extracted to agent_tools/agent_pre_dispatch/skill-hints;
    # the remaining code is a single cohesive lifecycle.
    "core/agent_handlers.py",
    # Session store facade — owns every KVault table + uniform per-table
    # getters/setters (meta, state, events, channels, subagents, jobs,
    # conversation, turn_rollup, fts). Heavy lifting for counters, fork,
    # rollups already extracted to sibling modules (store_counters,
    # store_fork, rollup); what remains is the cohesive table surface.
    "session/store.py",
    # Session output module — one cohesive OutputModule that routes ~18
    # distinct activity types (tool/subagent/token/compact/plugin-hook
    # /cache/scratchpad/attach) to ``_record``. Handlers are short and
    # uniform; splitting them across files would fragment a single
    # dispatch table for no readability win.
    "session/output.py",
    # Studio façade — pure consumer class wrapping every studio
    # sub-package (catalog/identity/sessions/persistence/editors/attach)
    # as nested namespaces.  Every method is a one-liner forwarding to
    # an existing function; splitting the namespaces across files would
    # fragment a single discoverable surface for the programmatic API.
    "studio/studio.py",
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
    if rel in DATA_FILE_UNLIMITED:
        return  # pure data, no upper limit
    if rel in ALLOWLIST_600:
        assert lines <= 1000, f"{rel} is {lines} lines (allowlisted but max 1000)"
    else:
        assert lines <= 600, f"{rel} is {lines} lines (max 600)"


@pytest.mark.parametrize(
    "path", list(_all_py_files()), ids=lambda p: str(p.relative_to(SRC))
)
def test_file_under_1000_lines(path):
    """Hard max: no file should ever exceed 1000 lines (data files exempt)."""
    rel = str(path.relative_to(SRC)).replace("\\", "/")
    if rel in DATA_FILE_UNLIMITED:
        return  # pure data, no upper limit
    lines = len(path.read_text(encoding="utf-8").splitlines())
    assert lines <= 1000, f"{path.relative_to(SRC)} is {lines} lines (hard max 1000)"
