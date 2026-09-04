---
name: tree
description: List a directory as a tree, respecting .gitignore. Use to orient in an unfamiliar project. Not for finding a specific file - use glob.
category: builtin
tags: [search, files]
---

# tree

Renders a directory as an indented tree.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| path | string | no | Directory to render; defaults to the working directory |
| depth | integer | no | Maximum depth to descend |
| limit | integer | no | Maximum lines, default 100 |

## Behavior

- `.gitignore` and common noise directories are excluded, so the output
  reflects the project rather than its build artifacts.
- Output is truncated at `limit` lines with a notice.
