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

## Behavior

- Python `re` syntax, not ripgrep or shell grep; escape `(`, `[`, and `.`.
- Binary files are skipped.
- Search stops when `limit` matches have been collected, including within a
  single file. The output then says more matches may exist; no exhaustive
  total is computed. Narrow the pattern or file filter to refine the results.

## Limits

- Lines over 2000 characters are truncated in the output.

## Reference

### Output format

```
src/main.py:10: def main():
src/utils.py:25: def helper(x):

(2 matches in 15 files)
```
