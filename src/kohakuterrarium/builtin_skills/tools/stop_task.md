---
name: stop_task
description: 'Cancel a running background job by id. Use it when its result is no longer needed or it has run too long.'
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
