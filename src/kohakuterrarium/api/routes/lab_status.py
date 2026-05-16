"""``/api/lab/status`` — operator-facing lab cluster snapshot.

A richer counterpart to ``/api/nodes`` aimed at the deployment side:
returns the lab mode, the bind address, and a per-client summary
(``node_id``, ``connected_at``, ``last_seen``, ``creatures``).

Standalone mode returns ``{"mode": "standalone", "clients": []}`` so
a deployment dashboard can show "single-host" without special-casing
the 404 from ``/api/nodes``.
"""

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/status")
async def lab_status(request: Request) -> dict[str, Any]:
    """Lab cluster snapshot for the deployment dashboard.

    Cheap enough for a per-second poll: no network round-trip, only
    reads ``HostEngine``'s already-tracked client roster.
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
