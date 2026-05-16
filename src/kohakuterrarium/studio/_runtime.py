"""Transitional helper for the Studio → TerrariumService migration.

The Studio class's runtime dependency is a :class:`TerrariumService`
(single-host: :class:`LocalTerrariumService` wrapping the in-process
:class:`Terrarium` engine). Studio's namespace methods pass that
service to the studio submodules below.

Studio submodule functions today operate on a raw :class:`Terrarium`
engine internally (``engine.add_creature(...)``,
``engine.subscribe(...)``, etc.). To accept both the new service form
*and* legacy raw-engine callers (api/routes/, cli/, tests) during the
migration, every public submodule function calls :func:`as_engine` on
its incoming runtime argument:

.. code-block:: python

    def start_creature(service: "TerrariumService", ...):
        engine = as_engine(service)
        # ... existing body operates on the engine

This file is internal to the studio layer. When the api/* layer
migrates to call Studio class methods (instead of importing submodules
directly), and when submodule bodies migrate to consume Protocol
methods on the service (replacing ``engine.foo()`` with
``service.foo()`` as the Protocol surface grows), this shim is no
longer needed and disappears.
"""

from kohakuterrarium.terrarium import Terrarium
from kohakuterrarium.terrarium.service import TerrariumService


def as_engine(runtime) -> Terrarium:
    """Return the underlying :class:`Terrarium` engine from either form.

    Args:
        runtime: One of:

            * A :class:`TerrariumService` (e.g. :class:`LocalTerrariumService`
              passed by the Studio class) — returns ``runtime.engine``.
            * A raw :class:`Terrarium` engine (legacy callers) — returns
              ``runtime`` unchanged.

    Returns:
        The underlying :class:`Terrarium` engine.

    Notes:
        :class:`TerrariumService` is a ``runtime_checkable`` Protocol,
        so the ``isinstance`` check is structural — anything exposing
        the full Protocol surface counts as a service. A raw Terrarium
        (which lacks ``node_id``, ``get_creature_info``, etc.) falls
        through to the else branch.
    """
    if isinstance(runtime, TerrariumService):
        return runtime.engine
    return runtime


def host_engine_or_none(runtime) -> Terrarium | None:
    """The host-local agent engine, or ``None`` in lab-host mode.

    - **standalone** (``LocalTerrariumService`` / raw ``Terrarium``):
      returns the host-local engine — the same thing :func:`as_engine`
      returns.
    - **lab-host** (``MultiNodeTerrariumService``): returns ``None``.
      The host runs no agent engine; ``as_engine`` would *raise*.
      Callers use this when they have a legitimate dual path — a
      host-local engine walk AND a service-Protocol / ``_meta`` remote
      branch — so the ``None`` simply selects the remote branch instead
      of blowing up.

    The discriminator is ``connected_nodes`` — only
    ``MultiNodeTerrariumService`` exposes it.  Use :func:`as_engine`
    (not this) when the function genuinely cannot work without a host
    engine: there it *should* raise loudly rather than silently no-op.
    """
    if hasattr(runtime, "connected_nodes"):
        return None
    return as_engine(runtime)


__all__ = ["as_engine", "host_engine_or_none"]
