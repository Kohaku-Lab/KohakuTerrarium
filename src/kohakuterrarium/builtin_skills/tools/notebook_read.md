---
name: notebook_read
description: 'Read a Jupyter notebook as cells with outputs. Required before notebook_edit. Not for plain files - use read.'
category: builtin
tags: [notebook]
---

# notebook_read

Returns a notebook as a list of cells rather than raw JSON.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| path | string | yes | `.ipynb` file |
| cell_id | string | no | Read one cell by real id or `cell-N` |
| offset | integer | no | First cell index |
| limit | integer | no | Cells to read |
| include_outputs | string | no | `none`, `summary` (default), or `all` |
| include_metadata | boolean | no | Include per-cell metadata, default false |

## Behavior

- Reading a notebook is what unlocks `notebook_edit` on it.
- Real cell ids are stable; synthetic `cell-N` ids are positional and shift
  after an insert or delete, so re-read after structural edits.

## Limits

- Source is truncated at 8000 characters per cell, outputs at 4000.

## Reference

### Output shape

One block per cell: index, id, type, source, then outputs at the requested
detail. Use plain `read` only when debugging the notebook file format itself.
