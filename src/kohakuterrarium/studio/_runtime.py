"""Transitional helper for the Studio → TerrariumService migration.

Studio namespaces receive a :class:`TerrariumService`, while some lower-level
Studio modules still require direct engine operations and some legacy callers
still pass a raw :class:`Terrarium`. These helpers centralize that compatibility
boundary and distinguish runtimes with a host-local agent engine from
coordination-only multi-node hosts.
"""

from kohakuterrarium.terrarium import Terrarium
from kohakuterrarium.terrarium.service import TerrariumService


def as_engine(runtime) -> Terrarium:
    """Return an engine from either a service or a legacy raw engine.

    ``TerrariumService`` is runtime-checkable, so service detection is
    structural. Raw engines lack the complete protocol surface and pass through
    unchanged.
    """
    if isinstance(runtime, TerrariumService):
        return runtime.engine
    return runtime


def host_engine_or_none(runtime) -> Terrarium | None:
    """Return the host-local agent engine when one exists.

    Multi-node coordination hosts expose ``connected_nodes`` but no local agent
    engine, so callers with a valid remote path receive ``None``. Call
    :func:`as_engine` instead when absence of a local engine must remain an error.
    """
    if hasattr(runtime, "connected_nodes"):
        return None
    return as_engine(runtime)


__all__ = ["as_engine", "host_engine_or_none"]
