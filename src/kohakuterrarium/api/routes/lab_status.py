"""Expose a deployment-oriented snapshot of lab mode and client membership.

Unlike node-management routes, this endpoint returns an empty standalone
snapshot instead of 404 so deployment dashboards can represent a single-host
installation without a separate error path.
"""

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/status")
async def lab_status(request: Request) -> dict[str, Any]:
    """Return lab mode, bind address, and the host's tracked client roster.

    The route performs no worker round trips, so it is safe for frequent
    deployment-dashboard polling.
    """
    app = request.app
    lab_mode = getattr(app.state, "lab_mode", "standalone")
    out: dict[str, Any] = {
        "mode": lab_mode,
        "lab_bind": (
            getattr(app.state, "lab_bind", None) if lab_mode != "standalone" else None
        ),
        "clients": [],
    }
    host_engine = getattr(app.state, "lab_host_engine", None)
    if host_engine is None:
        return out

    # Prefer membership metadata for connection timestamps, but retain the
    # legacy alive-client surface when that registry is unavailable.
    membership = getattr(host_engine, "membership", None)
    roster: dict[str, dict[str, Any]] = {}
    if membership is not None and hasattr(membership, "roster"):
        try:
            for node_id, info in membership.roster().items():
                roster[node_id] = {
                    "node_id": node_id,
                    "connected_at": getattr(info, "connected_at", None),
                    "last_seen": getattr(info, "last_seen", None),
                }
        except Exception:
            pass
    else:
        for node_id in host_engine.alive_clients():
            roster[node_id] = {"node_id": node_id}

    out["clients"] = list(roster.values())
    out["client_count"] = len(roster)
    return out
