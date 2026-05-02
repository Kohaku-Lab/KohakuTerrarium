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

from kohakuterrarium.api.deps import get_engine
from kohakuterrarium.serving.process_metrics import get_aggregator
from kohakuterrarium.studio.sessions import lifecycle as sessions_lifecycle

router = APIRouter()


@router.get("/snapshot")
def metrics_snapshot(engine=Depends(get_engine)) -> dict[str, Any]:
    """Return a full metrics snapshot.

    Cheap to compute (~1 ms for ~50 series). Polled every 5 s by the
    Stats tab; the dashboard mini-strip reuses the same payload.
    """
    aggregator = get_aggregator()
    snapshot = aggregator.snapshot()
    snapshot["gauges"] = _build_gauges(engine)
    return snapshot


def _build_gauges(engine) -> dict[str, int]:
    """Read instantaneous gauges off live engine state.

    ``creature``-vs-``terrarium`` separation comes from the studio
    handle's ``kind`` field; ``mcp_servers_connected`` peeks into the
    agent's MCP manager because the engine doesn't surface it
    directly.
    """
    sessions = list(sessions_lifecycle.list_sessions(engine))
    creatures_running = sum(1 for s in sessions if s.kind == "creature")
    terrariums_running = sum(1 for s in sessions if s.kind == "terrarium")

    # Each session is a graph; its creature count is on the listing.
    # ``creatures_total`` counts every creature across every active
    # session (the dashboard's "Running" card uses this for the badge
    # in the section title).
    creatures_total = 0
    for s in sessions:
        try:
            full = sessions_lifecycle.get_session(engine, s.session_id)
            creatures_total += len(full.creatures)
        except Exception:  # pragma: no cover — defensive
            pass

    # MCP — sample the first running creature's manager. Multiple
    # creatures can attach independent MCP managers; aggregating
    # connection count meaningfully needs a process-wide registry the
    # framework doesn't have yet (cluster: ``mcp/client``). For now we
    # report a best-effort sum across creatures we can reach.
    mcp_connected = 0
    for s in sessions:
        try:
            full = sessions_lifecycle.get_session(engine, s.session_id)
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
