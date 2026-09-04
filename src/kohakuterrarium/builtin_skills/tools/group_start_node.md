---
name: group_start_node
description: 'Start a stopped member of your group. Not for adding a new one - use group_add_node.'
category: builtin
tags: [group]
---

# group_start_node

Starts a member that is registered but not running.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| name | string | yes | Creature name or id |

## Behavior

- Starting an already-running member is a no-op.
