---
name: send_channel
description: 'Send a message to a channel in your group. Not for one creature in particular - use group_send.'
category: builtin
tags: [group]
---

# send_channel

Publishes onto a named group channel.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| channel | string | yes | Channel you are allowed to send on |
| message | string | yes | Body to send |

## Behavior

- Broadcast: every listener receives it, and it is not echoed back to you.
- The channels you may send on are listed in your group section.
