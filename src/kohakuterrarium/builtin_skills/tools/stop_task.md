---
name: stop_task
description: Cancel a running background job by id. Use when its result is no longer needed.
category: builtin
tags: [jobs]
---

# stop_task

Cancels a background tool call or sub-agent run.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| job_id | string | yes | Job to cancel |

## Behavior

- Cancelling an already-finished job is a no-op, not an error.
- A cancelled job delivers no result, so nothing further arrives for it.
