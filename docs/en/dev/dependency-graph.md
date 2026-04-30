---
title: Dependency graph
summary: Module import-direction invariants and the tests that enforce them.
tags:
  - dev
  - internals
  - architecture
---

# Dependency rules

The package has a strict one-way import discipline. Enforced by
convention and verified by `scripts/dep_graph.py`. There are zero
runtime cycles; keep it that way.

## The rules, in one paragraph

`utils/` is a leaf. Everything imports from it; it imports nothing
from the framework. `modules/` is protocols only. `core/` is the
creature runtime — it imports `modules/` and `utils/` but **never**
`builtins/`, `terrarium/`, `studio/`, `bootstrap/`, `api/`, or `cli/`.
`bootstrap/` and `builtins/` assemble concrete runtime pieces on top of
`core/` + `modules/`. `terrarium/` hosts creatures in graphs and imports
`core/` + `bootstrap/`. `studio/` sits above `terrarium/` for management
policy. `cli/` and `api/` are top-layer adapters over `studio/` /
`terrarium/` plus launch glue.

## The tiers

From leaf (bottom) to transport (top):

```
  cli/, api/                    <- user/API adapters
  studio/                       <- management facade and policies
  serving/                      <- launch helpers + legacy compatibility wrappers
  terrarium/                    <- creature graph runtime engine
  bootstrap/, builtins/         <- assembly + implementations
  core/                         <- creature runtime
  modules/                      <- protocols (plus some base classes)
  parsing/, prompt/, llm/, …    <- support packages
  testing/                      <- depends on the whole stack, used only by tests
  utils/                        <- leaf
```

Per-tier detail:

- **`utils/`** — logging, async helpers, file guards. Must not import
  anything from the framework. Adding a framework import here is
  almost always wrong.
- **`modules/`** — protocol and base class definitions. `BaseTool`,
  `BaseOutputModule`, `BaseTrigger`, etc. Implementation-free so any
  layer above can depend on them.
- **`core/`** — `Agent`, `Controller`, `Executor`, `Conversation`,
  `Environment`, `Session`, channels, events, registry. The runtime.
  `core/` must never import `terrarium/`, `builtins/`, `bootstrap/`,
  `serving/`, `cli/`, or `api/`. Doing so reintroduces a cycle.
- **`bootstrap/`** — factory functions that build `core/` components
  from config (LLM, tools, IO, subagents, triggers). Imports `core/`
  and `builtins/`.
- **`builtins/`** — concrete tools, sub-agents, inputs, outputs, TUI,
  user commands. Internal catalogs (`tool_catalog`,
  `subagent_catalog`) are leaf modules with deferred loaders.
- **`terrarium/`** — creature graph runtime. Imports `core/`,
  `bootstrap/`, `builtins/`. Not imported by any of them.
- **`studio/`** — management facade for catalog, identity, active sessions,
  saved-session persistence, attach policy, and editors. Depends on
  `terrarium/` and lower layers.
- **`serving/`** — web/desktop launch helpers plus legacy compatibility
  wrappers. New management code should live in `studio/`.
- **`cli/`, `api/`** — top layer. One is an argparse entry point, the
  other a FastAPI app. They delegate management to `studio/` and runtime
  mechanics to `terrarium/`.

See [`src/kohakuterrarium/README.md`](../../src/kohakuterrarium/README.md)
for the ASCII dependency flow used as the source of truth.

## Why these rules

The rules serve three goals:

1. **No cycles.** Cycles cause init-order fragility, partial-import
   errors, and import-time side effects that bite at startup.
2. **Testability.** If `core/` never imports `terrarium/`, you can unit
   test the controller without spinning up a multi-agent runtime. If
   `modules/` is protocol-only, you can swap implementations trivially.
3. **Clear change surface.** When you modify `utils/`, everything
   rebuilds. When you modify `cli/`, nothing else does. Tiers give
   you a predictable blast radius.

Historical note: there used to be a cycle
`builtins.tools.registry → terrarium.runtime → core.agent →
builtins.tools.registry`. It was broken by introducing catalog/helper
modules and moving terrarium root-tool implementations under
`terrarium/`. `core/__init__.py` still uses module-level `__getattr__`
for lazy public exports, but new function-local imports should be
justified by the dep-graph allowlist rather than used as a cycle
workaround.

## The tool — `scripts/dep_graph.py`

