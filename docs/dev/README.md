# Development

For contributors working on the framework itself. Not for users.

## Contributing workflow

See the top-level [CONTRIBUTING.md](../../CONTRIBUTING.md) for setup,
branch conventions, and PR flow. Read [CLAUDE.md](../../CLAUDE.md)
before touching code — it encodes the architecture rules (Creature vs
Terrarium vs Root, controller-as-orchestrator, non-blocking tool
dispatch) and the style conventions (modern type hints, no in-function
imports, logging instead of `print`).

## In this section

- [Architecture](internals.md) — implementation-level map of the 16
  runtime flows. Read alongside `src/kohakuterrarium/`.
- [Testing](testing.md) — how to run the suite and use the
  `ScriptedLLM` / `TestAgentBuilder` harness.
- [Dependency rules](dependency-graph.md) — the leaf-first import
  discipline and how to verify with `scripts/dep_graph.py`.
- [Frontend](frontend.md) — Vue 3 dashboard, panel registration,
  WebSocket contracts.

## When to read what

- Just landed? Start with [CONTRIBUTING.md](../../CONTRIBUTING.md),
  then skim [internals.md](internals.md) top-to-bottom.
- Adding a tool, trigger, or module? Read the relevant concept doc
  under [../concepts/modules/](../concepts/modules/README.md) first.
  The concepts explain *why*; this section explains *where*.
- Changing the agent lifecycle or the controller loop? Read
  [internals.md §Agent runtime](internals.md#1-agent-runtime) and the
  impl-notes — especially
  [non-blocking-compaction](../concepts/impl-notes/non-blocking-compaction.md)
  and [stream-parser](../concepts/impl-notes/stream-parser.md).
- Touching persistence? Read
  [session-persistence](../concepts/impl-notes/session-persistence.md)
  before the code.

## Code-near docs

Every subpackage under `src/kohakuterrarium/` has its own `README.md`
covering the files it contains. Those are the most accurate description
of "what actually lives here." Use them together with this section.
