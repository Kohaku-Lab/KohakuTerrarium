---
name: scratchpad
description: Read or write session working memory as key-value pairs.
category: builtin
tags: [memory]
---

# scratchpad

Stores small values that survive across turns within the session.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| action | string | yes | `get`, `set`, `delete`, or `list` |
| key | string | no | Required for get, set, and delete |
| value | string | no | Required for set |

## Behavior

- Contents persist for the session and are saved with it.
- For anything the user should see or keep, write a file instead.
