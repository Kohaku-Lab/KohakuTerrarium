---
name: edit
description: Edit file via search/replace (path, old, new) or unified diff (path, diff). Use info(edit) first.
category: builtin
tags: [file, io, edit, diff, patch]
---

# edit

Edit files using search/replace or unified diff. Mode is auto-detected from arguments.

## SAFETY

- **You MUST read the file before editing it.** The tool will error if you haven't.
- If the file was modified since your last read, you must re-read it.
- Binary files cannot be edited.
- `edit` requires reading this manual first via `info(name=edit)`.

## WHEN TO USE

Use `edit` when:

- you need a single search/replace change
- you want to apply a unified diff patch

Use `multi_edit` instead when:

- you want to make several search/replace edits in the same file
- you want one result showing what actually changed
- you want strict atomic behavior for multiple edits

## Mode 1: Search/Replace (recommended for simple changes)

Find an exact string and replace it.

### Arguments

| Arg         | Type  | Description                              |
| ----------- | ----- | ---------------------------------------- |
| path        | @@arg | Path to file (required)                  |
| old         | @@arg | Exact text to find (required)            |
| new         | @@arg | Replacement text (required)              |
| replace_all | @@arg | Replace all occurrences (default: false) |

### Rules

- `old` must match the file content EXACTLY (including whitespace)
- If `old` appears multiple times and `replace_all` is false, provide more context to make it unique
- Set `replace_all=true` to replace every occurrence
- If you are about to make multiple search/replace edits to the same file, prefer `multi_edit`

### Example

```
tool call: edit(
  path: src/main.py
  old: def hello():
  new: def greet():
)
```

## Mode 2: Unified Diff (for multi-site or complex changes)

Apply standard unified diff patches.

### Arguments

| Arg  | Type  | Description                     |
| ---- | ----- | ------------------------------- |
| path | @@arg | Path to file (required)         |
| diff | body  | Unified diff content (required) |

### Format

```
@@ -start,count +start,count @@
 context line (unchanged, starts with space)
-line to remove (starts with minus)
+line to add (starts with plus)
```

- `start` is 1-indexed line number
- `count` is the number of lines in that section
- Context lines must match the file exactly
- Multiple hunks can appear in one diff

### Example

```
tool call: edit(
  path: src/app.py
@@ -1,2 +1,3 @@
 import os
+import json
 import sys
@@ -20,2 +21,2 @@
-    return None
+    return {}
)
```

## Output Format

Search/replace:

```
Edited /path/to/file.py
  1 replacement(s) made
```

Unified diff:

```
Edited /path/to/file.py
  2 hunk(s) applied
  +3 -2 lines
```

## TIPS

- Use search/replace for single-site changes
- Use unified diff for line-based or complex patch-style changes
- Use `multi_edit` when making multiple search/replace edits in one file
- Always read the file first to see exact content and line numbers
