# Dependency rules

The package has a strict one-way import discipline. Enforced by
convention and verified by `scripts/dep_graph.py`. There are zero
runtime cycles; keep it that way.

## The rules, in one paragraph

`utils/` is a leaf. Everything imports from it; it imports nothing
from the framework. `modules/` is protocols only. `core/` is the
runtime — it imports `modules/` and `utils/` but **never** `builtins/`
or `terrarium/` or `bootstrap/`. `bootstrap/` and `builtins/` import
`core/` + `modules/`. `terrarium/` and `serving/` import `core/` +
`bootstrap/`. `cli/` and `api/` sit on top of `serving/` + `terrarium/`.

## The tiers

From leaf (bottom) to transport (top):

```
  cli/, api/                    <- transports
  serving/, terrarium/          <- orchestration
  bootstrap/, builtins/         <- assembly + implementations
  core/                         <- runtime engine
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
- **`terrarium/`** — multi-agent runtime. Imports `core/`,
  `bootstrap/`, `builtins/`. Not imported by any of them.
- **`serving/`** — `KohakuManager`, `AgentSession`. Depends on `core/`
  and `terrarium/`. Transport-agnostic.
- **`cli/`, `api/`** — top layer. One is an argparse entry point, the
  other a FastAPI app. Both consume `serving/`.

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
builtins.tools.registry`. It was broken by introducing `tool_catalog`
as a leaf module with deferred loaders. See
[`internals.md`](internals.md) legacy notes section in git history for
the details. Only two legitimate lazy imports remain: `core/__init__.py`
uses `__getattr__` to avoid a `core.agent` init-order issue, and
`terrarium/tool_registration.py` defers terrarium-tool registration
until first lookup.

## The tool — `scripts/dep_graph.py`

Static AST analyzer. Walks every `.py` under `src/kohakuterrarium/`,
parses `import` / `from ... import`, and classifies each edge as:

- **runtime** — top-level import that executes on module load.
- **TYPE_CHECKING** — guarded by `if TYPE_CHECKING:`. Not in the
  runtime graph.
- **lazy** — import inside a function body. Not in the runtime graph.

Only runtime edges count for cycle detection.

### Commands

```bash
# Summary stats + cross-group edge counts (default)
python scripts/dep_graph.py

# Runtime SCC cycle detection
python scripts/dep_graph.py --cycles

# Graphviz DOT output (pipe into `dot -Tsvg`)
python scripts/dep_graph.py --dot > deps.dot

# Render a matplotlib group + module plot into plans/
python scripts/dep_graph.py --plot

# All of the above
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
  non-trivial SCC, the runtime graph has a cycle.

The `--plot` flag writes `plans/dep-graph.png` (group-level, circular
layout) and `plans/dep-graph-detailed.png` (module-level, concentric
rings). Both are useful for PR review when a refactor shuffles edges.

### When to run it

- Before a PR that adds a new subpackage.
- When you suspect a circular import (symptom: `ImportError` at
  startup mentioning a partially initialized module).
- As a sanity check after a large refactor.

Run `python scripts/dep_graph.py --cycles` and confirm the output
reads:

```
None found. The runtime import graph is acyclic.
```

If it doesn't, fix the cycle before merging.

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
