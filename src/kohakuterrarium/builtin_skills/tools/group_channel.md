---
name: group_channel
description: 'Create, delete, or rewire channels in your group. Not for output wires - use group_wire.'
category: builtin
tags: [group]
---

# group_channel

Manages channels and which members listen or send on them.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| action | string | yes | `create`, `delete`, `listen`, `unlisten`, `allow_send`, `deny_send` |
| channel | string | yes | Channel name |
| creature | string | no | Member to wire, for the per-creature actions |
| description | string | no | Shown in every member's group section |

## Behavior

- Channels are broadcast; there is no per-recipient addressing.
- A description is worth writing: it is what other members see.
- Deleting a channel drops every edge on it.
