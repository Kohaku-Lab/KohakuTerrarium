---
name: worker
description: Implement a scoped code change end to end. Use for edits you can specify completely up front. Not for open questions - use explore or plan.
category: subagent
tags: [implementation]
---

# worker

Carries out a specified change: reads what it needs, edits, and verifies.

## Task shape

Give the complete specification: what to change, where, what done looks like,
and how to check it. A task that requires a decision it cannot make will
either stall or guess.

## Returns

A summary of what changed, the files touched, and what verification ran.

## Limits

- Read-write, including shell access.
- Scope is what you wrote. It will not widen the change on its own.
