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
| `async_utils.py` | `run_with_timeout`, `gather_with_concurrency`, `retry_async`, `collect_async_iterator`, `first_result`, `AsyncQueue`, `to_thread` |

## Dependencies

None (leaf module, uses only Python stdlib: `logging`, `asyncio`).
