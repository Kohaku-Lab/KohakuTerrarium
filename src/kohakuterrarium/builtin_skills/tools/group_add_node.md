---
name: group_add_node
description: 'Spawn a creature into your group from a config path. Not for one-shot private work - dispatch a sub-agent.'
category: builtin
tags: [group]
---

# group_add_node

Adds a new creature to your graph and links it to you as its parent.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| config_path | string | yes | Creature folder or `@pkg/...` reference |
| name | string | no | Runtime name; defaults to the config's |
| llm | string | no | Model override for the new member |
| pwd | string | no | Working directory; defaults to yours |

## Behavior

- The new member joins your graph but is wired to nothing. Draw its channels
  with `group_channel` or its wires with `group_wire`, or it hears nothing.
- Spawning is refused once the graph reaches its population cap; remove a
  member first.
- Spawned creatures are never privileged.
