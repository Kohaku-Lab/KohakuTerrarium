---
name: file_move
description: Move or rename a file/directory (set overwrite=true to replace destination)
category: builtin
tags: [file, io, move, rename]
---

# file_move

Move or rename a file or directory. Structured replacement for `bash mv`.

## SAFETY

- **Path boundary**: both `src` and `dst` must be inside the agent's working
  directory (the same warn/block policy as the other file tools).
- **Overwrite requires read**: if `dst` exists and is a file, you must have
  read it first (and it must be unchanged since) before `overwrite=true`
  will proceed. This prevents silently clobbering tracked content.
- **No self-move**: `src` and `dst` resolving to the same path is rejected.
- **Read state migrates**: a file's `read` record is transferred from `src`
  to `dst` on success, so you can keep editing without re-reading. For
  directory moves, read records under `src` are cleared (re-read as needed).

## Arguments

| Arg | Type | Description |
|-----|------|-------------|
| src | string | Source path (required). Accepts `source` as alias. |
| dst | string | Destination path (required). Accepts `destination` as alias. |
| overwrite | bool | Replace `dst` if it already exists (default: false) |

## WHEN TO USE

- Renaming a file or directory
- Moving a file into another directory
- Reorganizing project layout

## Examples

Rename a file:
```
tool call: file_move(
  src: src/old_name.py
  dst: src/new_name.py
)
```

Move into a directory:
```
tool call: file_move(
  src: src/utils/helper.py
  dst: src/helpers/helper.py
)
```

Replace an existing file (you must have `read` it first):
```
tool call: file_move(
  src: draft.md
  dst: README.md
  overwrite: true
)
```

## Output

```
Moved file /path/to/src -> /path/to/dst
```
or
```
Moved directory /path/to/src -> /path/to/dst
```

## LIMITATIONS

- Cross-device moves fall back to copy + delete via `shutil.move`.
- Symlinks are moved as-is (the link, not the target).
- `dst`'s parent directory is created automatically if missing.

## TIPS

- To move something *out* of the working directory, the path guard will
  warn you once; retry to proceed.
- Use `file_move` instead of `bash mv` — you get path-guard coverage and
  read-state migration for free.
