# Controller

## What it is

The **controller** is the reasoning loop of a creature. It pulls events
off a queue, asks the LLM to respond, dispatches the tool and
sub-agent calls that come back, collects the results, and decides
whether to loop.

It is *not* "the brain." The brain is the LLM. The controller is the
thin code that makes the LLM actually do work over time.

## Why it exists

The LLM is stateless: you feed it messages, it produces more messages.
An agent is stateful: it has tools running, sub-agents spawned, events
arriving, turns accumulating. Something has to bridge the two.

Without a controller, a creature would either collapse into a single
LLM round-trip (chat bot) or require bespoke glue for each agent
design. The controller is *the* piece that makes "LLM + loop + tools"
into a reusable substrate instead of glue.

## How we define it

The controller's contract, reduced:

```
loop:
    events = collect from queue (batch stackable, break on non-stackable)
    context = build turn input from events
    stream = LLM.chat(messages + context)
    for chunk in stream:
        output text chunks
        dispatch parsed tool / sub-agent / framework-command blocks
    wait for direct-mode tools and sub-agents
    feed their results back as new events
    loop or break
```

Three design choices worth naming:

- **Single event lock.** Only one LLM turn runs at a time per creature.
  Triggers fire freely, but they enqueue rather than interrupt.
- **Stackable batching.** Bursts of similar events (for example two
  tools completing in the same tick) merge into one turn.
- **Tools dispatched mid-stream.** The controller does not wait for the
  LLM to finish speaking before firing a tool. See
  [impl-notes/stream-parser](../impl-notes/stream-parser.md).

## How we implement it

The main class is `Controller` (`core/controller.py`). It owns an
`asyncio.Queue` for events, a parser state machine for the LLM's
output stream, and a reference to the creature's `Registry` (tools),
`SubAgentManager`, `Executor`, and `OutputRouter`.

Key invariants:

- The `_processing_lock` is held for the entire "collect → stream →
  dispatch → await → loop" sequence.
- Non-stackable events (errors, priority signals) break the current
  batch and get their own turn.
- The controller never calls tools directly; it hands them to the
  `Executor` which spawns `asyncio.Task`s.

## What you can therefore do

- **Swap LLM mid-session.** The `/model` user command or the
  `switch_model` API swaps the LLM provider in place. The controller
  does not care which provider it is talking to.
- **Dynamic system prompt.** `update_system_prompt(...)` appends or
  replaces the prompt before the next turn; the controller picks it up
  automatically.
- **Regenerate a turn.** `regenerate_last_response()` tells the
  controller to rerun the last LLM call with current state.
- **Inject events from anywhere.** Because everything flows through the
  event queue, a plugin, a tool, or external Python code can call
  `agent.inject_event(...)` and the controller will process it in
  order.

## Don't be bounded

A controller-less creature is nonsensical — you cannot have an agent
without a loop. But the loop's *shape* is negotiable. Plugin hooks
(`pre_llm_call`, `post_llm_call`, `pre_tool_execute`, …) let you rewrite
every step of the loop from outside, without touching the controller
class. See [plugin](plugin.md).

## See also

- [Composing an agent](../foundations/composing-an-agent.md) — where the controller sits.
- [impl-notes/stream-parser](../impl-notes/stream-parser.md) — why tools start before the LLM stops.
- [impl-notes/prompt-aggregation](../impl-notes/prompt-aggregation.md) — what prompt the controller is driving.
- [reference/python.md — Agent, Controller](../../reference/python.md) — signatures.
