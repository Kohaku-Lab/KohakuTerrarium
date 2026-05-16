"""Persistence-layer executor — thin alias over the shared I/O pool.

The persistence routes (saved list / disk usage / stats / delete) and
the catalog scan routes (creature / terrarium config discovery) both
do I/O-heavy fan-out work that competes with every other framework
``to_thread`` call.  They share one dedicated executor — see
:mod:`kohakuterrarium.api._io_executor` for the rationale and the
sizing decision.

The ``run_in_persistence_executor`` name is preserved here so existing
imports keep working; new callers should use
``kohakuterrarium.api._io_executor.run_in_io_executor`` directly.
"""

from kohakuterrarium.api._io_executor import _MAX_WORKERS  # noqa: F401
from kohakuterrarium.api._io_executor import get_io_executor, run_in_io_executor

# Back-compat aliases — existing imports still work.
get_persistence_executor = get_io_executor
run_in_persistence_executor = run_in_io_executor


__all__ = ["get_persistence_executor", "run_in_persistence_executor"]
