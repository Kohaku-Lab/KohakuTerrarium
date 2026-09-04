---
name: group_stop_node
description: 'Stop a running member without removing it. Not for permanent removal - use group_remove_node.'
category: builtin
tags: [group]
---

# group_stop_node

Stops a member, leaving it in the graph.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| name | string | yes | Creature name or id |

## Behavior

- Its wiring survives; it simply stops acting until restarted.
- In-flight work is cancelled.
