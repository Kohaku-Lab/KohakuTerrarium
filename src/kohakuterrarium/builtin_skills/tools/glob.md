---
name: glob
description: Find files by path pattern, newest first. Use when you know the name or extension. Not for searching file contents - use grep.
category: builtin
tags: [search, files]
---

# glob

Matches paths against a glob pattern and returns them sorted by modification
time, newest first.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| pattern | string | yes | Glob such as `**/*.py` |
| path | string | no | Directory to search from; defaults to the working directory |
| limit | integer | no | Maximum paths to return |

## Behavior

- Recency ordering means the files someone touched most recently come first,
  which is usually what you want when orienting in a repo.
- Ignored paths (`.gitignore`) are skipped.

## Limits

- Matches paths only. Finding text inside files is `grep`.
