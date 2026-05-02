---
name: grep
description: Search file contents with regex pattern matching
category: builtin
tags: [search, content]
---

# grep

Search file contents with regex pattern matching.

## WHEN TO USE

- Finding where something is defined/used
- Searching for specific code patterns
- Locating TODOs, FIXMEs, or comments
- Finding function/class definitions

## HOW TO USE

```
tool call: grep(
pattern
)
```

With optional parameters:

```
tool call: grep(
  path: src/
  glob: **/*.py
  limit: 50
  ignore_case: true
pattern
)
```

## Arguments

| Arg         | Type  | Description                                |
| ----------- | ----- | ------------------------------------------ |
| pattern     | body  | Python regex pattern to search (required)  |
| path        | @@arg | Directory or file to search (default: cwd) |
| glob        | @@arg | File glob filter (default: `**/*`)         |
| limit       | @@arg | Max matches to return (default: 50)        |
| ignore_case | @@arg | Case-insensitive search (default: false)   |

## Behavior

- Uses Python regex (re module), not ripgrep or shell grep
- Binary files are automatically skipped
- Lines longer than 2000 characters are truncated in output
- When results exceed the limit, a hint message shows total match count so you know to narrow your pattern
- Searches recursively through directories matching the glob filter

## Examples

```
tool call: grep(
  glob: **/*.py
def \w+\(
)
```

```
tool call: grep(
  ignore_case: true
todo|fixme
)
```

```
tool call: grep(
  path: src/main.py
import
)
```

## Output Format

```
src/main.py:10: def main():
src/utils.py:25: def helper(x):

(2 matches in 15 files)
```

## TIPS

- Use `glob` arg to narrow file types (e.g., `**/*.py`)
- Escape regex special chars: `\(`, `\[`, `\.`
- Use `read` after grep to examine surrounding context
- Set `ignore_case=true` for case-insensitive text search
