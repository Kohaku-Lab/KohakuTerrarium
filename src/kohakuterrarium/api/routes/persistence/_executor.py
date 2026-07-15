"""Persistence-layer executor — thin alias over the shared I/O pool.

Persistence and catalog routes perform I/O-heavy fan-out that would otherwise
compete with unrelated framework ``to_thread`` calls. They share the dedicated
executor defined in :mod:`kohakuterrarium.api._io_executor`.

The persistence-named exports remain compatibility aliases. New callers should
use ``kohakuterrarium.api._io_executor`` directly.
"""

from kohakuterrarium.api._io_executor import _MAX_WORKERS  # noqa: F401
from kohakuterrarium.api._io_executor import get_io_executor, run_in_io_executor

# These aliases preserve the persistence-route import surface.
get_persistence_executor = get_io_executor
run_in_persistence_executor = run_in_io_executor


__all__ = ["get_persistence_executor", "run_in_persistence_executor"]
