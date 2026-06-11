---
title: Compose algebra
summary: Stitch agents and async callables together in plain Python with sequence / parallel / fallback / retry operators.
tags:
  - guides
  - python
  - composition
---

# Composition

For readers who want multi-agent choreography from plain Python without building a terrarium.

The compose algebra treats agents and async callables as composable units. Four operators (`>>`, `&`, `|`, `*`) cover sequence, parallel, fallback, and retry. Everything returns a `BaseRunnable` you can keep composing.

Concept primer: [composition algebra](../concepts/python-native/composition-algebra.md), [agent as a Python object](../concepts/python-native/agent-as-python-object.md).

Use this guide when you want a loop outside the creature: writer ↔ reviewer until approved, parallel ensembles, cheap-to-expensive fallback chains. For horizontal multi-agent systems with shared channels, use a [Terrarium](terrariums.md).

## Operators

| Op | Meaning |
|---|---|
| `a >> b` | Sequence: `b(a(x))`. Auto-flattens. A dict on the right side becomes a `Router`. |
| `a & b` | Parallel: run both concurrently, return a **tuple** of results. On the first failure the surviving siblings are cancelled and awaited before the exception propagates. |
| `a \| b` | Fallback: if `a` raises, run `b` with the original input. If `b` also fails, `a`'s exception is chained as `__cause__`. |
| `a * N` | Retry: up to `N` attempts on exception (immediate, no delay). |

Precedence follows Python's operators: `*` binds tightest, then `>>`,
then `&`, then `|`. So `a >> b & c` is `(a >> b) & c`, and
`a & b | c` is `(a & b) | c`. Parenthesize when in doubt.

Combinators and methods:

- `Pure(fn)` / `pure(fn)`: wrap a plain sync or async callable.
- `.retry(max_attempts, *, backoff=0.0, max_backoff=30.0)`: like `* N`
  but with exponential backoff: sleep `backoff` seconds after the first
  failure, doubling per attempt, capped at `max_backoff`.
- `.map(fn)`: post-transform output (`self >> pure(fn)`).
- `.contramap(fn)`: pre-transform input (`pure(fn) >> self`).
- `.fails_when(pred)`: raise `ValueError` when the predicate matches
  the output (composes with `|`).
- `pipeline.iterate(initial_input)`: async iterator that feeds each
  output back in as the next input; `it.feed(value)` overrides the
  next input.

## `agent` vs `factory`

Two agent wrappers, both taking the same keywords:

```python
await agent(config, *, engine=None, pwd=None, llm=None)   # -> AgentRunnable
factory(config, *, engine=None, pwd=None, llm=None)       # -> AgentFactory
```

- `config`: an `AgentConfig`, a filesystem path, or an
  `@pkg/creatures/<name>` reference.
- `engine`: a shared `Terrarium` to spawn into. When omitted, each
  wrapper stands up a private engine that is torn down with the
  runnable; pass a shared engine to amortize startup across many
  compose agents (closing the runnable then only removes its creature,
  never your engine).
- `pwd`: working directory for the creature (no global chdir).
- `llm`: profile name, `LLMProfile`, or provider instance; the same
  grammar as `Agent.build` / `Terrarium.add_creature`.

`agent(...)` is **persistent**: starts immediately, conversation
accumulates across calls, must be closed (use `async with`).
`factory(...)` is **per-call**: a fresh agent for each invocation, no
state carry-over, no lifecycle to manage.

```python
from kohakuterrarium.compose import agent, factory

async with await agent("@kt-biome/creatures/swe", llm="fast") as swe:
    r1 = await swe("Read the repo.")
    r2 = await swe("Now fix the auth bug.")   # same conversation

coder = factory(some_config)
r1 = await coder("Task 1")                    # fresh agent
r2 = await coder("Task 2")                    # another fresh agent
```

Construction is strict: a bad path raises `ConfigNotFoundError`, an
uninstalled package raises `PackageNotInstalledError`, and a bad `llm`
selector raises `LLMNotConfiguredError`, at `agent()` / first
`factory` call time, not as an empty reply later.

## Writer ↔ reviewer loop

Iterate a two-agent pipeline until the reviewer approves:

