---
name: python
description: Run a Python snippet and return its stdout. Use for computation and data munging. Not for shell pipelines - use bash.
category: builtin
tags: [shell, system]
---

# python

Executes a Python snippet in a subprocess and returns whatever it prints.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| code | string | yes | Python source to run |
| timeout | number | no | Seconds; 0 disables |
| run_in_background | boolean | no | Return immediately; the result arrives in a later turn |

## Behavior

- Only stdout comes back, so a value you want to see must be printed.
- Each call is a fresh process; nothing persists between calls.
