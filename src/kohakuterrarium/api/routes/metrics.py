"""Expose process metrics and live runtime gauges through one snapshot route.

Histogram and rate data comes from the process-wide aggregator, while gauges
that represent current runtime state are read from sessions and the host
engine. Keeping both sources in one response lets dashboard consumers poll a
single endpoint. Histograms are recomputed on every request because the
calculation is cheap and caching would make the snapshot stale.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.serving.process_metrics import get_aggregator
from kohakuterrarium.studio._runtime import host_engine_or_none
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()


def _session_lifecycle():
    from kohakuterrarium.studio.sessions import lifecycle

    return lifecycle


@router.get("/snapshot")
def metrics_snapshot(
    service: TerrariumService = Depends(get_service),
) -> dict[str, Any]:
    """Return aggregator metrics augmented with current runtime gauges.

    Session lookups use ``TerrariumService`` so counts aggregate across workers
    in lab-host mode rather than depending on the host engine, which may not
    contain agents.
    """
    aggregator = get_aggregator()
    snapshot = aggregator.snapshot()
    snapshot["gauges"] = _build_gauges(service)
    return snapshot


def _build_gauges(service: TerrariumService) -> dict[str, int]:
    """Compute session, creature, and reachable MCP connection gauges.

    A session with at most one creature is classified as solo; larger sessions
    are multi-creature terrariums. MCP managers are only accessible for
    host-local creatures, so the MCP gauge is zero when the service has no host
    engine.
    """
    sessions_lifecycle = _session_lifecycle()
    sessions = list(sessions_lifecycle.list_sessions(service))
    creatures_running = sum(1 for s in sessions if s.creatures <= 1)
    terrariums_running = sum(1 for s in sessions if s.creatures > 1)

    # Session summaries may not include the complete creature collection.
    # Fetch each full session before summing all active creatures.
    creatures_total = 0
    for s in sessions:
        try:
            full = sessions_lifecycle.get_session(service, s.session_id)
            creatures_total += len(full.creatures)
        except Exception:  # pragma: no cover — defensive
            pass

    # MCP manager internals are available only for creatures hosted by this
    # process; remote worker connections cannot be counted from the host.
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
