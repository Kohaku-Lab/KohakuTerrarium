---
name: multi_edit
description: Apply multiple ordered search/replace edits to one file with strict, partial, or best_effort policies. Use info(multi_edit) first.
category: builtin
tags: [file, io, edit, diff, patch, atomic]
---

# multi_edit

Apply multiple exact search/replace edits to a single file in order.

This tool exists for cases where you want to make several related search/replace edits in the same file and want a clear policy for what happens if one of them fails.

`multi_edit` is intentionally limited:

- one file only
- exact string matching only
- ordered edits only
- no unified diff input
- no regex

## SAFETY

- **You MUST read the file before editing it.** The tool will error if you haven't.
- If the file was modified since your last read, you must re-read it.
- Binary files cannot be edited.
- `multi_edit` requires reading this manual first via `info(name=multi_edit)`.

## WHEN TO USE

Use `multi_edit` when:

- you want to perform multiple search/replace edits in one file
- several edits are logically related
- you want atomic behavior (`strict=true`)
- you want one result showing what the tool actually changed

Use plain `edit` instead when:

- you only need one search/replace edit
- you want to apply a unified diff patch

## SIGNATURE

```json
{
  "path": "src/foo.py",
  "edits": [
    { "old": "class OldName", "new": "class NewName" },
    { "old": "OldName(", "new": "NewName(", "replace_all": true }
  ],
  "strict": true,
  "best_effort": false
}
```

## ARGUMENTS

| Arg         | Type  | Description                                                                           |
| ----------- | ----- | ------------------------------------------------------------------------------------- |
| path        | @@arg | File path to edit                                                                     |
| edits       | @@arg | Non-empty array of ordered search/replace edits                                       |
| strict      | @@arg | Default `true`. If any edit fails, do not write anything                              |
| best_effort | @@arg | Default `false`. Try every edit, skipping failures. Cannot be used with `strict=true` |

Each edit item has:

| Field       | Type    | Description                                            |
| ----------- | ------- | ------------------------------------------------------ |
| old         | string  | Exact text to find. Must be non-empty                  |
| new         | string  | Replacement text. Can be empty for deletion            |
| replace_all | boolean | Replace all occurrences for this edit. Default `false` |

## MATCH RULES

Matching is exact string matching.

That means:

- whitespace matters
- indentation matters
- punctuation matters
- letter casing matters

For each edit:

- if `old` is not found: that edit fails
- if `old` appears more than once and `replace_all` is not true: that edit fails
- if `replace_all=true`: all occurrences in the current buffer are replaced

## ORDERED SEMANTICS

Edits are applied **sequentially**.

Edit `1` sees the file after edit `0`.
Edit `2` sees the file after edit `1`.
And so on.

This means earlier edits can:

- remove text that later edits expected
- create text that later edits will match

This is intentional.

## POLICY MODES

### 1. Strict mode

Default:

```json
{
  "strict": true,
  "best_effort": false
}
```

Behavior:

- apply edits in memory in order
- if any edit fails, the whole call fails
- **the file remains unchanged on disk**

Use this when you want atomic behavior.

### 2. Partial mode

```json
{
  "strict": false,
  "best_effort": false
}
```

Behavior:

- apply edits in order
- stop at the first failure
- write any successful earlier edits
- remaining edits are skipped

Use this when partial progress is acceptable.

### 3. Best-effort mode

```json
{
  "strict": false,
  "best_effort": true
}
```

Behavior:

- attempt every edit in order
- failed edits are recorded and skipped
- successful edits are still written

Use this only when you explicitly want a loose batch operation.

### Invalid combination

This is invalid:

```json
{
  "strict": true,
  "best_effort": true
}
```

The tool will reject it.

## EDGE CASES

- `old == new` is allowed. It becomes a no-op for that edit.
- `new == ""` is allowed. That deletes the matched text.
- if the final content ends up identical to the original file, the call can still succeed
- empty `old` is not allowed

## OUTPUT

`multi_edit` returns two kinds of information:

1. a summary of what happened per edit
2. a unified diff of the file's actual before/after content, when content changed

### Example success

```text
Edited src/foo.py
mode: strict
applied: 3
failed: 0
skipped: 0

edit[0]: ok: 1 replacement
edit[1]: ok: 7 replacements
edit[2]: ok: no change (old equals new)

--- a/src/foo.py
+++ b/src/foo.py
@@ -1,3 +1,3 @@
-class OldName:
+class NewName:
```

### Example strict failure

```text
No changes made to src/foo.py
mode: strict
applied: 1
failed: 1
skipped: 2

edit[0]: ok: 1 replacement
edit[1]: error: old not found in file after prior edits
edit[2]: skipped
edit[3]: skipped
```

In strict mode, a failure means the file stays unchanged.

### Example best-effort result with failures

```text
Edited src/foo.py
mode: best_effort
applied: 2
failed: 1
skipped: 0

edit[0]: ok: 1 replacement
edit[1]: error: found 3 occurrences of old; set replace_all=true or provide more context
edit[2]: ok: 2 replacements
```

## TIPS

- Prefer `multi_edit` over repeated `edit` calls when changing the same file several times
- Prefer `strict=true` unless you specifically want partial or best-effort behavior
- Include enough surrounding context in `old` to make each match unique
- Use `replace_all=true` for deliberate rename-like edits
- Read the file again after a failed attempt if your edits may have become stale
