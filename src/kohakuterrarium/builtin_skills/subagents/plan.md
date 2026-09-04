---
name: plan
description: Produce an implementation plan, read-only. Use before a change whose shape is unclear. Not for carrying the plan out - use worker.
category: subagent
tags: [planning]
---

# plan

Reads enough of the codebase to propose an ordered implementation plan.

## Task shape

State the goal, the constraints that matter, and anything already decided.
Plans are only as good as the constraints they are given.

## Returns

An ordered plan with the files each step touches, plus assumptions and risks.
No edits.

## Limits

- Read-only.
- Proposes; it does not decide. Conflicting requirements come back as options.
