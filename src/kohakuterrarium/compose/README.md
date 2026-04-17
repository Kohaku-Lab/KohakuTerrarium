# compose/

Agent composition algebra. Pythonic operators (`>>`, `&`, `|`, `*`) over
`AgentSession` and plain callables so pipelines read like code instead of
YAML. Zero framework coupling beyond `serving/agent_session` — everything
else is pure async combinators.

## Files

| File | Responsibility |
|------|----------------|
| `__init__.py` | Public API re-exports (`agent`, `factory`, `Runnable`, `Sequence`, `Product`, `Fallback`, `Retry`, `Router`, `Pure`, `FailsWhen`, `PipelineIterator`, `Effects`) |
| `core.py` | `BaseRunnable` (operator overloads) + every combinator in one file to avoid circular imports |
| `agent.py` | `AgentRunnable` (persistent session) + `AgentFactory` (ephemeral), and the `agent()` / `factory()` convenience constructors |
| `effects.py` | `Effects` dataclass — optional cost/latency/reliability annotations; semiring-style composition (`sequential`, `parallel`) |

## Dependency direction

Imported by: user code only (examples, notebooks, application scripts).
Nothing inside the framework imports `compose/`.

Imports: `serving/agent_session` (`AgentSession`), `core/config_types`
(`AgentConfig`), `utils/logging`, and stdlib `asyncio` / `inspect`.

## Key entry points

- `agent(config_or_path)` — async constructor; returns a started `AgentRunnable`
- `factory(config_or_path)` — sync; returns a lazy `AgentFactory` (fresh agent per call)
- `BaseRunnable` — base class for custom combinators; provides operator overloads
- `Pure(fn)` — wrap any sync/async callable as a `Runnable`
- `BaseRunnable.iterate(initial)` — async-for loop that feeds output back as input

## Operators

| Op | Combinator | Semantics |
|----|------------|-----------|
| `a >> b` | `Sequence` | Run `a`, pipe output to `b`. Auto-wraps callables with `Pure`. Dict syntax builds a `Router`. |
| `a & b` | `Product` | Run concurrently (`asyncio.gather`), return tuple. |
| `a \| b` | `Fallback` | Try `a`; on `Exception`, run `b` with the original input. |
| `a * N` | `Retry` | Retry `a` up to `N` times on exception. |
| `p.map(fn)` | — | Post-process output. |
| `p.contramap(fn)` | — | Pre-process input. |
| `p.fails_when(pred)` | `FailsWhen` | Treat matching outputs as failure (triggers `\|` fallback). |
| `await p(x)` | — | Run the pipeline. |
| `p.iterate(x)` | `PipelineIterator` | Async iterate (supports `.feed(override)`). |

## Notes

- All combinators inherit from `BaseRunnable`, so nested structures get the
  same operators for free.
- `Sequence._flat` and `Product._flat` collapse adjacent same-kind
  combinators so `a >> b >> c` is a single 3-step Sequence, not nested.
- `AgentRunnable` reuses one `AgentSession` across calls — conversation
  history accumulates. Use `async with await agent(...)` for cleanup.
- `AgentFactory` spins up a fresh session per invocation — no carry-over,
  no cleanup needed.
- `Effects` is advisory only (cost / latency / reliability hints). The
  combinators don't currently read them; they exist for external planners.

## See also

- `../serving/agent_session.py` — the streaming chat wrapper `AgentRunnable` owns
- `docs/concepts/python-native/` — philosophy + examples of the algebra
- `examples/compose/` — runnable pipeline demos
