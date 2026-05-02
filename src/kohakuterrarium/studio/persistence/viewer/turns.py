"""Per-turn rollup pagination for the Session Viewer.

Verbatim port of ``_session_viewer.py:build_turns_payload`` — drives
the trace timeline + the Cost tab's per-turn aggregation.
"""

from typing import Any

from fastapi import HTTPException

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.viewer.rollups import (
    aggregate_turn_rollups,
    rollups_or_derived,
)


def build_turns_payload(
    store: SessionStore,
    session_name: str,
    *,
    agent: str | None,
    from_turn: int | None,
    to_turn: int | None,
    limit: int,
    offset: int,
    aggregate: bool = False,
) -> dict[str, Any]:
    """Paginated rollup rows. Drives the trace timeline + collapsed list.

    When ``aggregate`` is true, sum across **every** agent in the
    session (main + attached) and include a per-agent ``breakdown``
    field in each row. ``agent`` is then ignored. Used by the Cost tab
    to show a unified per-turn view of the whole session.
    """
    meta = store.load_meta()

    if aggregate:
        rows = aggregate_turn_rollups(store)
        agent_used = None
    else:
        main_agents = list(meta.get("agents") or [])
        attached_namespaces = [
            e["namespace"]
            for e in store.discover_attached_agents()
            if e.get("namespace")
        ]
        known_agents = main_agents + [
            n for n in attached_namespaces if n not in main_agents
        ]
        if agent is None:
            default = meta.get("viewer_default_agent")
            if isinstance(default, str) and default in known_agents:
                agent = default
            elif main_agents:
                agent = main_agents[0]
            else:
                raise HTTPException(404, f"Session has no agents: {session_name}")
        elif agent not in known_agents:
            raise HTTPException(404, f"Agent not found in session: {agent}")
        rows = rollups_or_derived(store, agent)
        agent_used = agent

    if from_turn is not None:
        rows = [r for r in rows if int(r.get("turn_index", -1)) >= from_turn]
    if to_turn is not None:
        rows = [r for r in rows if int(r.get("turn_index", -1)) <= to_turn]
    total = len(rows)
    page = rows[offset : offset + limit]
    return {
        "session_name": session_name,
        "agent": agent_used,
        "aggregate": aggregate,
        "turns": page,
        "total": total,
        "offset": offset,
        "limit": limit,
        "from_turn": from_turn,
        "to_turn": to_turn,
    }
