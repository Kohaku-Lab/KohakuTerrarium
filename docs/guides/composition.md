# Composition

For readers who want multi-agent choreography from plain Python without building a terrarium.

The compose algebra treats agents and async callables as composable units. Four operators (`>>`, `&`, `|`, `*`) cover sequence, parallel, fallback, and retry. Everything returns a `BaseRunnable` you can keep composing.

Concept primer: [composition algebra](../concepts/python-native/composition-algebra.md), [agent as a Python object](../concepts/python-native/agent-as-python-object.md).

Use this guide when you want a loop outside the creature — writer ↔ reviewer until approved, parallel ensembles, cheap-to-expensive fallback chains. For horizontal multi-agent systems with shared channels, use a [Terrarium](terrariums.md).

## Operators

| Op | Meaning |
|---|---|
| `a >> b` | Sequence: `b(a(x))`. Auto-flattens. Dict on the right side becomes a `Router`. |
| `a & b` | Parallel: `asyncio.gather(a(x), b(x))`. Returns a list. |
| `a \| b` | Fallback: if `a` raises, try `b`. |
| `a * N` | Retry `a` up to `N` additional times on exception. |

Precedence: `*` > `|` > `&` > `>>`.

Combinators:

- `Pure(fn_or_value)` — wrap a plain callable.
- `.map(fn)` — post-transform output.
- `.contramap(fn)` — pre-transform input.
- `.fails_when(pred)` — raise when predicate matches (composes with `|`).
- `pipeline.iterate(stream)` — apply pipeline to each element of an async iterable.

## `agent` vs `factory`

Two agent wrappers:

- `agent(config_or_path)` — **persistent** agent (async context manager). Conversation context accumulates across calls. Good for one long interaction.
- `factory(config)` — **per-call** agent. Fresh agent for each invocation; no state carry-over. Good for stateless workers.

```python
from kohakuterrarium.compose import agent, factory

async with await agent("@kt-defaults/creatures/swe") as swe:
    r1 = await swe("Read the repo.")
    r2 = await swe("Now fix the auth bug.")   # same conversation

coder = factory(some_config)
r1 = await coder("Task 1")                    # fresh agent
r2 = await coder("Task 2")                    # another fresh agent
```

## Writer ↔ reviewer loop

Iterate a two-agent pipeline until the reviewer approves:

```python
import asyncio
from kohakuterrarium.compose import agent
from kohakuterrarium.core.config import load_agent_config

def make(name, prompt):
    c = load_agent_config("@kt-defaults/creatures/general")
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

`&` dispatches to `asyncio.gather`, so all three run concurrently and you pay the max latency, not the sum.

## Retry + fallback chain

Try the expensive expert twice, then fall back to the cheap generalist:

```python
safe = (expert * 2) | generalist
result = await safe("Explain JSON-RPC.")
```

Combine with error-predicate fallback:

```python
cheap = fast.fails_when(lambda r: len(r) < 50)
pipeline = cheap | deep            # if fast returns < 50 chars, try deep
```

## Routing

A dict on the RHS of `>>` becomes a `Router`:

```python
router = classifier >> {
    "code":   coder,
    "math":   solver,
    "prose":  writer,
}
```

The upstream step should emit a dict `{classifier_key: payload}`; the router picks the matching branch. Great for "classify then dispatch" patterns.

## Mixing agents and functions

Plain callables auto-wrap with `Pure`:

```python
pipeline = (
    writer
    >> str.strip                      # zero-arg callable on the output
    >> (lambda t: {"text": t})        # lambda
    >> reviewer
    >> json.loads                     # parse reviewer's JSON response
)
```

Sync and async callables both work; async is awaited automatically.

## Side-effect logging

```python
from kohakuterrarium.compose.effects import Effects

effects = Effects()
logged = effects.wrap(pipeline, on_call=lambda step, x, y: print(f"{step}: {x!r} -> {y!r}"))
result = await logged("input")
```

Useful for debugging pipeline flow without changing the pipeline.

## When to use terrariums instead

Pick a terrarium when:

- Creatures need to run *continuously* and react to messages on their own schedule.
- You need hot-plug creatures or external observability.
- Multiple creatures share a workspace (scratchpad, channels) and need `Environment` isolation.

Pick composition when:

- Your application is the orchestrator and you call agents on demand.
- The pipeline is short-lived (request-scoped, not long-running).
- You want native Python control flow (`for`, `if`, `try`, `gather`).

## Troubleshooting

- **Persistent `agent()` raises on re-use.** It's an async context manager — use it inside `async with`.
- **Pipeline returns a list unexpectedly.** You used `&` somewhere; the result is a list. Add `>> (lambda results: ...)` to collapse.
- **Retry doesn't retry.** `* N` triggers on exceptions. Use `.fails_when(pred)` to convert a bad-looking success into an exception.
- **Type mismatch between steps.** Each step's output is the next step's input. Insert a `Pure` function (or lambda) to adapt.

## See also

- [Programmatic Usage](programmatic-usage.md) — the underlying `Agent` / `AgentSession` API.
- [Concepts / composition algebra](../concepts/python-native/composition-algebra.md) — design rationale.
- [Reference / Python API](../reference/python.md) — `compose.core`, `compose.agent`, operator signatures.
- [examples/code/](../../examples/code/) — `review_loop.py`, `ensemble_voting.py`, `debate_arena.py`, `smart_router.py`, `pipeline_transforms.py`.
