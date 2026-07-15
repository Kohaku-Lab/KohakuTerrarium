"""Expose informational attachment-policy hints for live runtime targets.

Policies describe available inspection bindings but do not gate Chat or
Inspector access. Unknown or stopped targets return 404 so callers can omit the
hint without treating it as a runtime failure.
"""

from fastapi import APIRouter, Depends, HTTPException

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.studio.attach import policies as policy_lib
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()


def _host_engine(service):
    """Resolve a host-local engine while preserving lightweight test doubles.

    Multi-node services have no local agent engine. Single-host services expose
    ``engine``; legacy callers may pass the engine object directly. Attribute
    checks avoid protocol ``isinstance`` requirements on simple fakes.
    """
    if hasattr(service, "connected_nodes"):
        return None
    return getattr(service, "engine", service)


@router.get("/policies/{creature_id}")
async def get_creature_policies(
    creature_id: str,
    service: TerrariumService = Depends(get_service),
) -> dict[str, list[str]]:
    """Return order-stable attachment policy codes for a creature or graph.

    Local creatures resolve against the host engine. Multi-node targets fall
    back to service routing, and the identifier is also tried as a session ID
    because the Inspector uses this compatibility route for graph hints.
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
    # Only multi-node services own the home registry needed to route remote
    # creature and session policy lookups.
    is_multi_node = hasattr(service, "_home")
    if is_multi_node:
        svc_fn = getattr(service, "attach_policies", None)
        if callable(svc_fn):
            try:
                return {"policies": list(await svc_fn(creature_id))}
            except KeyError:
                pass
        # The compatibility endpoint also receives graph IDs from the Inspector.
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
    """Return attachment policy codes for a local or worker-hosted graph."""
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
