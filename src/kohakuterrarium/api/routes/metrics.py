"""Process-wide metrics — REST snapshot.

Mounted at ``/api/metrics``. Currently a single endpoint
``GET /api/metrics/snapshot`` returns the entire aggregator state in
one shot — counters, sliding histograms (5-minute and 1-hour windows),
and the per-minute rate buckets the dashboard sparklines render.

Adding a websocket delta-stream is on the M3 milestone; the snapshot
shape is forward-compatible so the WS deltas can reuse the same field
names without a frontend migration.

The snapshot intentionally re-computes every histogram on each call
(no caching). Aggregator instance is process-wide; multiple browser
tabs polling at 5 s each costs a few hundred microseconds total.

Some gauges (running creatures / terrariums / jobs / MCP / sessions)
read directly off the engine + the active session bookkeeping rather
than living in the aggregator — they are instantaneous and labelling
them with closed cardinality is trivial. Putting them on the snapshot
keeps the frontend's single ``/api/metrics/snapshot`` poll covering
everything the Stats tab and the dashboard mini-strip need.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.serving.process_metrics import get_aggregator
from kohakuterrarium.studio._runtime import host_engine_or_none
from kohakuterrarium.studio.sessions import lifecycle as sessions_lifecycle
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()


@router.get("/snapshot")
def metrics_snapshot(
    service: TerrariumService = Depends(get_service),
) -> dict[str, Any]:
    """Return a full metrics snapshot.

    Cheap to compute (~1 ms for ~50 series). Polled every 5 s by the
    Stats tab; the dashboard mini-strip reuses the same payload.

    Service-routed: ``list_sessions`` / ``get_session`` already accept
    the ``TerrariumService`` (standalone OR multi-node), so the
    session-count gauges aggregate across workers in lab-host mode
    instead of reading an empty host engine.
    """
    aggregator = get_aggregator()
    snapshot = aggregator.snapshot()
    snapshot["gauges"] = _build_gauges(service)
    return snapshot


def _build_gauges(service: TerrariumService) -> dict[str, int]:
    """Read instantaneous gauges off live session state.

    Solo-vs-multi separation comes from the listing's creature count —
    1 creature is a solo session, 2+ is a multi-creature graph
    (matches the frontend's ``isMulti`` derivation).
    ``mcp_servers_connected`` peeks into the agent's MCP manager — only
    reachable for host-local creatures, so in lab-host mode (no host
    agent engine) it reports 0; a cross-node MCP count would need a
    dedicated service surface.
    """
    sessions = list(sessions_lifecycle.list_sessions(service))
    creatures_running = sum(1 for s in sessions if s.creatures <= 1)
    terrariums_running = sum(1 for s in sessions if s.creatures > 1)

    # Each session is a graph; its creature count is on the listing.
    # ``creatures_total`` counts every creature across every active
    # session (the dashboard's "Running" card uses this for the badge
    # in the section title).
    creatures_total = 0
    for s in sessions:
        try:
            full = sessions_lifecycle.get_session(service, s.session_id)
            creatures_total += len(full.creatures)
        except Exception:  # pragma: no cover — defensive
            pass

    # MCP — sample each reachable creature's manager.  Only host-local
    # creatures expose ``agent._mcp_manager``; in lab-host mode the
    # host runs no agents, so ``host_engine_or_none`` is ``None`` and
    # this gauge is 0 (documented above).
    mcp_connected = 0
    engine = host_engine_or_none(service)
    if engine is not None:
        for s in sessions:
            try:
                full = sessions_lifecycle.get_session(service, s.session_id)
                for c in full.creatures:
                    cid = c.get("creature_id")
                    if not cid:
                        continue
                    try:
                        creature = engine.get_creature(cid)
                    except KeyError:
                        continue
                    mgr = getattr(creature.agent, "_mcp_manager", None)
                    connected = getattr(mgr, "_sessions", None) if mgr else None
                    if connected:
                        mcp_connected += len(connected)
            except Exception:  # pragma: no cover — defensive
                pass

    return {
        "agents_running": creatures_total,
        "creatures_running": creatures_running,
        "terrariums_running": terrariums_running,
        "mcp_servers_connected": mcp_connected,
        "sessions_open": len(sessions),
    }
