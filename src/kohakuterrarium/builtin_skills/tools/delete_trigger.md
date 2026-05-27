---
name: delete_trigger
description: Stop and remove a previously-installed trigger (timer / schedule / channel watcher) by id
category: builtin
tags: [trigger, lifecycle]
license: internal
---

# delete_trigger

Stop and remove a trigger that was installed via `add_timer`,
`add_schedule`, `watch_channel`, or any other trigger-creating tool /
API. Pair with the creation tools — the `trigger_id` they return is
the only input.

`stop_task` cannot stop triggers: triggers live in
`agent.trigger_manager`, not in the executor / sub-agent manager that
`stop_task` knows about. Use `delete_trigger` for triggers and
`stop_task` for running tools / sub-agents.

## Arguments

| Arg | Type | Description |
| --- | --- | --- |
| trigger_id | string | Trigger id returned by `add_timer` / `add_schedule` / `watch_channel`, or any id shown by the `/triggers` listing. Required. |

## Returns

| State | Output |
| --- | --- |
| Removed | `Removed trigger: <id>` (exit_code=0) |
| Unknown id | `Trigger not found: <id>` (exit_code=1, surfaced as `error`) |
| Missing context | `Agent context required` (exit_code=1) |

## Examples

Remove a previously-installed timer:

```
##tool##
{"name": "delete_trigger", "args": {"trigger_id": "trigger_abc12345"}}
##tool##
```

Remove a scheduled job named `daily-summary`:

```
##tool##
{"name": "delete_trigger", "args": {"trigger_id": "daily-summary"}}
##tool##
```

## Notes

- Idempotent at the agent layer: removing the same id twice surfaces
  `Trigger not found` on the second call; nothing destructive happens.
- The trigger's running task (if any) is cancelled cleanly via
  `trigger_manager.remove`, then `trigger.stop()` is invoked and the
  state row is dropped.
- If you only want to PAUSE a trigger temporarily, this tool is the
  wrong choice — there is no resume counterpart. Re-install the
  trigger with the same `name` argument if you need to bring it back.
