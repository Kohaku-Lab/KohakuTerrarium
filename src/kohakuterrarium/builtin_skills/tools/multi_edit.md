---
name: multi_edit
description: Apply ordered exact search/replace edits to one file. Use when changing a file in one or more places. Not for whole-file rewrites - use write.
category: builtin
tags: [file, edit]
---

# multi_edit

Applies each edit in order to a single file; edit N sees the file as edit N-1
left it.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| path | string | yes | File to edit |
| edits | array | yes | Ordered `{old, new, replace_all?}` objects |
| strict | boolean | no | Default true. Any failure leaves the file untouched |
| best_effort | boolean | no | Apply what matches, skip failures. Rejects strict=true |

```json
{
  "path": "src/foo.py",
  "edits": [
    { "old": "class OldName", "new": "class NewName" },
    { "old": "OldName(", "new": "NewName(", "replace_all": true }
  ]
}
```

## Behavior

- Matching is exact: whitespace, indentation, punctuation, and case all count.
- An edit fails when `old` is absent, or appears more than once without
  `replace_all`.
- The three policies differ only in what reaches disk after a failure:
  **strict** writes nothing, **partial** (`strict=false`) writes the edits
  before the first failure, **best_effort** writes every edit that matched.
- Requires a prior `read`; re-read after any external modification.
- Ordering is load-bearing - an early edit can create or destroy text a later
  one expects.

## Limits

- One file, exact strings only. No regex, no unified diff.
- `old` must be non-empty; `new` may be empty to delete.
- Binary files are rejected.

## Reference

### Output format

A per-edit summary followed by a unified diff of what actually changed.

```text
Edited src/foo.py
mode: strict
applied: 3
failed: 0
skipped: 0

edit[0]: ok: 1 replacement
edit[1]: ok: 7 replacements
edit[2]: ok: no change (old equals new)
```

A strict failure reports the same shape with `No changes made to <path>` and
leaves the file alone:

```text
edit[0]: ok: 1 replacement
edit[1]: error: old not found in file after prior edits
edit[2]: skipped
```

### Edge cases

- `old == new` is a permitted no-op.
- A run whose net effect is identical to the original still succeeds.
- `strict=true` with `best_effort=true` is rejected outright.
