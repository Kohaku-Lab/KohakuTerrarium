---
name: delete_trigger
description: 'Stop and remove an installed trigger by id. Not for cancelling a running job - use stop_task.'
category: builtin
tags: [triggers]
---

# delete_trigger

Removes a trigger previously installed by `add_timer`, `add_schedule`, or a
channel watcher.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| trigger_id | string | yes | Id returned when the trigger was installed |

## Behavior

- The trigger stops firing immediately and is not restored on resume.
- An unknown id is an error, not a silent success.
