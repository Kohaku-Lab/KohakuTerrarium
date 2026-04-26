---
name: notebook_edit
description: Apply ordered cell edits to a Jupyter .ipynb notebook. Use info(notebook_edit) and notebook_read first.
category: builtin
tags: [file, io, notebook, jupyter, ipynb, edit, batch, atomic]
---

# notebook_edit

Apply one or more ordered cell edits to a Jupyter notebook (`.ipynb`).

This tool edits notebook cells directly, so you do not need to manipulate the raw JSON structure yourself.

## SAFETY

- **You MUST call `info(name="notebook_edit")` before first use.** The tool is locked until this manual is read.
- **You MUST call `notebook_read` on the notebook before editing it.** The tool will error if the file was not read.
- If the notebook was modified since your last read, the edit is blocked. Re-read first.
- The tool only accepts `.ipynb` files.
- `notebook_edit` serializes with other unsafe file tools.

## SINGLE-EDIT SIGNATURE

```json
{
  "path": "analysis.ipynb",
  "cell_id": "abc123",
  "edit_mode": "replace",
  "new_source": "x = 1\ny = x + 1",
  "cell_type": "code",
  "clear_outputs": true
}
```

## BATCH SIGNATURE

Use `edits` to change many cells in one call. This is notebook-specific `multi_edit` style behavior.

```json
{
  "path": "analysis.ipynb",
  "edits": [
    {
      "cell_id": "title-cell",
      "edit_mode": "replace",
      "cell_type": "markdown",
      "new_source": "# Cleaned analysis"
    },
    {
      "cell_id": "imports-cell",
      "edit_mode": "replace",
      "new_source": "import pandas as pd\nimport matplotlib.pyplot as plt"
    },
    {
      "cell_id": "imports-cell",
      "edit_mode": "insert",
      "insert_location": "after",
      "cell_type": "markdown",
      "new_source": "## Load data"
    }
  ],
  "strict": true,
  "best_effort": false
}
```

If `edits` is present, it takes precedence over top-level single-edit fields.

## ARGUMENTS

Top-level fields:

| Arg | Type | Description |
|-----|------|-------------|
| path | string | Path to a `.ipynb` file |
| cell_id | string | Cell id for single replace/delete, or insertion anchor |
| edit_mode | string | `replace`, `insert`, or `delete`. Default `replace` |
| new_source | string | New source for single replace/insert |
| cell_type | string | `code`, `markdown`, or `raw`; required for insert; optional conversion for replace |
| insert_location | string | `after`, `before`, `beginning`, or `end`. Default `after`; inserts at end when no anchor is given |
| clear_outputs | boolean | Clear code cell outputs/execution count. Default `true` |
| edits | array | Ordered batch of cell edits |
| strict | boolean | Default `true`. If any edit fails, do not write anything |
| best_effort | boolean | Default `false`. Try every edit, skipping failures. Cannot be used with `strict=true` |

Each `edits[]` item has the same cell fields: `cell_id`, `edit_mode`, `new_source`, `cell_type`, `insert_location`, `clear_outputs`.

## EDIT MODES

### replace

Replace a cell's source. Requires `cell_id` and `new_source`.

```json
{
  "path": "analysis.ipynb",
  "cell_id": "abc123",
  "edit_mode": "replace",
  "new_source": "df.head()"
}
```

If replacing a code cell and `clear_outputs=true`, the tool resets:

```json
"execution_count": null,
"outputs": []
```

### insert

Insert a new cell. Requires `cell_type` and `new_source`.

```json
{
  "path": "analysis.ipynb",
  "cell_id": "abc123",
  "edit_mode": "insert",
  "insert_location": "after",
  "cell_type": "markdown",
  "new_source": "## Next section"
}
```

If `cell_id` is omitted, default insertion is at the end. You can also set `insert_location` to `beginning` or `end` explicitly.

### delete

Delete a cell. Requires `cell_id`.

```json
{
  "path": "analysis.ipynb",
  "cell_id": "cell-7",
  "edit_mode": "delete"
}
```

## POLICY MODES

Like `multi_edit`, batch edits support three policies:

### strict mode

Default:

```json
{"strict": true, "best_effort": false}
```

- applies edits in memory in order
- if any edit fails, the whole call fails
- the notebook file remains unchanged

### partial mode

```json
{"strict": false, "best_effort": false}
```

- applies edits in order
- stops at the first failure
- writes successful earlier edits

### best-effort mode

```json
{"strict": false, "best_effort": true}
```

- attempts every edit
- failed edits are recorded and skipped
- successful edits are written

## CELL IDS

Use ids shown by `notebook_read`.

Real ids are stable. Synthetic `cell-N` ids are index-based and may shift after insert/delete. Re-read after structural edits.

## OUTPUT

The tool returns:

1. a per-edit summary
2. a truncated unified diff of the notebook JSON before/after

Example:

```text
Edited analysis.ipynb
mode: strict
applied: 2
failed: 0
skipped: 0

edit[0]: ok: replaced cell abc123 at index 1
edit[1]: ok: inserted markdown cell def456 at index 2

--- a/analysis.ipynb
+++ b/analysis.ipynb
...
```

## TIPS

- Prefer a single `notebook_edit` batch for logically related notebook changes.
- Prefer `strict=true` unless partial progress is explicitly acceptable.
- Use `notebook_read` again after insert/delete before making more structural edits.
- Do not use this tool to execute notebooks. Use `bash` with Jupyter/nbconvert, or Python tooling, when execution is needed.
