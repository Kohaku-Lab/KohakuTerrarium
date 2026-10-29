---
name: ask_user
description: 'Ask the user a free-text question and wait for the answer. Use it whenever you need a fact or decision from the user. Not for a pick-one choice - use show_card.'
category: builtin
tags: [interaction]
---

# ask_user

Puts a question to the user and blocks until they answer.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| question | string | yes | What to ask |
| timeout_s | number | no | Bound the wait; unset waits indefinitely |

## Behavior

- With no interactive output attached, the call returns a no-responder note
  immediately instead of blocking forever.
- The wait is unbounded unless `timeout_s` is set.
