---
name: bash
description: Run a shell command. Use for builds, tests, git, and process control. Not for reading or editing files - use read, glob, grep, multi_edit.
category: builtin
tags: [shell, command, system]
---

# bash

Runs a command and returns its combined output and exit status.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| command | string | yes | Command to run |
| type | string | no | Shell: bash (default), zsh, sh, fish, pwsh, powershell |
| timeout | number | no | Total seconds including lock wait; 0 disables |
| allow_concurrent | boolean | no | Skip the serial lock when this call is safe to parallelize |
| run_in_background | boolean | no | Return immediately; the result arrives in a later turn |

## Behavior

- Commands run through bash on every platform unless `type` says otherwise;
  the ambient `$SHELL` is not consulted.
- Shell commands are opaque, so they take a serial lock by default. Set
  `allow_concurrent` only when you know the call is independent.
- `timeout` covers waiting for that lock as well as the command itself, so a
  call can time out before the command starts.

## Limits

- If the requested shell is missing, the error names the shells that are
  installed.

## Reference

### Shell resolution order

1. `KT_<SHELL>_PATH`, e.g. `KT_BASH_PATH`
2. `KT_SHELL_PATH`
3. Platform discovery

### Chaining

Run independent commands as separate calls so they parallelize. Chain with
`&&` when a later command depends on an earlier one, `;` when failure is
acceptable.
