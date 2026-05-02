---
name: json_write
description: Modify JSON files at specific paths
category: builtin
tags: [data, json]
---

# json_write

Modify JSON files by setting values at specific dot-path locations.

## WHEN TO USE

- Updating configuration values in JSON files
- Adding new fields to existing JSON data
- Creating new JSON files from scratch
- Programmatically modifying package.json, tsconfig.json, etc.

## HOW TO USE

```
tool call: json_write(
  path: file_path
  query: .key.nested
"value"
)
```

The value (content body) is parsed as JSON. If it is not valid JSON, it is treated as a plain string.

## Arguments

| Arg   | Type    | Description                                       |
| ----- | ------- | ------------------------------------------------- |
| path  | @@arg   | Path to JSON file (required)                      |
| query | @@arg   | Dot-path to modify (default: "." for entire file) |
| value | content | JSON value to set (required)                      |

## Query Syntax

- `.` - replace entire document
- `.key` - set top-level key
- `.key.nested` - set nested key (creates intermediate objects)
- `.array[0]` - set array element by index
- `.array[0].field` - set field inside array element

## Examples

Set a string field:

```
tool call: json_write(
  path: config.json
  query: .database.host
"localhost"
)
```

Set a number:

```
tool call: json_write(
  path: config.json
  query: .database.port
5432
)
```

Set an object:

```
tool call: json_write(
  path: config.json
  query: .settings
{"debug": true, "verbose": false}
)
```

Replace entire file:

```
tool call: json_write(
  path: data.json
{"users": [], "version": 1}
)
```

## Output Format

Returns a confirmation message indicating the file and path that were updated.

## LIMITATIONS

- Creates parent directories automatically if they do not exist
- Cannot append to arrays (use json_read, modify, json_write as a workaround)
- Index-based writes require the array to already exist and be long enough
- Output is always formatted with 2-space indentation

## TIPS

- Use `json_read` first to inspect the current state before writing
- Intermediate objects are created automatically (e.g., `.a.b.c` creates `a` and `b` if missing)
- Values are parsed as JSON first. Wrap strings in quotes (`"hello"`) to ensure they stay strings
- If the file does not exist, it is created with an empty object as the base
- For non-JSON files, use `write` or `edit` instead
