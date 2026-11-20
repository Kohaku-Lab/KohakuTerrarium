---
title: Bounded shutdown
summary: How Agent.stop bounds caller waiting with one shared deadline, keeps ordinary cancellation idempotent, and reports forced or abandoned work honestly.
tags:
  - concepts
  - impl-notes
  - lifecycle
---

# Bounded shutdown

## The problem this solves

Cancellation settlement (PR #583) made ordinary cancellation idempotent:
an interrupt sends one `Task.cancel()`, and a later `stop()` joins that
request instead of interrupting cleanup again. That protects tool
history, but a tool or cleanup coroutine can suppress cancellation or
wait forever. Then an unbounded `stop()` never returns.

The older behaviour sent a second `cancel()` during shutdown. That
sometimes broke a stuck cleanup, but it could also lose results and
interrupt resource release, and it still could not stop uncooperative
work. Repeated `Task.cancel()` is not a termination guarantee.

## Options considered

- **Always cancel twice.** Rejected: it silently turns "please stop"
  into "force stop" and throws away settled results.
- **`asyncio.wait_for()` around each stage.** Rejected: `wait_for`
  cancels the inner awaitable and then waits for that cancellation to
  finish, so it can run past its timeout. Per-stage timeouts also add
  up to an unbounded total.
- **One shared deadline, explicit escalation (chosen).**

## What we do

`await agent.stop(grace=None, force=False)` returns a `ShutdownReport`.

- **No `grace`:** the shutdown waits for cooperative settlement
  indefinitely. This is the previous behaviour and stays the default.
- **`grace=<seconds>`:** sets one monotonic deadline shared by every
  stage (plugins, processing task, turn lock, consumer, MCP, tool jobs,
  sub-agents, interrupt finalizers, triggers, input, compaction,
  outputs, providers). A later request may move it earlier but never
  resets or extends it. Joins use `asyncio.wait(timeout=...)`, which
  returns at its timeout without cancelling anything.
- **Deadline expiry:** unsettled work is *abandoned*, not cancelled
  again. Each later cleanup stage is still started so resources can be
  released, but it is not awaited. The call raises
  `ShutdownIncompleteError` (a `TimeoutError`) carrying the report.
- **`force=True`:** explicit, destructive escalation. At the deadline,
  work that is still running (not cleanup stages) receives one more
  `cancel()` and gets one extra window (`FORCE_SETTLE_SECONDS`, 1 s).
  `force` needs a bounded deadline. A forced shutdown is never reported
  as settled, even if every task exited.
- **Repeated or concurrent calls** join the shutdown already in
  progress. An ordinary repeat never escalates. `stop(grace=g)` from a
  second caller tightens an unbounded in-flight shutdown.
  `stop(force=True)` after an incomplete shutdown escalates the
  abandoned work.

### Report fields

| Field | Meaning |
|-------|---------|
| `settled` | Every tracked task exited through ordinary cooperative cancellation. |
| `terminated` | Every tracked task has exited, however it got there. |
| `timed_out` / `expired_stage` | The deadline passed, and at which stage. |
| `forced` | Escalated cancellation was used. |
| `abandoned` | Labels (`processing`, `job:<id>`, `subagent:<id>`, `finalizer:<name>`, `stage:<name>`) the shutdown stopped waiting for. |
| `errors` | Settlement and provider-close failures. |

`settled=False, terminated=True` means a forced shutdown completed.
`terminated=False` means the call returned while work may still be
running. A bounded return does not mean the work has ended.

### Late writes

When the deadline first expires, the agent revokes its sinks before
returning: `OutputRouter.revoke()` drops later events (which covers
session output, a router secondary), and `Conversation.seal()` makes
every mutation raise `ConversationSealedError`. Abandoned work that
completes later cannot change history or outputs. Its failure is logged.

`start()` refuses to restart (`ConflictError`) while abandoned work is
alive. Once it has all exited, `start()` reopens the sinks.

### Error precedence

1. `ShutdownIncompleteError` whenever work was forced or abandoned; the
   first settlement or provider error is its `__cause__`.
2. Otherwise the interrupted-job settlement failure (`RuntimeError`).
3. Otherwise the first provider-close failure. A settled stop that
   failed only this way can be retried and re-closes failed providers.

## Invariants preserved

- Ordinary cancellation stays idempotent; only `force=True` repeats it.
- Normal settlement keeps the actual outcomes and publishes each
  terminal once. Revocation happens only after a deadline expires.
- A timeout is never reported as a settled cancellation.

## Limits

The bound holds only while the event loop keeps running. A coroutine
that blocks the loop (synchronous I/O, a busy loop) cannot be
interrupted from inside the loop, and threads are never killed. Hard
termination of such work needs a subprocess boundary, which this
design does not add. Rollback of external side effects and
exactly-once effects are out of scope. A forced or abandoned shutdown
may lose output, skip cleanup, and leave side effects that already
happened.

## Where it lives in the code

- `src/kohakuterrarium/core/agent/shutdown.py`: `ShutdownDeadline`,
  `ShutdownState`, `ShutdownReport`.
- `src/kohakuterrarium/core/agent/lifecycle.py`: `stop()` and its stages.
- `src/kohakuterrarium/modules/output/router/router.py`: `revoke` / `restore`.
- `src/kohakuterrarium/core/conversation/conversation.py`: `seal` / `unseal`.
- `src/kohakuterrarium/modules/subagent/manager.py`: `request_cancel_all`.
