---
name: stop_task
description: Cancel a running background task (tool or sub-agent) by job ID
category: builtin
tags: [execution, background, cancel]
---

# stop_task

Cancel a running background tool or sub-agent.

## Arguments

| Arg    | Type   | Description                                                               |
| ------ | ------ | ------------------------------------------------------------------------- |
| job_id | string | Job ID to cancel (required). Use the `jobs` command to list running jobs. |

## Behavior

- Cancels the asyncio task associated with the job.
- The job status changes to CANCELLED.
- Checks both background tools and background sub-agents.
- If the job is already done, reports its current status instead of erroring.
- Does not affect direct (blocking) tools, only background tasks.

## WHEN TO USE

- Cancel a long-running sub-agent (e.g., explore taking too long)
- Cancel a background tool (e.g., terrarium_observe you no longer need)
- Stop a trigger by its trigger ID

## Output

Returns confirmation that the task was cancelled, or its current status
if already completed.

## TIPS

- Use the `jobs` command to see running job IDs before cancelling.
- Stash the returned trigger IDs from `add_timer`, `watch_channel`, and `add_schedule` in your scratchpad if you may need to stop them later.
