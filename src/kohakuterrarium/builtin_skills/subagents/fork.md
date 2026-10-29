---
name: fork
description: 'Run a side task on a snapshot of this conversation, read-only. Dispatch it to check, inspect or look something up in parallel while you continue.'
category: subagent
tags: [context, inspection, side-conversation]
---

# fork

A default-on, read-only helper with a detached snapshot of parent context.
Unlike `explore` and `critic`, it can use the conversation already in progress.
The parent retains planning and decisions.

## Task shape

Supply the side question and its scope. Inherited context is background, not a
request to continue unfinished work. The child's current tool inventory wins
over inventories mentioned in the inherited context.

## Returns

A direct answer with evidence and explicit uncertainty or missing context.
Normal dispatch returns to the controller; `/btw` answers only the user.

## Limits

- Only `read`, `glob`, `grep`, `tree`. No writes, shell, web, graph coordination,
  or recursive delegation.
- Inherited model, isolated provider required. Never reuse the parent provider.
- Soft/hard budgets: 8/12 model calls, 24/40 tool calls. Stop expanding at soft;
  hard blocks tools but allows text reporting. Absolute cap: 14 calls.
- Timeout: 600 seconds. No automatic compaction or shared parent iteration pool.
- Report partial findings rather than inventing completion.

## Reference

### Configuration and provider isolation

`fork_enabled: false` disables context forks and rejects `/btw`. An explicit
`subagents:` entry named `fork` overrides the implicit preset when enabled.
The inherited-model provider must return a distinct instance from `fork()`;
providers unable to do so are rejected, never silently reused.

### Parent authorization policies

Only opted-in pre-tool authorization guards are inherited live, not parent
budget accounting, context hooks, or lifecycle hooks. Builtin `sandbox` and
`permgate` opt in. New approvals granted in a fork stay child-private. Custom
plugins use `inherit_fork_policy` and `fork_tool_policy()` for validation-only
guards; inherited guards cannot rewrite arguments. Parent policy identity or
option changes during async approval block the invocation for retry. This is a
known-tool authorization contract, not an OS sandbox or automatic containment
of arbitrary custom tool code.

### User-facing side conversations

`/btw QUESTION` starts a user-audience side run. Neither the question nor answer
becomes parent LLM input, and completion does not wake the parent controller.

- `/btw --continue JOB_ID QUESTION`: follow up after completion or stopping.
- `/btw --open JOB_ID`: reopen the transcript, hiding the inherited parent prefix.
- `/btw --stop JOB_ID`: stop that side job only.

With session persistence, follow-ups retain the same run and job ID, source
provenance, and saved inherited context. The visible transcript hides that prefix;
the saved context still contains it. A continuation uses the side transcript,
not a new parent snapshot. The existing `/fork` session-copy command is unrelated
and unchanged.
