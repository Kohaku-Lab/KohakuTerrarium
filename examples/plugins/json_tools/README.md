# json_tools — a worked custom tool

Two tools that read and modify JSON files through dot-path expressions. They
shipped as built-ins until KohakuTerrarium 3.0, when they were removed from the
default surface: `read` + `multi_edit` cover the same ground, and every callable
in a creature's loadout costs schema tokens and dilutes tool selection.

They live on here as the reference for **writing a context-aware custom tool**.

## What to copy from this example

- `needs_context = True`, so `_execute` receives a `ToolContext` and can resolve
  paths against the agent's working directory rather than the process cwd.
- `resolve_tool_path(path, context)` instead of `Path(path)`.
- `context.path_guard.check(...)` before touching the filesystem, so the sandbox
  plugin can confine the tool.
- **`check_edit_guards` / `update_edit_read_state` in the write path.** The
  original built-in `json_write` omitted these, which made it a way around the
  read-before-write rule every other file-writing tool enforces. The version
  here does it correctly — that is the point of keeping the example.

## Using them

```yaml
tools:
  - name: json_read
    type: custom
    module: examples/plugins/json_tools/json_read.py
    class: JsonReadTool
  - name: json_write
    type: custom
    module: examples/plugins/json_tools/json_write.py
    class: JsonWriteTool
```

## Limitations kept from the original

Dot-path queries only — no wildcards, filters, or JSONPath. `json_write` cannot
append to an array, and it rewrites the document with two-space indentation, so
it does not preserve the original formatting. For anything beyond a single
scalar update, `read` + `multi_edit` is the better tool.
