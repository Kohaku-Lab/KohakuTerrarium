---
name: group_remove_node
description: 'Remove a creature from your group. Not for pausing one temporarily - use group_stop_node.'
category: builtin
tags: [group]
---

# group_remove_node

Removes a member and its edges from the graph.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| name | string | yes | Creature name or id |

## Behavior

- Removing a member that bridges two halves splits the graph, and each side
  gets its own session store. Check `group_status` first.
- Removal is permanent for this run; a stopped member can be restarted, a
  removed one cannot.
