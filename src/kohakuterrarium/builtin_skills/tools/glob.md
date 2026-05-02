---
name: glob
description: Find files by glob pattern (sorted by modification time)
category: builtin
tags: [file, search]
---

# glob

Find files by glob pattern, sorted by modification time (newest first).

## WHEN TO USE

- Finding files by name or extension
- Exploring project structure
- Locating specific file types

## HOW TO USE

```
tool call: glob(
pattern
)
```

Or with optional parameters:

```
tool call: glob(
  path: base_dir
  limit: 50
pattern
)
```

## Arguments

| Arg     | Type  | Description                   |
| ------- | ----- | ----------------------------- |
| pattern | body  | Glob pattern (required)       |
| path    | @@arg | Base directory (default: cwd) |
| limit   | @@arg | Max results (default: 100)    |

## Pattern Syntax

| Pattern | Matches                             |
| ------- | ----------------------------------- |
| `*`     | Any chars except `/`                |
| `**`    | Any chars including `/` (recursive) |
| `?`     | Single character                    |
| `[abc]` | a, b, or c                          |

## Examples

```
tool call: glob(
**/*.py
)
```

```
tool call: glob(
  path: src/components
*.ts
)
```

```
tool call: glob(
**/*.{json,yaml,toml}
)
```

## Output Format

```
src/main.py
src/utils/helpers.py
tests/test_main.py
```

Results are sorted by modification time (newest first). Shows total count when results are truncated by the limit.

## TIPS

- Use `**/*.ext` for recursive search by extension
- Combine with `read` to examine found files
- Use specific patterns to narrow results in large codebases
