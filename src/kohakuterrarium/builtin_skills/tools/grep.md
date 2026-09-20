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
| limit | integer | no | Maximum matches, default 50 |
| ignore_case | boolean | no | Case-insensitive match |
| gitignore | boolean | no | Follow scoped `.gitignore` rules; default true. Set false to include paths excluded by those rules |

## Behavior

- Python `re` syntax, not ripgrep or shell grep; escape `(`, `[`, and `.`.
- Binary files are skipped.
- Directory searches apply `.gitignore` rules to recursive and non-recursive
  file filters. Ancestor rules are loaded up to the nearest repository root;
  outside a repository, rules start at the requested search directory.
- Rules support anchored paths, nested overrides and `!` re-inclusion. A file
  inside an excluded directory stays excluded unless its parent is re-included.
- `gitignore=false` only disables `.gitignore` filtering. Recursive traversal
  still skips dot-prefixed entries and built-in dependency/cache directories.
  An explicitly addressed single file bypasses directory filtering.
- This uses `.gitignore` files only, not Git's index, global excludes or
  `.git/info/exclude`. A tracked file can still match a search ignore rule.
- When matches exceed `limit`, the total count is reported so you know the
  pattern needs narrowing rather than the limit raising.

## Limits

- Lines over 2000 characters are truncated in the output.

## Reference

### Output format

```
src/main.py:10: def main():
src/utils.py:25: def helper(x):

(2 matches in 15 files)
```
