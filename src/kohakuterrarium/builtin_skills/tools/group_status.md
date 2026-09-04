---
name: group_status
description: 'Snapshot your group: members, channels, wires, and health. Not for changing it - use the other group tools.'
category: builtin
tags: [group]
---

# group_status

Reports the current graph as the engine sees it.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| include_history | boolean | no | Include recent channel traffic |

## Behavior

- Read this before a structural change; removing a bridge member splits the
  graph.
- Cheap to call, but it is a snapshot, not a subscription. Do not poll it.
