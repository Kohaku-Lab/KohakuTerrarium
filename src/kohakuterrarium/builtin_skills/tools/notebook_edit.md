---
name: notebook_edit
description: 'Apply ordered cell edits to a Jupyter notebook. Requires a prior notebook_read. Not for plain files - use multi_edit.'
category: builtin
tags: [notebook, edit]
---

# notebook_edit

Replaces, inserts, or deletes notebook cells, preserving the notebook's
structure.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| path | string | yes | `.ipynb` file |
| edits | array | no | Ordered edits; replaces the single-edit arguments below |
| cell_id | string | no | Target cell, or the anchor for an insert |
| new_source | string | no | New cell source |
| cell_type | string | no | `code`, `markdown`, or `raw` |
| edit_mode | string | no | `replace` (default), `insert`, or `delete` |
| insert_location | string | no | `after` (default), `before`, `beginning`, `end` |
| clear_outputs | boolean | no | Clear outputs on a code replace, default true |

## Behavior

- Requires a prior `notebook_read`; re-read after any external change.
- Edits apply in order, and each sees the notebook the previous one left.
- Replacing a code cell clears its outputs and execution count unless you
  say otherwise, so stale results never outlive the code that made them.

## Limits

- Notebooks only. Editing the `.ipynb` as text risks corrupting it.

## Reference

### Cell ids

Real ids are stable across edits. Synthetic `cell-N` ids are index-based and
shift after an insert or delete; re-read before relying on them again.
