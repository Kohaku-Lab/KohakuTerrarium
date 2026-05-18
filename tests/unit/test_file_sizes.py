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
    # methods). Output-event handlers already extracted to AppOutputMixin;
    # multi-creature wiring extracted to AppMultiCreatureMixin.
    "builtins/cli_rich/app.py",
    # Composer — prompt_toolkit TextArea + ~25 key bindings (paste
    # detection, picker indirection, completion, multi-line edit, focus
    # cycling). Splitting would scatter keymap setup from the TextArea.
    "builtins/cli_rich/composer.py",
    # Multi-creature mixin — cohesive surface for RichCLIApp's
    # multi-creature mode (setup/teardown, focus controller, @name
    # routing, multiplex demux, runtime CREATURE_STARTED/STOPPED
    # subscriber + dynamic mount/unmount, B2 per-creature scrollback
    # capture + on-focus redraw, user-message routing helpers). Every
    # method here references the same RichCLIApp instance state
    # (live_regions, draft_by_creature, committer, focus_controller);
    # splitting along category lines would scatter handlers that all
    # mutate the same dicts across files for no readability win.
    "builtins/cli_rich/app_multi.py",
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
    # Laboratory host engine — top-level orchestrator class owning
    # accept loop, Hello/Welcome handshake, per-client read/write
    # tasks, envelope routing (match-case over EnvelopeKind), pluggable
    # CONTROL + APP extension dispatch, and heartbeat reaper. Same
    # shape as core/agent.py / serving/manager.py / cli_rich/app.py:
    # one cohesive lifecycle with many short delegation methods.
    "laboratory/_internal/host.py",
    # Laboratory client connector — counterpart to host.py with the
    # same shape: handshake, read/write tasks, reconnect+backoff loop,
    # APP request/response dispatch with pending-future bookkeeping,
    # heartbeat producer. The added structured logging at every state
    # transition (boot/connect/reconnect/dispatch/abort) pushes it over
    # the soft cap. Splitting transport-side bookkeeping from handler
    # dispatch would fragment a single cohesive lifecycle.
    "laboratory/_internal/client.py",
    # TerrariumService Protocol + LocalTerrariumService — full
    # per-creature API surface (chat / state / mutation / wiring /
    # cluster snapshot). One cohesive Protocol definition with a
    # uniform LocalImpl method per Protocol method; splitting along
    # category lines would scatter related implementations across
    # files that all consume the same engine handle.
    "terrarium/service.py",
    # RemoteTerrariumService — wire-call counterpart to service.py,
    # one method per Protocol entry. Same shape rationale.
    "terrarium/remote_service.py",
    # Worker-side ``terrarium.files`` adapter — cohesive Lab APP handler
    # covering the full file-IO surface (list/stat/read/read_stream/
    # write/write_stream/write_commit/write_abort/delete/watch/push_bundle)
    # over five scopes. Each op is short; splitting the dispatch would
    # fragment the namespace surface external Lab callers consume.
    "laboratory/adapters/terrarium_files.py",
    # MultiNodeTerrariumService — composite service. ~30 route-by-home
    # delegations + fan-out subscribe/snapshot in one cohesive
    # registry / dispatch class.
    "terrarium/multi_node_service.py",
    # Session lifecycle — start_creature/start_terrarium with
    # local-vs-remote branches, rename, list, stop, attach, find. Many
    # short helpers around the engine; splitting would fragment the
    # session creation surface frontends depend on.
    "studio/sessions/lifecycle.py",
    # Pure agent-touching helpers shared by service.py + studio.sessions.
    # One function per Protocol method (scratchpad, triggers, env,
    # plugins, modules, native tools, attach policies, runtime graph
    # snapshot, slash-command dispatch).  Grouped by topic; splitting
    # would scatter related agent reads across many small files.
    "terrarium/creature_ops.py",
    # Lab APP adapter for ``terrarium.runtime``.  One match-case per
    # RPC type (40+ types: lifecycle, channels, per-creature state,
    # module catalog, runtime graph snapshot).  Splitting along category
    # lines would fragment the single authoritative dispatch table.
    "laboratory/adapters/terrarium_runtime.py",
    # Channel topology + persistence + event emission for the engine.
    # Adding the ``CHANNEL_MESSAGE`` emit (2026-05-13) pushed it over
    # the soft cap; splitting persistence from topology would scatter
    # the on_send install + store wiring + event emit across files
    # that all reference the same ``Channel`` lifecycle.
    "terrarium/channels.py",
    # FastAPI application factory + lifespan + route mounting.  The
    # multi-node lifespan (HostEngine + adapters + membership watcher +
    # mirror writer + broadcast + output-wire forwarder) and the long
    # route-include block sit in one cohesive boot path; splitting
    # would scatter the dependency order callers rely on.
    "api/app.py",
    # AgentInitMixin: cohesive ``_init_*`` / ``_create_*`` lifecycle
    # mixin for the Agent class (LLM, registry, executor, subagents,
    # controller, skills, I/O, user-commands, triggers). Same shape as
    # the already-allowlisted ``core/agent_handlers.py``; heavy lifting
    # is already delegated to ``bootstrap/*`` factory modules — what
    # remains is the wiring sequence the Agent depends on in order.
    "bootstrap/agent_init.py",
    # AgentToolsMixin: tool dispatch + handle waiting + promotion +
    # interruption + completion-activity emission + native/text result
    # formatting. One cohesive lifecycle around in-flight tool jobs;
    # runtime tool registration already extracted to
    # ``agent_runtime_tools.py`` and metrics to ``agent_tools_metrics.py``.
    "core/agent_tools.py",
    # Resumable-events normalization pipeline — cohesive replay/branch
    # logic: nested-branch parent-path resolution, branch-view selection,
    # OpenAI-shape conversation replay, interrupted-job synthesis, plus
    # the B8 synthetic ``assistant_tool_calls`` injector that pairs
    # orphan tool_call/tool_result events ahead of replay. Splitting
    # would scatter the pairing + synthesis + rollup logic across files
    # that all reference the same event stream and selected_branches
    # state.
    "session/history.py",
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
