"""Quoted-exchange builder: the last few prompts and replies of a session.

Backs the history-row accordion and the viewer overview. Reads the saved
conversation snapshot of one agent (the primary one by default).
"""

from typing import Any

from kohakuterrarium.errors import NotFoundError
from kohakuterrarium.session import run_state
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.session_index.exchange import (
    primary_agent,
    recent_exchanges,
)

EXCHANGE_TEXT_LIMIT = 2000
MAX_EXCHANGES = 20


def build_exchanges_payload(
    store: SessionStore,
    session_name: str,
    *,
    agent: str | None = None,
    limit: int = 3,
) -> dict[str, Any]:
    """``{session_name, agent, title, summary, stop_reason, turn_count, exchanges}``."""
    meta = store.load_meta()
    agents = list(meta.get("agents") or [])
    if agent is not None and agent not in agents:
        raise NotFoundError(f"Agent not found in session: {agent}")
    agent = agent or primary_agent(meta)
    messages = store.load_conversation(agent) if agent else None
    every = recent_exchanges(messages, len(messages or []), EXCHANGE_TEXT_LIMIT)
    count = max(1, min(int(limit), MAX_EXCHANGES))
    summary = meta.get("summary") if isinstance(meta.get("summary"), dict) else {}
    return {
        "session_name": session_name,
        "agent": agent,
        "agents": agents,
        "title": str(meta.get("name") or ""),
        "summary": str(summary.get("text") or ""),
        "summary_source": str(summary.get("source") or ""),
        "stop_reason": run_state.stop_reason(run_state.read_lifecycle(meta)),
        "turn_count": len(every),
        "exchanges": every[-count:],
    }
