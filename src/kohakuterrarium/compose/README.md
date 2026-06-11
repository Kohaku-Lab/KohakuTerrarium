# compose/

Agent composition algebra. Pythonic operators (`>>`, `&`, `|`, `*`) over
engine-backed creatures and plain callables so pipelines read like code
instead of YAML. Zero framework coupling beyond `terrarium/`; everything
else is pure async combinators.

## Files

| File          | Responsibility                                                                                                                                         |
| ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `__init__.py` | Public API re-exports (`agent`, `factory`, `pure`, `Runnable`, `Sequence`, `Product`, `Fallback`, `Retry`, `Router`, `Pure`, `FailsWhen`, `PipelineIterator`) |
| `core.py`     | `BaseRunnable` (operator overloads) + every combinator in one file to avoid circular imports                                                           |
| `agent.py`    | `AgentRunnable` (persistent session) + `AgentFactory` (ephemeral), and the `agent()` / `factory()` convenience constructors                            |

## Dependency direction

Imported by: user code only (examples, notebooks, application scripts).
Nothing inside the framework imports `compose/`.

Imports: `terrarium` (`Terrarium`), `core/config_types` (`AgentConfig`),
`utils/logging`, and stdlib `asyncio` / `inspect`.

## Key entry points

- `agent(spec, *, engine=, pwd=, llm=)`: async constructor; returns a
  started `AgentRunnable`. `spec` is an `AgentConfig`, a path, or an
  `@pkg/creatures/<name>` reference; `llm` follows the
  `Terrarium.add_creature` grammar (profile name / `LLMProfile` /
  provider instance). Pass `engine=` to share one engine across agents.
- `factory(spec, *, engine=, pwd=, llm=)`: sync; returns a lazy
  `AgentFactory` (fresh agent per call); same keywords.
- `BaseRunnable`: base class for custom combinators; provides operator overloads
- `pure(fn)` / `Pure(fn)`: wrap any sync/async callable as a `Runnable`
- `BaseRunnable.retry(n, backoff=…)`: retry with exponential backoff
- `BaseRunnable.iterate(initial)`: async-for loop that feeds output back as input

## Operators

| Op                   | Combinator         | Semantics                                                                                          |
| -------------------- | ------------------ | --------------------------------------------------------------------------------------------------- |
| `a >> b`             | `Sequence`         | Run `a`, pipe output to `b`. Auto-wraps callables with `Pure`. Dict syntax builds a `Router`.       |
| `a & b`              | `Product`          | Run concurrently, return tuple. First failure cancels the surviving siblings before propagating.    |
| `a \| b`             | `Fallback`         | Try `a`; on `Exception`, run `b` with the original input. If `b` also fails, `a`'s exception chains as `__cause__`. |
| `a * N`              | `Retry`            | Retry `a` up to `N` times on exception (immediate; use `.retry(N, backoff=…)` for delays).          |
| `p.map(fn)`          | (none) | Post-process output.                                                                                |
| `p.contramap(fn)`    | (none) | Pre-process input.                                                                                  |
| `p.fails_when(pred)` | `FailsWhen`        | Treat matching outputs as failure (triggers `\|` fallback).                                         |
| `await p(x)`         | (none) | Run the pipeline.                                                                                   |
| `p.iterate(x)`       | `PipelineIterator` | Async iterate (supports `.feed(override)`).                                                         |

## Notes

- All combinators inherit from `BaseRunnable`, so nested structures get the
  same operators for free.
- `Sequence._flat` and `Product._flat` collapse adjacent same-kind
  combinators so `a >> b >> c` is a single 3-step Sequence, not nested.
- `AgentRunnable` reuses one creature across calls, so conversation
  history accumulates. Use `async with await agent(...)` for cleanup.
- `AgentFactory` spins up a fresh creature per invocation: no carry-over,
  no cleanup needed.
- Without `engine=`, each constructor owns a private `Terrarium` and
  shuts it down on close; with `engine=`, close only removes the
  creature; the caller's engine is never shut down.

## See also

- `../terrarium/creature_host.py`: the `Creature.chat` adapter the runnables drive
- `docs/concepts/python-native/`: philosophy + examples of the algebra
- `examples/compose/`: runnable pipeline demos
