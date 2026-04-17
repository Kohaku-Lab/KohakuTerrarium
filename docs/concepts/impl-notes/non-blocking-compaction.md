# Non-blocking compaction

## The problem this solves

A creature that runs for hours accumulates conversation. Eventually
the prompt exceeds the model's context budget. The standard fix is
compaction: summarise old turns into a condensed note, keep recent
turns raw. But compaction is itself an LLM call — if the controller
is blocked while the summariser works, an ambient agent freezes for
tens of seconds while 50 k tokens get rewritten.

For a coding-agent-style creature that is tolerable. For a
monitoring or conversational creature it is a product defect.

## Options considered

- **Synchronous pause.** Stop the controller, summarise, resume.
  Simple, but produces long freezes.
- **Hand off to a separate agent.** Overkill for what is essentially
  "rewrite the old turns into a paragraph."
- **Background task + atomic splice.** Summarise in parallel with the
  controller; swap the conversation in between turns. This is what
  the framework does.

## What we actually do

The conversation is split conceptually into two zones:

```
  [ ----- compact zone ----- ][ --- live zone (keep_recent_turns) --- ]
           eligible                            raw, never summarised
```

Flow:

1. After every turn, the compact manager checks
   `prompt_tokens >= threshold * max_tokens`.
2. If yes, it emits a `compact_start` activity event and spawns a
   background `asyncio.Task`.
3. The task:
   - snapshots the compact zone,
   - runs the summariser LLM (the main controller's LLM, or a
     dedicated cheaper `compact_model` if configured),
   - produces a summary that preserves decisions, file paths, error
     strings, and other high-signal tokens verbatim.
4. Meanwhile the controller keeps processing events — tools run,
   sub-agents spawn, the user can keep typing.
5. When the summary is ready, the manager waits for the current turn
   to end, then **atomically** rewrites the conversation:
   - old compact zone replaced by `{system prompt, prior summaries,
     new summary, live zone raw messages}`,
   - emits a `compact_complete` event.

## Invariants preserved

- **No mid-turn swap.** Conversation is only replaced between turns,
  so the controller never sees messages vanish during an LLM call.
- **Live zone never shrinks during compaction.** New turns accrete
  onto the live zone while the summary is in flight; the splice
  accounts for that.
- **Summaries stack.** The next compaction produces a summary that
  includes the previous summary, so history degrades gracefully rather
  than getting lost.
- **Opt-out per creature.** `compact.enabled: false` disables it
  entirely.

## Where it lives in the code

- `src/kohakuterrarium/core/compact.py` — `CompactManager` with the
  start/pending/done state machine.
- `src/kohakuterrarium/core/agent.py` — `_init_compact_manager()` wires
  the manager into the agent on `start()`.
- `src/kohakuterrarium/core/controller.py` — the post-turn hook that
  asks the manager to consider compaction.
- `src/kohakuterrarium/builtins/user_commands/compact.py` — `/compact`
  for manual trigger.

## See also

- [Memory and compaction](../modules/memory-and-compaction.md) — the
  conceptual picture.
- [reference/configuration.md — `compact`](../../reference/configuration.md) —
  per-creature config knobs.
