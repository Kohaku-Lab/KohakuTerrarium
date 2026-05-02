---
name: write
description: Write content to a file (must read first if file exists)
category: builtin
tags: [file, io]
---

# write

Write content to a file. Creates if doesn't exist, overwrites if it does.

## SAFETY

- **You MUST read an existing file before writing to it.** The tool will error if you haven't.
- For partial changes to existing files, prefer the `edit` tool (it sends only the diff, not the whole file).
- New files (file does not exist) can be written without reading first.
- If the file was modified since your last read, you must re-read it.

## WHEN TO USE

- Creating new files
- Replacing entire file contents
- Writing generated code or configs

## HOW TO USE

```
tool call: write(
  path: file_path
content here
)
```

## Arguments

| Arg     | Type  | Description             |
| ------- | ----- | ----------------------- |
| path    | @@arg | Path to file (required) |
| content | body  | Content to write        |

## Examples

```
tool call: write(
  path: src/hello.py
def hello():
    print("Hello, World!")

if __name__ == "__main__":
    hello()
)
```

## Output Format

```
Created /path/to/file.py (15 lines, 342 bytes)
```

## LIMITATIONS

- Overwrites entire file (no partial edit)
- Creates parent directories automatically

## TIPS

- Use `read` first to understand existing content
- For partial edits, use `edit` tool instead
- Content is written exactly as provided
