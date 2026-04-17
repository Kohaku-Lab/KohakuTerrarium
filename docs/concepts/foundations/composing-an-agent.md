# Composing an agent

[What is an agent](what-is-an-agent.md) introduced six modules. This
page shows how they actually fit together into a running creature.

## The single envelope: `TriggerEvent`

Everything external to the controller arrives as a `TriggerEvent`:

- A user typing → `TriggerEvent(type="user_input", content=...)`
- A timer firing → `TriggerEvent(type="timer", ...)`
- A tool finishing → `TriggerEvent(type="tool_complete", job_id=..., content=...)`
- A sub-agent returning → `TriggerEvent(type="subagent_output", ...)`
- A channel message → `TriggerEvent(type="channel_message", ...)`
- A context injection → `TriggerEvent(type="context_update", ...)`
- An error → `TriggerEvent(type="error", stackable=False, ...)`

One envelope for everything. The controller does not need a different
code path for every source; it only asks "what events do I have this
turn?" That is the whole architectural simplification.

## The event queue

```
        +-----------+  +---------+  +-----------+  +----------+
        | input.get |  | trigger |  | tool done |  | sub done |
        +-----+-----+  +----+----+  +-----+-----+  +-----+----+
              \             \             /             /
               \             \           /             /
                +------------ event queue ------------+
                              |
                              v
                        +------------+
                        | Controller |
                        +------------+
```

Every wake-up source pushes events onto a single queue. Multiple
events that arrive "at the same time" can be **stackable** — the
controller merges them into one turn's user message, so a burst of
activity does not translate into a burst of LLM calls.

Non-stackable events (errors, priority signals) break the batch. They
are processed in their own turn.

## One turn, step by step

```
  +---- collect events from queue (batch stackable)
  |
  |   +- build turn context (job status + event content, multimodal-aware)
  |
  |   +- call LLM in streaming mode
  |
  |       during stream:
  |         - text chunks -> output
  |         - tool blocks detected -> asyncio.create_task(run tool)
  |         - sub-agent blocks detected -> asyncio.create_task(run sub)
  |         - framework commands (info, jobs, wait) -> inline
  |
  |   +- await direct-mode tools + sub-agents
  |
  |   +- feed their results back as new events
  |
  |   +- decide: loop or break
  +---- back to event queue
```

A few invariants worth noticing:

1. **Tools start immediately.** The moment a tool block finishes
   parsing — long before the LLM stops speaking — we dispatch it as a
   new task. Multiple tools in the same turn run in parallel. This is
   covered in [impl-notes/stream-parser](../impl-notes/stream-parser.md).
2. **Only one LLM turn at a time.** A per-creature lock guarantees the
   controller is never re-entered. Triggers fire freely, but they
   queue.
3. **Direct vs background vs stateful** are dispatch modes, not
   separate systems. See [modules/tool](../modules/tool.md).

## Where the other modules sit

- **Input** pushes events into the queue; nothing else changes about it.
- **Triggers** each own a background task that pushes events into the
  queue when their condition fires.
- **Tools and sub-agents** run via the executor / sub-agent manager.
  Their completions become new events — the loop closes.
- **Output** consumes the controller's text and tool-activity stream
  and routes it to one or more sinks (stdout, TTS, Discord, whatever
  you configured).

## What the concept docs do and don't cover at this level

This page is the architectural overview. For each module, the deep
story is in its own module doc:

- [Controller](../modules/controller.md) — the loop
- [Input](../modules/input.md) — first trigger
- [Trigger](../modules/trigger.md) — world-to-agent wake-up
- [Output](../modules/output.md) — agent-to-world
- [Tool](../modules/tool.md) — agent's hands
- [Sub-agent](../modules/sub-agent.md) — context-scoped delegate

Two cross-cutting pieces belong in their own section rather than on
top of a single module:

- [Channel](../modules/channel.md) — the communication substrate
  shared across tools, triggers, and terrariums.
- [Session and environment](../modules/session-and-environment.md) —
  the private-vs-shared state split.

## See also

- [Agent as a Python object](../python-native/agent-as-python-object.md)
  — how this same picture maps onto plain Python when you embed it.
- [impl-notes/stream-parser](../impl-notes/stream-parser.md) — why
  tools start before the LLM stops.
- [impl-notes/prompt-aggregation](../impl-notes/prompt-aggregation.md)
  — how the system prompt that drives this loop is built.
