"""Inspect sub-agent conversations and message live sub-agents.

Live runs resolve through the parent manager by job ID or interactive name;
persisted runs resolve through the session store. Sub-agent internals are not
part of the service protocol, so this surface is available only when the host
owns the creature's agent engine.
"""

import re
from typing import Any

from kohakuterrarium.errors import ConflictError, InvalidRequestError, NotFoundError
from kohakuterrarium.core.conversation import Conversation
from kohakuterrarium.studio._runtime import host_engine_or_none
from kohakuterrarium.terrarium.service import TerrariumService
from kohakuterrarium.modules.subagent.interactive import InteractiveSubAgent
from kohakuterrarium.studio.sessions.lifecycle import find_creature

# Names may contain underscores, so job IDs are parsed from the fixed hex suffix.
_JOB_ID_RE = re.compile(r"^agent_(?P<name>.+)_[0-9a-f]{8}$")


def _agent(service: "TerrariumService", session_id: str, creature_id: str) -> Any:
    # Multi-node hosts cannot inspect worker-local sub-agent internals.
    engine = host_engine_or_none(service)
    if engine is None:
        raise NotFoundError("sub-agent inspection is not available in multi-node mode")
    return find_creature(engine, session_id, creature_id).agent


def _parent_name(agent: Any) -> str:
    """The key ``SubAgent`` persists under — the agent's config name,
    recorded on the manager as ``_parent_name`` in ``attach_session_store``.
    """
    return getattr(getattr(agent, "subagent_manager", None), "_parent_name", "") or (
        getattr(getattr(agent, "config", None), "name", "") or ""
    )


def _subagent_name_from_job_id(job_id: str) -> str | None:
    match = _JOB_ID_RE.match(job_id)
    return match.group("name") if match else None


def _latest_persisted_run(store: Any, parent: str, name: str) -> int | None:
    """Highest persisted run index for ``(parent, name)``, or ``None``.

    The store's ``_subagent_runs`` counter holds the NEXT index (restored
    from persisted ``:meta`` keys on resume); an allocated-but-unsaved run
    can leave it one ahead, so probe downward to the first run that
    actually persisted.
    """
    upper = getattr(store, "_subagent_runs", {}).get(f"{parent}:{name}", 0)
    for run in range(int(upper) - 1, -1, -1):
        if store.load_subagent_meta(parent, name, run) is not None:
            return run
    return None


def _payload(
    creature_id: str,
    session_id: str,
    *,
    name: str,
    run: int | None,
    job_id: str | None,
    live: bool,
    interactive: bool,
    can_receive: bool,
    messages: list[dict[str, Any]],
    meta: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "creature_id": creature_id,
        "session_id": session_id,
        "name": name,
        "run": run,
        "job_id": job_id,
        "live": live,
        # ``interactive`` describes the subtype; ``can_receive`` reflects whether
        # this specific live run can currently accept inbox messages.
        "interactive": interactive,
        "can_receive": can_receive,
        "messages": messages,
        "meta": meta,
    }


