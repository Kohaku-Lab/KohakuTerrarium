---
name: grep
description: Search file contents by regex. Use to find where something is defined or used. Not for finding files by name - use glob.
category: builtin
tags: [search, content]
---

# grep

Searches file contents with Python regular expressions and returns matching
lines with their paths and line numbers.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| pattern | string | yes | Python regex |
| path | string | no | Directory or file to search; defaults to the working directory |
| glob | string | no | File filter, e.g. `**/*.py` |
| limit | integer | no | Maximum matches; must be positive, default 50 |
| ignore_case | boolean | no | Case-insensitive match |
| gitignore | boolean | no | Follow scoped `.gitignore` rules; default true. Set false to include paths excluded by those rules |

## Behavior

- Directory searches respect `.gitignore`; `gitignore=false` disables those
  rules, not hidden-item or built-in directory exclusions. An explicitly
  addressed single file bypasses directory filtering.
- Python `re` syntax, not ripgrep or shell grep; escape `(`, `[`, and `.`.
- Binary files are skipped.
- Search stops when `limit` matches have been collected, including within a
  single file. The output then says more matches may exist; no exhaustive
  total is computed. Narrow the pattern or file filter to refine the results.

## Limits

- Lines over 2000 characters are truncated in the output.

## Reference

### Ignore rules

Recursive and non-recursive file filters apply directory-scoped `.gitignore`
rules. Ancestors are loaded up to the nearest repository root; outside a
repository, rules start at the requested search directory. Anchored paths,
nested overrides and `!` re-inclusion are supported. An excluded parent must
be re-included before its children can be searched.

`gitignore=false` only disables `.gitignore` filtering. Recursive traversal
still skips dot-prefixed entries and built-in dependency/cache directories.
Git's index, global excludes and `.git/info/exclude` are not consulted, so a
tracked file can still match a search ignore rule.

### Output format

```
src/main.py:10: def main():
src/utils.py:25: def helper(x):

(2 matches in 15 files)
```
