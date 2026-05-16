"""Attach policy hint route.

Exposes the studio :mod:`kohakuterrarium.studio.attach.policies` helpers
over HTTP so the frontend Inspector Overview can render an "IO bindings"
hint for any running target.

The frontend treats these endpoints as **informational hints**, not as
gating mechanisms: every running target offers Chat and Inspector tabs
regardless of policy. Hence the routes return 404 (rather than a typed
error) when the target is not currently live — the frontend silently
omits the hint line.
"""

from fastapi import APIRouter, Depends, HTTPException

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.studio.attach import policies as policy_lib
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()


def _host_engine(service):
    """The host-local agent engine if any, else ``None``.

    Avoids :func:`host_engine_or_none` (which uses a Protocol
    ``isinstance`` check) so this route stays usable with the test
    suite's ``SimpleNamespace`` fakes.  Logic:

    - ``connected_nodes`` attribute → multi-node service → no host
      engine.
    - ``.engine`` attribute (single-host service) → that engine.
    - Otherwise → treat the argument itself as the engine (legacy
      raw-Terrarium injection).
    """
    if hasattr(service, "connected_nodes"):
        return None
    return getattr(service, "engine", service)


@router.get("/policies/{creature_id}")
async def get_creature_policies(
    creature_id: str,
    service: TerrariumService = Depends(get_service),
) -> dict[str, list[str]]:
    """Return the attach policies a single creature supports.

    Returns ``{"policies": ["log", "trace", ...]}`` — order-stable list
    of short codes from :class:`policy_lib.Policy`.

    Multi-node: when the host engine has no such creature but the
    service knows it (via the ``_home`` registry), fall back to
    asking the service for policies.  This surfaces actual policies
    for worker-hosted creatures instead of a blank 404.

    The frontend Inspector also hits this route with a *session*
    (graph) id — not just a creature id — so in multi-node mode an id
    that resolves as neither a creature falls through to
    session-policy resolution before 404-ing.  That is the reported
    ``GET /api/attach/policies/graph_... 404`` for a live worker
    session.
    """
    engine = _host_engine(service)
    if engine is not None:
        try:
            engine.get_creature(creature_id)
        except KeyError:
            engine = None
        else:
            policies = policy_lib.get_creature_policies(engine, creature_id)
            return {"policies": [p.value for p in policies]}
    # Not local (or in lab-host mode) — try the service.  Multi-node
    # service exposes ``attach_policies`` (routes by home node) and
    # ``session_attach_policies`` (for a graph id).  Guard with
    # ``_home`` so standalone services don't accidentally route.
    is_multi_node = hasattr(service, "_home")
    if is_multi_node:
        svc_fn = getattr(service, "attach_policies", None)
        if callable(svc_fn):
            try:
                return {"policies": list(await svc_fn(creature_id))}
            except KeyError:
                pass
        # The id may actually be a session / graph id — the
        # Inspector Overview keys its hint off the session id.
        sess_fn = getattr(service, "session_attach_policies", None)
        if callable(sess_fn):
            try:
                return {"policies": list(await sess_fn(creature_id))}
            except KeyError:
                pass
    raise HTTPException(404, f"creature {creature_id!r} not found")


@router.get("/session_policies/{session_id}")
async def get_session_policies(
    session_id: str,
    service: TerrariumService = Depends(get_service),
) -> dict[str, list[str]]:
    """Return the attach policies a whole session (graph) supports.

    Same fallback as :func:`get_creature_policies` — service-level
    ``session_attach_policies`` for worker-hosted graphs.
    """
    engine = _host_engine(service)
    if engine is not None:
        try:
            engine.get_graph(session_id)
        except KeyError:
            engine = None
        else:
            policies = policy_lib.get_session_policies(engine, session_id)
            return {"policies": [p.value for p in policies]}
    is_multi_node = hasattr(service, "_home")
    svc_fn = getattr(service, "session_attach_policies", None)
    if is_multi_node and callable(svc_fn):
        try:
            remote_policies = await svc_fn(session_id)
            return {"policies": list(remote_policies)}
        except KeyError:
            pass
    raise HTTPException(404, f"session {session_id!r} not found")