Static AST analyzer. Walks every `.py` under `src/kohakuterrarium/`,
reads files as UTF-8, parses `import` / `from ... import`, and classifies
edges as:

- **runtime** — top-level import that executes on module load.
- **TYPE_CHECKING** — guarded by `if TYPE_CHECKING:`. Not in the
  runtime graph.
- **in-function** — import inside a function body. The default/cycle view
  includes these so hidden cycles are still visible; `--module-only`
  restores the older top-level-only graph.

Import-hygiene linting classifies in-function imports against stdlib,
required deps, optional deps, platform-only modules, and
`scripts/dep_graph_allowlist.json`. Every allowlisted import needs a
reason.

### Commands

```bash
# Summary stats + cross-group edge counts (default)
python scripts/dep_graph.py

# Runtime SCC cycle detection (includes in-function imports by default)
python scripts/dep_graph.py --cycles

# In-function import policy report
python scripts/dep_graph.py --lint-imports

# JSON dump (graph + lint result)
python scripts/dep_graph.py --json

# Exit non-zero on parse errors / cycles / lint violations
python scripts/dep_graph.py --fail

# Graphviz DOT output (pipe into `dot -Tsvg`)
python scripts/dep_graph.py --dot > deps.dot

# Render a matplotlib group + module plot into plans/
python scripts/dep_graph.py --plot

# Stats + cycles + import lint
python scripts/dep_graph.py --all
```

Key outputs:

- **Top fan-out** — modules that import the most. Usually assembly
  code (`bootstrap/`, `core/agent.py`).
- **Top fan-in** — modules imported the most. `utils/`, `modules/base`,
  `core/events.py` should dominate.
- **Cross-group edges** — a bar-chart-style readout of how many edges
  cross package boundaries. If a new edge appears from `core/` into
  `terrarium/`, investigate.
- **SCCs** — should always be empty. If Tarjan's algorithm finds a
  non-trivial SCC, the runtime graph has a cycle. Cycle reports include
  a sample path and the import statements that form it.
- **Import hygiene** — `--lint-imports` reports disallowed in-function
  imports. Optional/platform imports are auto-allowed; intentional
  exceptions live in `scripts/dep_graph_allowlist.json`.

The `--plot` flag writes `plans/dep-graph.png` (group-level, circular
layout). It is useful for PR review when a refactor shuffles edges.

### When to run it

- Before a PR that adds a new subpackage.
- When you suspect a circular import (symptom: `ImportError` at
  startup mentioning a partially initialized module).
- As a sanity check after a large refactor.

Run `python scripts/dep_graph.py --fail` and confirm the output includes:

```
None found. The runtime import graph is acyclic.
```

If it exits non-zero, fix parse errors, cycles, or lint violations before merging. CI runs the same guard in `tests/unit/test_dep_graph_lint.py` as a dedicated `test-dep-graph` job.

## Adding a new package

Pick the right tier. Ask:

- **Does it have runtime behavior, or just base classes / protocols?**
  Protocols → `modules/`. Runtime → `core/` or a dedicated subpackage.
- **Does it need `core.Agent`?** If yes, it sits above `core/`, not
  inside.
- **Is it a built-in (shipped with KT) or an extension?** Built-ins
  go under `builtins/`; extensions live in separate packages and plug
  in via the package manifest.

Then respect the tier's import rules:

- `utils/` imports nothing framework-side.
- `modules/` imports `utils/` and core typing, nothing else.
- `core/` imports `modules/`, `utils/`, `llm/`, `parsing/`, `prompt/`.
  Never `terrarium/`, `serving/`, `builtins/`, `bootstrap/`.
- `bootstrap/` and `builtins/` import `core/` + `modules/`.
- Everything else sits above that.

If a new edge feels awkward, it probably is. Introduce a leaf helper
module (like `tool_catalog`) to break the cycle instead of papering
over with an in-function import. In-function imports are discouraged
(CLAUDE.md §Import Rules) and are the last resort, not the first.

## See also

- [CLAUDE.md §Import Rules](../../CLAUDE.md) — the conventions this
  discipline enforces.
- [`src/kohakuterrarium/README.md`](../../src/kohakuterrarium/README.md) —
  the canonical ASCII flow diagram.
- [internals.md](internals.md) — flow-by-flow map of what each
  subpackage is for.
