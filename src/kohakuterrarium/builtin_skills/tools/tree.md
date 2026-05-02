---
name: tree
description: List files in tree format (respects .gitignore, max 100 lines by default)
category: builtin
tags: [file, directory, navigation]
---

# tree

List directory structure with frontmatter summaries.
Respects .gitignore by default and limits output to avoid flooding context.

## Arguments

| Arg       | Type    | Description                                    |
| --------- | ------- | ---------------------------------------------- |
| path      | string  | Directory to list (default: cwd)               |
| depth     | integer | Max recursion depth (default: 3)               |
| limit     | integer | Max output lines (default: 100, 0 = unlimited) |
| gitignore | boolean | Follow .gitignore rules (default: true)        |
| hidden    | boolean | Show hidden files (default: false)             |

## Gitignore Support

By default, the tree respects `.gitignore` files found in each directory.
Common directories like `node_modules`, `__pycache__`, `.git`, and
`*.egg-info` are always skipped. The output footer shows how many entries
were ignored and which directories were skipped.

Set `gitignore=false` to show everything.

## Line Limit

Output is capped at 100 lines by default. When truncated, the footer
tells you how to see more (increase `limit` or narrow `path`).
Set `limit=0` to disable the limit.

## Frontmatter Extraction

For markdown files, extracts and displays inline summaries from YAML frontmatter:

- `summary`: Brief description (preferred)
- `title`: File title (fallback)
- `description`: Description (fallback)
- `protected`: Shows [protected] marker

## WHEN TO USE

- Exploring project structure
- Understanding folder organization
- Finding files before reading them
- Getting overview of a directory
- Discovering memory structure (markdown files with frontmatter)

## WHEN NOT TO USE

- Searching file contents (use grep)
- Finding files by pattern (use glob)
- Reading file contents (use read)

## Output

Tree-formatted directory listing with connectors. Directories are listed
before files. Markdown files show extracted frontmatter summaries inline.
Ignored entries and truncation are noted in the footer.

```
project/
├── src/
│   ├── main.py
│   └── utils.py
├── memory/
│   ├── character.md - Agent persona definition
│   └── rules.md [protected] - Immutable behavior rules
└── README.md
(15 entries ignored by .gitignore: node_modules, __pycache__..., use gitignore=false to show all)
```

## TIPS

- Use before `read` to discover file paths
- Combine with `glob` for pattern matching
- Use `depth` to limit depth, `limit` to limit total output lines
- Use `gitignore=false` when you need to see build artifacts or output files
