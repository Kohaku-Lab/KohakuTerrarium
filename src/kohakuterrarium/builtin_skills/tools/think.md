---
name: think
description: Record a reasoning step (no-op); preserves thought in the event log
category: builtin
tags: [reasoning, planning, event-log]
---

# think

Record a deliberate reasoning step without any side effects. The tool is a
no-op: it does not touch the filesystem, network, or any external state. Its
only job is to emit a `tool_call` / `tool_result` pair into the session event
log so that the reasoning is:

- Preserved across context compaction (compact summaries are derived from the
  event log, not from raw stream text)
- Preserved across session resume (the `.kohakutr` file replays events)
- Visible to observers (TUI, web dashboard, `kt resume`) as a discrete step
  rather than blended into free-form streaming text

## WHEN TO USE

- Pausing to plan before a sequence of risky or expensive operations
- Working through a tricky diagnosis out loud, step by step
- Recording a decision or tradeoff so future you (after compaction) can still
  see the reasoning
- Splitting a long chain of thought into named checkpoints so a reviewer can
  follow the agent's thinking
- Keeping structured reasoning separate from user-visible output, especially
  in agents where the controller is wired to `output_to: external`

## WHEN NOT TO USE

- As a substitute for actually doing the work -- a `think` call that is never
  followed by concrete tool calls is noise
- For short in-line asides that would fit naturally in streaming text
- For information you want the user to see -- `think` output is not routed to
  external outputs; it only lives in the event log

## HOW TO USE

```
tool call: think(
Plain text containing the full thought.
Can span multiple lines.
)
```

The body becomes the `thought` argument. Passing `thought=...` explicitly also
works.

## Arguments

| Arg | Type | Description |
|-----|------|-------------|
| thought | content | The reasoning to record (required; falls back to the body) |

## Examples

Plan before a multi-step refactor:

```
tool call: think(
Plan:
1. Grep for all call sites of parse_config
2. Confirm the signature change is backward compatible
3. Update callers in two passes: tests first, then src
4. Run the full suite before committing
)
```

Record a tradeoff:

```
tool call: think(
Picking recursion over iteration here -- the tree depth is bounded at ~8
in practice, so stack overflow is not a concern, and the recursive form
mirrors the AST structure 1:1. Iterative would need an explicit work stack.
)
```

Checkpoint partway through a long task:

```
tool call: think(
Progress so far: migrations reverted cleanly, schema is back on v12.
Next: rerun the failing integration test to confirm the fix.
)
```

## Output Format

The tool echoes the thought back as its result. The result is otherwise
ignored by the framework -- its purpose is event-log preservation, not
downstream data flow.

## LIMITATIONS

- Pure no-op: does not change any state, does not call any LLM, does not
  contact the network
- Not routed to external outputs -- do not use `think` to communicate with
  the user
- If the `thought` is empty, the tool returns an error (prevents accidental
  empty-call spam)

## TIPS

- Prefer one `think` per coherent reasoning step; many tiny thinks fragment
  the event log
- Lead with a short label (`Plan:`, `Tradeoff:`, `Progress:`) so the event
  log is scannable
- Combine with `scratchpad` when the plan needs to be retrieved later --
  `think` writes to the log, `scratchpad` writes to working memory
