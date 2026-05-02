---
name: notebook_read
description: Read a Jupyter .ipynb notebook as cells (required before notebook_edit)
category: builtin
tags: [file, io, notebook, jupyter, ipynb, read]
---

# notebook_read

Read a Jupyter notebook (`.ipynb`) as a concise list of cells instead of raw JSON.

Notebook JSON is verbose and easy to corrupt. Prefer `notebook_read` over plain `read` or `json_read` when you need to inspect notebook cells.

## SAFETY

- **You MUST use `notebook_read` before `notebook_edit`.** The edit tool will error if the notebook has not been read.
- `notebook_read` records the notebook's file state so stale writes can be blocked.
- If the file changes after you read it, read it again before editing.

## SIGNATURE

```json
{
  "path": "analysis.ipynb",
  "cell_id": "cell-3",
  "offset": 0,
  "limit": 10,
  "include_outputs": "summary",
  "include_metadata": false,
  "max_source_chars": 8000,
  "max_output_chars": 4000
}
```

## ARGUMENTS

| Arg              | Type    | Description                                             |
| ---------------- | ------- | ------------------------------------------------------- |
| path             | string  | Path to a `.ipynb` file                                 |
| cell_id          | string  | Optional real cell id or synthetic `cell-N` reference   |
| offset           | integer | Optional cell offset when reading a range               |
| limit            | integer | Optional maximum number of cells to read. `0` means all |
| include_outputs  | string  | `none`, `summary`, or `all`. Default `summary`          |
| include_metadata | boolean | Include cell metadata. Default `false`                  |
| max_source_chars | integer | Max source chars per cell. Default `8000`               |
| max_output_chars | integer | Max output chars per output. Default `4000`             |

## CELL IDS

Each cell is shown with a reference like:

```text
[abc123] index=1 type=code execution_count=4
```

Use the bracketed id with `notebook_edit`.

If a notebook cell has no real id, `notebook_read` shows a synthetic reference:

```text
[cell-3] index=3 type=markdown synthetic_id=true
```

Synthetic `cell-N` references are index-based. They can shift after inserts/deletes, so prefer real ids when available and re-read after structural edits.

## OUTPUTS

`include_outputs` controls code-cell output detail:

- `none`: omit outputs
- `summary`: show up to the first few outputs per cell and omit large binary data
- `all`: show every output summary; image/base64 data is still summarized, not dumped

## EXAMPLES

Read the whole notebook:

```json
{ "path": "analysis.ipynb" }
```

Read only the first 5 cells:

```json
{ "path": "analysis.ipynb", "offset": 0, "limit": 5 }
```

Read one cell by id:

```json
{ "path": "analysis.ipynb", "cell_id": "abc123", "include_outputs": "all" }
```

## WHEN TO USE RAW JSON TOOLS

Use `json_read` or plain `read` only when debugging notebook file structure itself. For normal notebook work, use `notebook_read` and `notebook_edit`.
