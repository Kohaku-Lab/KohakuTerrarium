---
name: json_read
description: Read and query JSON files with path expressions
category: builtin
tags: [data, json]
---

# json_read

Read and query JSON files with simple dot-path expressions.

## WHEN TO USE

- Reading configuration files (package.json, tsconfig.json, etc.)
- Extracting specific values from large JSON data
- Inspecting API response dumps or data files
- Checking a single field without reading the entire file

## HOW TO USE

```
tool call: json_read(
  path: file_path
)
```

Or with a query to extract a specific value:

```
tool call: json_read(
  path: file_path
  query: .key.nested
)
```

## Arguments

| Arg   | Type  | Description                                   |
| ----- | ----- | --------------------------------------------- |
| path  | @@arg | Path to JSON file (required)                  |
| query | @@arg | Dot-path query (default: "." for entire file) |

## Query Syntax

- `.` - entire document
- `.key` - top-level key
- `.key.nested` - nested key
- `.array[0]` - array index
- `.array[0].field` - field inside array element

## Examples

Read entire file:

```
tool call: json_read(
  path: package.json
)
```

Read a nested value:

```
tool call: json_read(
  path: config.json
  query: .database.host
)
```

Read from an array:

```
tool call: json_read(
  path: data.json
  query: .users[0].name
)
```

## Output Format

Objects and arrays are formatted as indented JSON. Primitive values (strings, numbers, booleans) are returned as plain text.

## LIMITATIONS

- UTF-8 encoding only
- Output truncated at 50,000 characters
- Query syntax is dot-path only (no wildcards, filters, or JSONPath)

## TIPS

- Use `glob` to find JSON files first, then `json_read` to inspect them
- Use a query to avoid reading huge files entirely. Extract just what you need
- For modifying JSON, use `json_write` instead
- If you need the full file with line numbers, use `read` instead
