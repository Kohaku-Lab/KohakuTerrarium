# Stream parser

## The problem this solves

When an LLM emits a tool call mid-stream, when does the framework
start running the tool?

Two choices:

1. **Wait for the turn to end.** Collect all tool calls; dispatch in
   one batch; get results; maybe another LLM call.
2. **Dispatch the moment the block closes.** Every tool runs in
   parallel with the rest of the LLM's output; by the time the LLM
   finishes speaking, some tools are already done.

Option 2 is dramatically more responsive — especially for
long-streaming turns with multiple tool calls — and it is what the
framework does.

## Options considered

- **Post-turn dispatch.** Simpler, but wastes the streaming window;
  tools are sequential on the LLM.
- **Speculative dispatch.** Start tools while the LLM streams; cancel
  if the block turns out to be incomplete. Too error-prone.
- **Deterministic state-machine dispatch at block close.** What we
  actually do. Start the tool exactly when its textual block finishes
  parsing; never on partial input.

## What we actually do

The output stream from the LLM is fed chunk-by-chunk into a parser
state machine. The parser tracks three kinds of nested block, using
the currently configured `tool_format`:

- **Tool calls** — e.g. in bracket (default) `[/bash]@@command=ls\n[bash/]`; in XML `<bash command="ls"></bash>`; in native, the LLM provider's own function-calling envelope.
- **Sub-agent dispatches** — same format family, using the agent tag.
- **Framework commands** — `info`, `jobs`, `wait` (and, in the parser's DEFAULT_COMMANDS set, `read_job`). These share the same bracket/XML framing as tool calls. See [modules/tool — formats](../modules/tool.md) and [modules/plugin](../modules/plugin.md) for how the format is configured.

When a block closes, the parser emits an event on its output
generator. The controller reacts:

- `TextEvent` → stream to output.
- `ToolCallEvent` → `Executor.submit_from_event(event, is_direct=True)`
  → `asyncio.create_task(tool.execute(...))`. Returns immediately.
- `SubAgentCallEvent` → similar, via `SubAgentManager.spawn`.
- `CommandEvent` → execute inline (read a job's output, load docs, etc.);
  these are fast and deterministic.

At end-of-stream, the controller awaits any `direct` jobs started
during the stream, collects their results as `tool_complete` events,
and feeds them back to the LLM for the next turn.

## Invariants preserved

- **Exactly one dispatch per closed block.** Partial blocks never
  run.
- **Multiple tools in one turn run in parallel.** `gather` over their
  tasks, not a sequence.
- **LLM streaming is not blocked on tool execution.** The LLM keeps
  talking; the tools run beside it.
- **Background tools do not hold the turn open.** A tool marked
  background returns its job id as a placeholder; the controller moves
  on; the result is delivered as a later event.

## Where it lives in the code

- `src/kohakuterrarium/parsing/` — the parser state machine, one
  module per tool-format variant (bracket, XML, native).
- `src/kohakuterrarium/core/controller.py` — consumes parser events.
- `src/kohakuterrarium/core/executor.py` — wraps tool runs as tasks.
- `src/kohakuterrarium/core/agent_tools.py` — submit-from-event path
  that ties parser output to executor.

## See also

- [Composing an agent](../foundations/composing-an-agent.md) — the
  turn-level picture that this page zooms into.
- [Tool](../modules/tool.md) — execution modes (direct / background /
  stateful).
