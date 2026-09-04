---
name: edit
description: Edit one file by unified diff, or by one search/replace. Use for patch application and text tool-call formats. Not for several edits - use multi_edit.
category: builtin
tags: [file, edit, diff]
---

# edit

Applies either a unified diff or a single exact search/replace to one file.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| path | string | yes | File to edit |
| diff | string | no | Unified diff to apply |
| old | string | no | Exact text to find |
| new | string | no | Replacement text |
| replace_all | boolean | no | Replace every occurrence, default false |

## Behavior

- Supply either `diff`, or `old` plus `new`; supplying neither is an error.
- Diff mode is the only editing path available under text tool-call formats,
  where arguments cannot carry arrays.
- Requires a prior `read`; re-read after any external modification.

## Limits

- One file. For several search/replace edits with a failure policy, use
  `multi_edit`.
