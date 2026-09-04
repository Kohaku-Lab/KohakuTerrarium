---
name: group_send
description: 'Send a message directly to one creature in your group. Not for the whole group - use send_channel.'
category: builtin
tags: [group]
---

# group_send

Delivers a one-shot message to a single creature.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| to | string | yes | Target creature name or id |
| message | string | yes | Body to send |

## Behavior

- Arrives tagged `[direct from <you>]`, bypassing channels entirely.
- Point-to-point; nothing else in the group sees it.
