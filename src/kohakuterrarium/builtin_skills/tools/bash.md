---
name: bash
description: Execute shell commands (prefer dedicated tools for file ops)
category: builtin
tags: [shell, command, system]
license: internal
---

# bash

Execute shell commands and return output.

## IMPORTANT: Prefer Dedicated Tools

Do NOT use bash for operations that have dedicated tools:

- File reading: use `read` (NOT `cat`, `head`, `tail`)
- File editing: use `edit` (NOT `sed`, `awk`)
- File writing: use `write` (NOT `echo >`, `cat <<EOF`)
- File finding: use `glob` (NOT `find`, `ls`)
- Content search: use `grep` (NOT `grep`, `rg` via bash)

Using dedicated tools gives structured output and enables safety guards.

## Arguments

| Arg     | Type   | Description                                                                                      |
| ------- | ------ | ------------------------------------------------------------------------------------------------ |
| command | string | Shell command to execute (required)                                                              |
| type    | string | Shell type: bash, zsh, sh, fish, pwsh, powershell (default: bash)                                |
| timeout | number | Maximum execution time in seconds for this call (default: tool config timeout; `0` = no timeout) |

## Shell Type

By default, all commands run in **bash** on every platform (including
Windows, via Git Bash). The process `$SHELL` does not replace this default.
Use `type="..."` to select another shell explicitly. Supported values are
`bash`, `zsh`, `sh`, `fish`, `pwsh`, and `powershell`. If the requested shell
is not available, the tool reports which shells are installed.

Shell executable overrides are checked in this order:

1. `KT_<SHELL>_PATH`, such as `KT_BASH_PATH`
2. `KT_SHELL_PATH`
3. Platform shell discovery

## Git Safety

- Prefer new commits over amending existing ones.
- Never skip hooks (--no-verify) unless explicitly asked.
- Before destructive operations (reset --hard, push --force), confirm with
  the user.
- Never force push to main/master.

## Multiple Commands

- Independent commands: run them separately (parallel execution).
- Dependent commands: chain with `&&`.
- Sequential (failure OK): chain with `;`.

## Behavior

- Commands run in bash on all platforms (Git Bash on Windows) unless `type` selects another shell.
- Use the `type` parameter to switch shells explicitly.
- stdout and stderr are combined in the output.
- Commands have a configurable timeout; pass `timeout` per call to override the tool default.
- `timeout: 0` disables the execution timeout for long-running commands.
- Large outputs may be truncated to the configured max size.

## WHEN TO USE

- Running system commands (git, npm, pip, cargo, etc.)
- Checking system state (pwd, whoami, env)
- Running build/test commands
- Package management operations

## Output

Returns combined stdout/stderr. Exit code is included in the result metadata.

## LIMITATIONS

- Commands have timeout (default: 60 seconds; override per call with `timeout`)
- Large outputs may be truncated
- Shell availability varies by platform (bash via git bash on Windows)
