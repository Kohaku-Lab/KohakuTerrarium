# Composition algebra

## What it is

Once agents are Python values, you want to stitch them together. The
**compose algebra** is a small set of operators and combinators that
treat agents (and any async callable) as composable units:

- `a >> b` — sequence (output of `a` becomes input of `b`)
- `a & b` — parallel (run both, return `[result_a, result_b]`)
- `a | b` — fallback (if `a` raises, try `b`)
- `a * N` — retry (up to `N` extra attempts on failure)
- `pipeline.iterate(stream)` — apply the pipeline to each element of
  an async iterable; feed outputs back as inputs when looping is
  desired

Everything returns a `BaseRunnable` you can keep composing.

## Why it exists

The controller inside a creature is a loop. But sometimes you want a
loop *outside* the creature — writer ↔ reviewer iterating to
approval, parallel ensemble picking the best answer, retry-with-
fallback across providers. Doing this with bare `asyncio.gather` and
`try/except` works but clutters your call sites.

The operators are ergonomic sugar over asyncio. They do not introduce
a new execution model. They just make "compose two agents" read like
"add two numbers."

## How we define it

`BaseRunnable.run(input) -> Any` (async) is the protocol. Anything
that implements it is composable.

The operators:

- `__rshift__` wraps the pair in `Sequence` (auto-flattens nested
  sequences; a dict right-hand side becomes a `Router`).
- `__and__` wraps in `Product`; `run(x)` awaits `asyncio.gather` over
  all branches, broadcasting `x` as input.
- `__or__` wraps in `Fallback`; on exception, falls through.
- `__mul__` wraps in `Retry`; re-runs on exception up to N times.

Plus combinators:

- `Pure(value)` — wraps a plain value or callable; ignores input.
- `Router(routes)` — input `{key: value}` dispatches to the matching
  runnable.
- `.map(fn)` — pre-transform the input (`contramap`).
- `.contramap(fn)` — post-transform the output.
- `.fails_when(pred)` — raise if predicate matches; useful with `|`.

Agent factories:

- `agent(config)` — persistent agent wrapped in a runnable. Conversation
  context accumulates across calls.
- `factory(config)` — per-call agent. A fresh agent spawns for each
  invocation; no persistent state.

## How we implement it

`compose/core.py` holds the base protocol and combinator classes.
`compose/agent.py` wraps agents into runnables. `compose/effects.py`
is optional instrumentation for recording side-effects on a pipeline.

The agent-factory wrappers handle the lifecycle boilerplate — they
start / stop the underlying `Agent` on enter/exit, and forward input
via `inject_input` + output collection.

## A real example

```python
import asyncio
from kohakuterrarium.compose import agent, factory
from kohakuterrarium.core.config import load_agent_config

def make_agent(name, prompt):
    c = load_agent_config("@kt-defaults/creatures/general")
    c.name, c.system_prompt, c.tools, c.subagents = name, prompt, [], []
    return c

async def main():
    async with await agent(make_agent("writer", "You are a writer.")) as writer, \
               await agent(make_agent("reviewer", "You are a strict reviewer. Say APPROVED if good.")) as reviewer:

        pipeline = writer >> (lambda text: f"Review this:\n{text}") >> reviewer

        async for feedback in pipeline.iterate("Write a haiku about coding"):
            print(f"Reviewer: {feedback[:100]}")
            if "APPROVED" in feedback:
                break

    fast = factory(make_agent("fast", "Answer concisely."))
    deep = factory(make_agent("deep", "Answer thoroughly."))
    safe = (fast & deep) >> (lambda results: max(results, key=len))
    safe_with_retry = (safe * 2) | fast
    print(await safe_with_retry("What is recursion?"))

asyncio.run(main())
```

Two agents, persistent conversations, a feedback loop, a parallel
ensemble with fallback and retry — all in plain Python.

## What you can therefore do

- **Review loops.** Writer `>>` reviewer `.iterate(...)` until a
  predicate is satisfied. No new orchestration code.
- **Ensembles.** `(fast & deep) >> pick_best` — run two agents in
  parallel and combine results.
- **Fallback chains.** Try a cheap provider; on failure, fall back to
  a stronger one.
- **Retry over transient failures.** Wrap any runnable with `* N`.
- **Streaming pipelines.** `.iterate(async_generator)` processes each
  element through the full pipeline.

## Don't be bounded

Composition algebra is optional. Creature configs plus `AgentSession`
cover the majority of embedding use cases. The operators exist for
when you *do* want multi-agent choreography from plain Python without
a terrarium.

Status note: the algebra is useful but still evolving — the exact set
of operators may grow or simplify based on feedback. Prefer it for
internal pipelines, treat production uses as early-stable.

## See also

- [Agent as a Python object](agent-as-python-object.md) — the foundation this builds on.
- [Patterns](../patterns.md) — uses that mix the algebra with embedded agents.
- [guides/composition.md](../../guides/composition.md) — task-oriented usage.
- [reference/python.md — kohakuterrarium.compose](../../reference/python.md) — full API.
