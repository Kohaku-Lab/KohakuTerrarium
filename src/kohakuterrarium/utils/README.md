# utils/

Shared utilities and helpers used across the framework. Provides a custom
colored logger based on the `logging` module (format:
`[HH:MM:SS] [module.name] [LEVEL] message`) and common async patterns
for timeouts, retries, concurrency limiting, and thread offloading.

## Files

| File             | Description                                                                                                                       |
| ---------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `__init__.py`    | Re-exports logging and async utility functions                                                                                    |
| `logging.py`     | `get_logger`, `set_level`, `disable_colors`: colored structured logging with ANSI codes                                           |
| `kt_logger.py`   | `KTLogger`: `Logger` subclass whose level methods take structured fields as keyword arguments                                     |
| `async_utils.py` | `run_with_timeout`, `gather_with_concurrency`, `retry_async`, `collect_async_iterator`, `first_result`, `AsyncQueue`, `to_thread` |
| `file_ignore.py` | Per-search directory-scoped `.gitignore` rules, ancestor inheritance and excluded-parent caching |
| `file_walk.py` | Bounded file/directory iteration and glob filtering for built-in search tools |

## Dependencies

Logging and async helpers use Python stdlib. Ignore matching uses `pathspec`
to compile patterns to Python regular expressions; runtime traversal does not
invoke Git.

## Search ignore rules

`GitIgnoreFilter` keeps a per-search cache of each directory's rules and parent
exclusion decisions. Scope starts at the nearest ancestor containing `.git`
(file or directory), or the caller's search root outside a repository. This
preserves ancestor rules when a recursive glob narrows traversal to a prefix.
Only `.gitignore` is read: global excludes, `.git/info/exclude`, Git's index and
Git configuration are not consulted. Case handling preserves the previous
`fnmatch` convention: insensitive on Windows, sensitive on POSIX.

Pattern compilation uses `pathspec`'s `GitIgnoreSpecPattern`. Matching applies
last-match precedence to the entry itself; ancestor matches are handled by
cached parent decisions. This avoids directory re-inclusions overriding a
separate exclusion of a child, and preserves pruning of excluded parents.
Trailing `/**/` is expanded to `/**/*/` before compilation so the pattern
matches descendant directories without excluding the prefix directory itself.
The unit suite compares these combinations with `git check-ignore --no-index`.

The cache lasts for one invocation, including unreadable or absent ignore
files. The next search sees edits to rules. Invalid patterns are skipped
without discarding valid lines. Hidden-entry and fixed-directory exclusions
are independent and retain their existing behavior; `gitignore=false` only
turns off `.gitignore` processing. Shallow glob candidates retain `Path.glob`
semantics and receive the same ignore predicate before result limits apply.