def read_subagent_conversation(
    service: "TerrariumService",
    session_id: str,
    creature_id: str,
    *,
    job_id: str | None = None,
    name: str | None = None,
    run: int | None = None,
) -> dict[str, Any]:
    """Return a sub-agent run's conversation.

    ``job_id`` reads a live task sub-agent, falling back to the exact
    persisted run carrying that identifier when the job is no longer live.
    ``name`` alone
    reads a live interactive child, else falls back to the LATEST
    persisted run for that name; ``name`` + ``run`` reads a specific
    persisted snapshot. Raises :class:`NotFoundError` when the referenced
    run cannot be found and :class:`InvalidRequestError` when no usable
    identifier was supplied.

    ``can_receive`` is ``True`` for ANY live, still-running instance — a
    one-shot sub-agent is messageable via its inbox while it runs, not
    only an interactive child — so the frontend shows the chat box for
    any live sub-agent. Completed / persisted runs are read-only.
    """
    agent = _agent(service, session_id, creature_id)
    manager = getattr(agent, "subagent_manager", None)

    if job_id:
        subagent = manager.get_live_subagent(job_id) if manager is not None else None
        if subagent is not None:
            conversation = getattr(subagent, "conversation", None)
            messages = conversation.to_messages() if conversation is not None else []
            return _payload(
                creature_id,
                session_id,
                name=subagent.config.name,
                run=None,
                job_id=job_id,
                live=True,
                interactive=isinstance(subagent, InteractiveSubAgent),
                can_receive=bool(getattr(subagent, "is_running", False)),
                messages=messages,
                meta=None,
            )
        fallback = _read_persisted_by_job_id(agent, creature_id, session_id, job_id)
        if fallback is not None:
            return fallback
        raise NotFoundError(f"sub-agent for job {job_id!r} not found")

    if not name:
        raise InvalidRequestError("provide job_id, or name (with optional run)")

    if run is None and manager is not None:
        interactive = manager.get_interactive(name)
        if interactive is not None:
            conversation = getattr(interactive, "conversation", None)
            messages = conversation.to_messages() if conversation is not None else []
            return _payload(
                creature_id,
                session_id,
                name=name,
                run=None,
                job_id=None,
                live=True,
                interactive=True,
                can_receive=bool(getattr(interactive, "is_active", False)),
                messages=messages,
                meta=None,
            )

    if run is None:
        # Name-only reads fall back to the latest persisted transcript when no
        # interactive child is live.
        store = getattr(agent, "session_store", None)
        latest = (
            _latest_persisted_run(store, _parent_name(agent), name)
            if store is not None
            else None
        )
        if latest is None:
            raise NotFoundError(
                f"no live sub-agent {name!r} and no saved run; pass a run "
                "index for a specific saved conversation"
            )
        run = latest

    return _read_persisted(agent, creature_id, session_id, name, run)


def _read_persisted(
    agent: Any,
    creature_id: str,
    session_id: str,
    name: str,
    run: int,
) -> dict[str, Any]:
    store = getattr(agent, "session_store", None)
    if store is None:
        raise NotFoundError("session store not available for sub-agent read")
    parent = _parent_name(agent)
    conv_json = store.load_subagent_conversation(parent, name, run)
    meta = store.load_subagent_meta(parent, name, run)
    if conv_json is None and meta is None:
        raise NotFoundError(f"sub-agent run {parent}:{name}:{run} not found")
    messages = Conversation.from_json(conv_json).to_messages() if conv_json else []
    return _payload(
        creature_id,
        session_id,
        name=name,
        run=run,
        job_id=meta.get("job_id") if isinstance(meta, dict) else None,
        live=False,
        interactive=False,
        can_receive=False,
        messages=messages,
        meta=meta,
    )


def _read_persisted_by_job_id(
    agent: Any,
    creature_id: str,
    session_id: str,
    job_id: str,
) -> dict[str, Any] | None:
    """Resolve a non-live job exactly, or by one legacy candidate."""
    store = getattr(agent, "session_store", None)
    find_run = getattr(store, "find_subagent_run", None) if store is not None else None
    if not callable(find_run):
        return None
    parent = _parent_name(agent)
    row = find_run(job_id, parent=parent)
    if row is None:
        name = _subagent_name_from_job_id(job_id)
        list_runs = getattr(store, "list_subagent_runs", None)
        if not name or not callable(list_runs):
            return None
        legacy = [
            candidate
            for candidate in list_runs(parent=parent, name=name)
            if not candidate.get("job_id")
        ]
        if len(legacy) > 1:
            raise ConflictError(f"multiple legacy runs match sub-agent {parent}:{name}")
        row = legacy[0] if legacy else None
    if row is None:
        return None
    return _read_persisted(
        agent,
        creature_id,
        session_id,
        str(row["name"]),
        int(row["run"]),
    )


async def send_to_subagent(
    service: "TerrariumService",
    session_id: str,
    creature_id: str,
    name: str,
    content: str,
    *,
    job_id: str | None = None,
) -> dict[str, Any]:
    """Deliver a live user message to a RUNNING sub-agent.

    ``job_id`` targets one exact live run (preferred — unambiguous across
    concurrent same-name runs); ``name`` is the fallback. Rejects with
    :class:`ConflictError` when there is no live target — completed /
    persisted runs are read-only.
    """
    agent = _agent(service, session_id, creature_id)
    manager = getattr(agent, "subagent_manager", None)
    delivered = False
    if manager is not None:
        delivered = await manager.send_to_subagent(content, job_id=job_id, name=name)
    if not delivered:
        raise ConflictError(
            f"no live sub-agent to receive the message (job_id={job_id!r}, "
            f"name={name!r}); completed runs are read-only"
        )
    return {"status": "sent", "name": name, "job_id": job_id}
