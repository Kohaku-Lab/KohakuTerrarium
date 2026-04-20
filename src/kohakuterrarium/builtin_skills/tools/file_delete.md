---
name: file_delete
description: Delete a file (must read first) or directory (requires recursive=true)
category: builtin
tags: [file, io, delete, remove]
---

# file_delete

Delete a file or directory. Structured replacement for `bash rm`.

## SAFETY

- **Path boundary**: the target must be inside the agent's working directory
  (same warn/block policy as the other file tools).
- **Read-before-delete for files**: you must have read the file first (and
  it must be unchanged since) before deleting it. This mirrors the
  read-before-write guard — the model should know what content it is
  destroying. Symlinks are exempt (the target is not inspected).
- **Directories are opt-in**: deleting a directory requires
  `recursive=true`. Without it, the call is rejected with a clear error.
- **Read state is cleared**: any `file_read_state` record for the deleted
  path (or paths under a deleted directory) is removed on success.

## Arguments

| Arg | Type | Description |
|-----|------|-------------|
| path | string | Path to delete (required) |
| recursive | bool | Required for directories. Deletes the tree rooted at `path`. Default: false |

## WHEN TO USE

- Removing a stale file you just inspected with `read`
- Removing an empty scratch directory
- Tearing down a generated directory tree (with `recursive=true`)

## Examples

Delete a file you just read:
```
tool call: file_delete(
  path: src/old_module.py
)
```

Delete a directory tree:
```
tool call: file_delete(
  path: build/
  recursive: true
)
```

## Output

```
Deleted file /path/to/file
```
```
Deleted directory /path/to/dir
```
```
Deleted symlink /path/to/link
```

## LIMITATIONS

- Symlinks are unlinked, not resolved — the link is removed but its target
  is left alone.
- For directories, `recursive=true` uses `shutil.rmtree` and follows normal
  filesystem semantics (it stops on permission errors).

## TIPS

- If you really need to force-delete without reading, use `bash rm` and
  accept the safety trade-off (see `info(bash)`). Prefer this tool.
- To empty but keep a directory, delete its contents with `glob` + this
  tool, or combine `file_delete` + `mkdir`.
