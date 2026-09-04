---
name: send_message
description: Send a message to a named channel.
category: builtin
tags: [channels]
---

# send_message

Publishes a message onto a named channel.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| channel | string | yes | Channel name |
| message | string | yes | Body to send |

## Behavior

- Channels are broadcast: every listener receives the message, and your own
  sends are not echoed back to you.
- The channels you can reach are listed in the group section of your prompt.
