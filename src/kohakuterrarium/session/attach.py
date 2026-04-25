"""Wave F — attach / detach primitives between :class:`Agent` and :class:`Session`.

The module implements the locked Q3 decision from
``plans/session-system/implementation-plan.md`` §2.3: agents can be
explicitly attached to a running session at runtime, and the attached
agent's events land in the host session under a dedicated namespace
``<host_agent>:attached:<role>:<attach_seq>:e<seq>``.

Public entry points:

* :func:`attach_agent_to_session` — bind ``agent`` to ``session`` under
  ``role``. Called by :meth:`Agent.attach_to_session` /
  :meth:`Session.attach_agent` (they mirror each other).
* :func:`detach_agent_from_session` — unbind; flushes pending output,
  emits ``agent_detached`` in the host namespace, stops routing events
  into the host store.

Constraint: one session per agent (``AlreadyAttachedError`` on a
second attach to a *different* session). Re-attach to the same
session after a detach is allowed and bumps ``<attach_seq>`` for that
``(host, role)`` pair.
"""

import time
from typing import TYPE_CHECKING, Any

from kohakuterrarium.session.errors import (
    AlreadyAttachedError,
    NotAttachedError,
)
from kohakuterrarium.session.output import SessionOutput
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.core.agent import Agent
    from kohakuterrarium.session.session import Session
    from kohakuterrarium.session.store import SessionStore


logger = get_logger(__name__)


# Sentinel attribute attached to Agent instances holding their active
# attach metadata. Kept as a private dunder so callers rely on the
# public :meth:`Agent.attach_to_session` surface instead of poking at
# the internals.
_ATTACH_STATE_ATTR = "_wave_f_attach_state"


def _host_agent_name(session: "Session") -> str:
    """Return the host agent's namespace for the given session.

    The host is the primary agent that owns the :class:`SessionStore`.
    Discovery priority:

    1. ``session.agent.config.name`` — explicit owning Agent (typical
       programmatic case: ``Session(store, agent=my_agent)``).
    2. ``meta["agents"][0]`` — resume / root-agent case where the
       session was opened without an attached Agent.
    3. ``"host"`` — final fallback, used only when the session has no
       known agents yet (first attach on a blank store).
    """
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
    """State-table key for per-(host, role) monotonic attach counter."""
    return f"attach_seq:{host}:{role}"


def _next_attach_seq(store: "SessionStore", host: str, role: str) -> int:
    """Return the next ``attach_seq`` for ``(host, role)`` and persist it.

    Counter lives in the session store's ``state`` table so it survives
    process restarts. First attach for a role returns ``0``; second
    returns ``1``, etc.
    """
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
    """``<host>:attached:<role>:<attach_seq>`` — the event namespace."""
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
    """Append ``agent_attached`` / ``agent_detached`` in the host's namespace."""
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
    """Attach ``agent`` to ``session`` under ``role`` (Wave F entry point).

    * Writes the attach event namespace (``<host>:attached:<role>:<seq>``)
      to the agent via a dedicated :class:`SessionOutput` secondary sink.
    * Emits ``agent_attached`` in the host agent's namespace.
    * Mirrors the state onto ``agent`` so :meth:`detach_from_session`
      can cleanly unwire later.

    Raises :class:`AlreadyAttachedError` when the agent is already
    attached to a *different* session. Re-attaching to the same session
    is a no-op.
    """
    store: "SessionStore | None" = getattr(session, "store", None)
    if store is None:
        raise ValueError("Session has no backing SessionStore")

    existing = getattr(agent, _ATTACH_STATE_ATTR, None)
    if existing is not None:
        if existing.get("session") is session:
            # Idempotent re-attach to the same session with the same
            # role — callers sometimes do this to refresh the sink; we
            # keep it cheap and silent.
            return
        raise AlreadyAttachedError(
            "Agent already attached to a different session; "
            "call detach_from_session() first."
        )

    host = _host_agent_name(session)
    attach_seq = _next_attach_seq(store, host, role)
    prefix = _build_event_key_prefix(host, role, attach_seq)

    # Mint a SessionOutput bound to the attached namespace and wire it
    # onto the agent's output router as a secondary sink. This mirrors
    # the pre-Wave-F ``attach_session_store`` behaviour for the primary
    # case, but keeps the agent's own default store/output intact so
    # ephemeral agents can be attached without losing their stdout.
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

    # Record state so detach can find the sink again. Tuple shape is
    # intentionally simple — Wave G's token-usage API only needs the
    # ``(session, role, attach_seq, prefix)`` quad.
    state = {
        "session": session,
        "store": store,
        "host": host,
        "role": role,
        "attach_seq": attach_seq,
        "prefix": prefix,
        "output": output,
    }
    setattr(agent, _ATTACH_STATE_ATTR, state)

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

    logger.info(
        "Agent attached to session",
        agent_name=agent.config.name,
        host=host,
        role=role,
        attach_seq=attach_seq,
        session_id=session_id,
    )


def detach_agent_from_session(agent: "Agent") -> None:
    """Detach ``agent`` from its currently-attached session.

    Flushes and unregisters the :class:`SessionOutput` secondary sink,
    emits ``agent_detached`` in the host namespace, and clears the
    attach state on the agent. Raises :class:`NotAttachedError` if the
    agent is not currently attached.
    """
    state = getattr(agent, _ATTACH_STATE_ATTR, None)
    if state is None:
        raise NotAttachedError("Agent is not attached to a session.")

    output: SessionOutput = state["output"]
    store: "SessionStore" = state["store"]
    host: str = state["host"]
    role: str = state["role"]
    attach_seq: int = state["attach_seq"]

    # Unwire from the router first so no further events slip into the
    # host stream while we're tearing down.
    router = getattr(agent, "output_router", None)
    if router is not None and hasattr(router, "remove_secondary"):
        router.remove_secondary(output)

    # Best-effort: flush any pending state the sink would normally
    # write on ``on_processing_end``. We do not run the full end-of-turn
    # snapshot here — the controller owns that — but the store itself
    # gets a flush so the ``agent_detached`` event is durable.
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
        delattr(agent, _ATTACH_STATE_ATTR)
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
    """Return the agent's current attach state dict, or ``None`` when not attached.

    Intended for test / introspection code; the keys are documented in
    :func:`attach_agent_to_session`.
    """
    state = getattr(agent, _ATTACH_STATE_ATTR, None)
    if state is None:
        return None
    return dict(state)