```python
import asyncio
from kohakuterrarium.compose import agent
from kohakuterrarium.core.config import load_agent_config

def make(name, prompt):
    c = load_agent_config("@kt-biome/creatures/general")
    c.name, c.system_prompt = name, prompt
    c.tools, c.subagents = [], []
    return c

async def main():
    async with await agent(make("writer", "You are a writer.")) as writer, \
               await agent(make("reviewer", "Strict reviewer. Say APPROVED when good.")) as reviewer:

        pipeline = writer >> (lambda text: f"Review this:\n{text}") >> reviewer

        async for feedback in pipeline.iterate("Write a haiku about coding."):
            print(f"Reviewer: {feedback[:120]}")
            if "APPROVED" in feedback:
                break

asyncio.run(main())
```

`.iterate()` feeds the pipeline's output back in as the next input, producing an async stream you loop with native `async for`.

## Parallel ensemble with pick-best

Run three agents in parallel, keep the longest answer:

```python
from kohakuterrarium.compose import factory

fast = factory(make("fast", "Answer concisely."))
deep = factory(make("deep", "Answer thoroughly."))
creative = factory(make("creative", "Answer imaginatively."))

ensemble = (fast & deep & creative) >> (lambda results: max(results, key=len))
best = await ensemble("What is recursion?")
```

All three run concurrently, so you pay the max latency, not the sum.
The product result is a tuple, in branch order. If one branch raises,
the others are cancelled (and awaited) before the exception propagates,
so no detached agents keep burning LLM turns.

## Retry + fallback chain

Try the expensive expert twice, then fall back to the cheap generalist:

```python
safe = (expert * 2) | generalist
result = await safe("Explain JSON-RPC.")
```

With backoff between attempts:

```python
safe = expert.retry(3, backoff=2.0, max_backoff=30.0) | generalist
```

Combine with an error-predicate fallback:

```python
cheap = fast.fails_when(lambda r: len(r) < 50)
pipeline = cheap | deep            # if fast returns < 50 chars, try deep
```

When the whole chain fails, the exception you catch carries the
primary failure as `__cause__`, so debugging keeps the original error.

## Routing

A dict on the RHS of `>>` becomes a `Router`:

```python
router = classifier >> {
    "code":   coder,
    "math":   solver,
    "prose":  writer,
    "_default": generalist,       # optional catch-all
}
```

The router keys on the upstream output: a 2-tuple `(key, payload)`
routes `payload` to the branch named `key`; any other value is used as
both the key and the payload. With no matching branch and no
`_default`, it raises `KeyError`.

## Mixing agents and functions

Plain callables auto-wrap with `Pure`:

```python
pipeline = (
    writer
    >> str.strip                      # plain callable on the output
    >> (lambda t: f"Review:\n{t}")    # lambda
    >> reviewer
    >> json.loads                     # parse reviewer's JSON response
)
```

Sync and async callables both work; async is awaited automatically.

## When to use terrariums instead

Pick a terrarium when:

- Creatures need to run *continuously* and react to messages on their own schedule.
- You need hot-plug creatures or external observability.
- Multiple creatures share a workspace (scratchpad, channels) and need `Environment` isolation.

Pick composition when:

- Your application is the orchestrator and you call agents on demand.
- The pipeline is short-lived (request-scoped, not long-running).
- You want native Python control flow (`for`, `if`, `try`, `gather`).

The two mix well: pass `engine=` to `agent()` / `factory()` and your
compose pipeline spawns its creatures into the same engine your
long-running terrarium uses.

## Troubleshooting

- **Persistent `agent()` raises on re-use after close.** It's an async
  context manager; keep all calls inside `async with`.
- **Pipeline returns a tuple unexpectedly.** You used `&` somewhere;
  the result is a tuple. Add `>> (lambda results: ...)` to collapse.
- **Retry doesn't retry.** `* N` triggers on exceptions. Use
  `.fails_when(pred)` to convert a bad-looking success into an
  exception.
- **Type mismatch between steps.** Each step's output is the next
  step's input. Insert a `pure` function (or lambda) to adapt.

## See also

- [Programmatic Usage](programmatic-usage.md): the underlying `Agent` / `Terrarium` / `Creature` API.
- [Concepts / composition algebra](../concepts/python-native/composition-algebra.md): design rationale.
- [Reference / Python API](../reference/python.md#compose): exports and operator signatures.
- [`examples/code/`](../../../examples/code/): `review_loop.py`, `ensemble_voting.py`, `debate_arena.py`, `smart_router.py`, `pipeline_transforms.py`.
