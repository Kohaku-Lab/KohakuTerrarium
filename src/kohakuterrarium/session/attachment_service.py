"""Session attachment service.

Owns the concrete attach/detach runtime for binding an agent to a host
session namespace. This module is the single implementation backend used by
both ``Agent.attach_to_session`` and ``Session.attach_agent`` so the session
stack can depend on one lower-level service directly, without function-local
imports or sibling-module workaround patterns.
"""

import time
from typing import TYPE_CHECKING, Any

from kohakuterrarium.session.errors import AlreadyAttachedError, NotAttachedError
from kohakuterrarium.session.output import SessionOutput
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.core.agent import Agent
    from kohakuterrarium.session.session import Session
    from kohakuterrarium.session.store import SessionStore

logger = get_logger(__name__)

_ATTACHED_STATE_ATTR = "_wave_f_attach_state"


def _host_agent_name(session: "Session") -> str:
    """Return the host agent namespace for the given session."""
    agent = getattr(session, "agent", None)
    if agent is not None:
        cfg = getattr(agent, "config", None)
        name = getattr(cfg, "name", None) if cfg is not None else None
        if isinstance(name, str) and name:
            return name
    store = getattr(session, "store", None)
    if store is not None:
        try:
            meta = store.load_meta()
        except Exception as e:  # pragma: no cover — defensive
            logger.debug("load_meta failed in attach", error=str(e), exc_info=True)
            meta = {}
        agents = meta.get("agents") if isinstance(meta, dict) else None
        if isinstance(agents, list) and agents:
            first = agents[0]
            if isinstance(first, str) and first:
                return first
    return "host"


def _attach_seq_state_key(host: str, role: str) -> str:
    return f"attach_seq:{host}:{role}"


def _next_attach_seq(store: "SessionStore", host: str, role: str) -> int:
    key = _attach_seq_state_key(host, role)
    try:
        existing = store.state.get(key)
    except (KeyError, TypeError):
        existing = None
    if isinstance(existing, int):
        next_seq = existing + 1
    else:
        next_seq = 0
    try:
        store.state[key] = next_seq
    except Exception as e:  # pragma: no cover — defensive
        logger.debug("Failed to persist attach_seq", error=str(e), exc_info=True)
    return next_seq


def _build_event_key_prefix(host: str, role: str, attach_seq: int) -> str:
    return f"{host}:attached:{role}:{attach_seq}"


def _emit_lineage(
    store: "SessionStore",
    host: str,
    *,
    event_type: str,
    agent_name: str,
    role: str,
    attach_seq: int,
    attached_by: str,
    session_id: str,
) -> None:
    payload = {
        "agent_name": agent_name,
        "role": role,
        "attached_by": attached_by,
        "session_id": session_id,
        "attach_seq": attach_seq,
        "ts": time.time(),
    }
    try:
        store.append_event(host, event_type, payload)
    except Exception as e:  # pragma: no cover — observability
        logger.debug(
            "Lineage event emit failed",
            event_type=event_type,
            error=str(e),
            exc_info=True,
        )


def attach_agent_to_session(
    agent: "Agent",
    session: "Session",
    role: str,
    *,
    attached_by: str | None = None,
) -> None:
    """Attach ``agent`` to ``session`` under ``role``."""
    store: "SessionStore | None" = getattr(session, "store", None)
    if store is None:
        raise ValueError("Session has no backing SessionStore")

    existing = getattr(agent, _ATTACHED_STATE_ATTR, None)
    if existing is not None:
        if existing.get("session") is session:
            return
        raise AlreadyAttachedError(
            "Agent already attached to a different session; "
            "call detach_from_session() first."
        )

    host = _host_agent_name(session)
    attach_seq = _next_attach_seq(store, host, role)
    prefix = _build_event_key_prefix(host, role, attach_seq)

    output = SessionOutput(
        agent.config.name,
        store,
        agent,
        capture_activity=True,
        event_key_prefix=prefix,
    )
    router = getattr(agent, "output_router", None)
    if router is not None and hasattr(router, "add_secondary"):
        router.add_secondary(output)

    state = {
        "session": session,
        "store": store,
        "host": host,
        "role": role,
        "attach_seq": attach_seq,
        "prefix": prefix,
        "output": output,
    }
    setattr(agent, _ATTACHED_STATE_ATTR, state)

    session_id = ""
    try:
        session_id = store.session_id
    except Exception as e:  # pragma: no cover — defensive
        logger.debug("session_id read failed", error=str(e), exc_info=True)

    _emit_lineage(
        store,
        host,
        event_type="agent_attached",
        agent_name=agent.config.name,
        role=role,
        attach_seq=attach_seq,
        attached_by=attached_by or agent.config.name,
        session_id=session_id,
    )

    # Tell the session viewer to dispatch under the attach namespace
    # by default; otherwise the host namespace (which only carries
    # lineage events) wins and the conversation tab renders empty.
    # Last-attach wins; earlier attaches stay reachable via explicit
    # ``?agent=``. Stored in a viewer-only meta field so resume /
    # hot-plug / token-loop enumeration keep ``meta["agents"]`` clean.
    try:
        store.set_viewer_default_agent(prefix)
    except Exception as e:  # pragma: no cover — observability
        logger.debug(
            "set_viewer_default_agent failed",
            namespace=prefix,
            error=str(e),
            exc_info=True,
        )

    logger.info(
        "Agent attached to session",
        agent_name=agent.config.name,
        host=host,
        role=role,
        attach_seq=attach_seq,
        session_id=session_id,
    )


def detach_agent_from_session(agent: "Agent") -> None:
    """Detach ``agent`` from its currently attached session."""
    state = getattr(agent, _ATTACHED_STATE_ATTR, None)
    if state is None:
        raise NotAttachedError("Agent is not attached to a session.")

    output: SessionOutput = state["output"]
    store: "SessionStore" = state["store"]
    host: str = state["host"]
    role: str = state["role"]
    attach_seq: int = state["attach_seq"]

    router = getattr(agent, "output_router", None)
    if router is not None and hasattr(router, "remove_secondary"):
        router.remove_secondary(output)

    try:
        if hasattr(store, "flush"):
            store.flush()
    except Exception as e:  # pragma: no cover — observability
        logger.debug("Store flush on detach failed", error=str(e), exc_info=True)

    session_id = ""
    try:
        session_id = store.session_id
    except Exception as e:  # pragma: no cover — defensive
        logger.debug("session_id read failed", error=str(e), exc_info=True)

    _emit_lineage(
        store,
        host,
        event_type="agent_detached",
        agent_name=agent.config.name,
        role=role,
        attach_seq=attach_seq,
        attached_by=agent.config.name,
        session_id=session_id,
    )

    try:
        delattr(agent, _ATTACHED_STATE_ATTR)
    except AttributeError:
        pass

    logger.info(
        "Agent detached from session",
        agent_name=agent.config.name,
        host=host,
        role=role,
        attach_seq=attach_seq,
        session_id=session_id,
    )


def get_attach_state(agent: "Agent") -> dict[str, Any] | None:
    """Return the agent's current attach-state dict, or ``None``."""
    state = getattr(agent, _ATTACHED_STATE_ATTR, None)
    if state is None:
        return None
    return dict(state)
