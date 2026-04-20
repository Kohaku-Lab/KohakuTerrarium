---
name: mkdir
description: Create a directory (parents and existing dirs OK by default)
category: builtin
tags: [file, io, directory, mkdir]
---

# mkdir

Create a directory. Structured replacement for `bash mkdir -p`.

## SAFETY

- **Path boundary**: the target must be inside the agent's working directory
  (same warn/block policy as the other file tools).
- **Path type check**: if `path` already exists and is *not* a directory,
  the call is rejected rather than replacing the existing file.

## Arguments

| Arg | Type | Description |
|-----|------|-------------|
| path | string | Directory path to create (required) |
| parents | bool | Create missing intermediate directories (default: true) |
| error_if_exists | bool | Error out instead of succeeding silently when the dir already exists (default: false) |

## Behavior

- By default, behaves like `mkdir -p`: creates parents, treats an existing
  directory as success.
- Set `parents=false` to require the parent directory to already exist
  (strict `mkdir` behavior).
- Set `error_if_exists=true` if you want to know whether you created the
  directory fresh — useful as a "claim" check when generating unique paths.

## WHEN TO USE

- Creating output directories before writing files
- Scaffolding project structure
- Ensuring a nested path exists before a series of `write` calls

## Examples

Create a nested path (the common case):
```
tool call: mkdir(
  path: build/artifacts/logs
)
```

Strict create (parent must exist, fail if dir exists):
```
tool call: mkdir(
  path: build/artifacts
  parents: false
  error_if_exists: true
)
```

## Output

```
Created directory /abs/path/to/dir
```
```
Directory already exists: /abs/path/to/dir
```

## LIMITATIONS

- Does not create files — use `write` for that.
- Mode/permission bits are taken from the process umask; this tool does
  not expose a `mode` argument.
