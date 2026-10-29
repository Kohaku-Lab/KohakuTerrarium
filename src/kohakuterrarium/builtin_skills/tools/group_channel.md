---
name: group_channel
description: 'Create, delete or rewire channels in your group. Use it to set up broadcast routes between creatures. Not for output wires - use group_wire.'
category: builtin
tags: [group]
---

# group_channel

Manages channels and which members listen or send on them.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| action | string | yes | `create`, `delete`, `wire`, `unwire` |
| channel | string | yes | Channel name |
| creature_id | string | no | Member to wire or unwire (id or name) |
| direction | string | no | `listen` or `send`: the member's edge, for `wire` / `unwire` |
| description | string | no | Shown in every member's group section (`create` only) |

## Behavior

- Channels are broadcast; there is no per-recipient addressing.
- `wire` only uses an existing channel; an unknown name is refused. `create`
  it first, or reach one member directly with `group_send` by name.
- A description is worth writing: it is what other members see.
- Deleting a channel drops every edge on it.
