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
| gitignore | boolean | no | Follow scoped `.gitignore` rules; default true. Set false to disable this filtering |

## Behavior

- Recency ordering means the files someone touched most recently come first,
  which is usually what you want when orienting in a repo.
- Recursive and non-recursive patterns respect directory-scoped `.gitignore`
  rules, including anchored paths, nested overrides and `!` re-inclusion.
  Excluded parents must be re-included before their children can be searched.
- Ancestor rules are loaded up to the nearest repository root. Outside a
  repository, rules start at the requested search directory.
- `gitignore=false` disables only `.gitignore` filtering. Recursive traversal
  still skips dot-prefixed entries and built-in dependency/cache directories.
- Git's index, global excludes and `.git/info/exclude` are not consulted.

## Limits

- Matches paths only. Finding text inside files is `grep`.
