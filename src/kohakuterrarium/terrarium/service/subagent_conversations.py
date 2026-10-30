"""Read sub-agent transcripts and deliver messages on the owning runtime."""

import re
from typing import Any

from kohakuterrarium.errors import ConflictError, InvalidRequestError, NotFoundError
from kohakuterrarium.core.conversation.conversation import Conversation
from kohakuterrarium.modules.subagent.interactive.interactive import InteractiveSubAgent

_JOB_ID_RE = re.compile(r"^agent_(?P<name>.+)_[0-9a-f]{8}$")


def _parent_name(agent: Any) -> str:
    return getattr(getattr(agent, "subagent_manager", None), "_parent_name", "") or (
        getattr(getattr(agent, "config", None), "name", "") or ""
    )


def _side_enabled(agent: Any) -> bool:
    return bool(getattr(getattr(agent, "config", None), "fork_enabled", True))


def _side_running(manager: Any, job_id: str, child: Any = None) -> bool:
    task = getattr(manager, "_tasks", {}).get(job_id)
    return bool(task is not None and not task.done()) or bool(
        getattr(child, "is_running", False)
    )


def _subagent_name_from_job_id(job_id: str) -> str | None:
    match = _JOB_ID_RE.match(job_id)
    return match.group("name") if match else None


def _latest_persisted_run(store: Any, parent: str, name: str) -> int | None:
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
        "interactive": interactive,
        "can_receive": can_receive,
        "messages": messages,
        "meta": meta,
    }


def read_subagent_conversation(
    agent: Any,
    session_id: str,
    creature_id: str,
    *,
    job_id: str | None = None,
    name: str | None = None,
    run: int | None = None,
) -> dict[str, Any]:
    """Read a live child or its persisted run, preferring an exact job ID."""
    manager = getattr(agent, "subagent_manager", None)
    if job_id:
        subagent = manager.get_live_subagent(job_id) if manager is not None else None
        if subagent is not None:
            conversation = getattr(subagent, "conversation", None)
            messages = conversation.to_messages() if conversation is not None else []
            user_side = getattr(subagent, "_audience", None) == "user"
            running = bool(getattr(subagent, "is_running", False))
            snapshot = getattr(subagent, "_snapshot", None)
            meta = (
                {
                    "audience": "user",
                    "inherited_message_count": getattr(
                        subagent, "_inherited_message_count", 0
                    ),
                    "source": snapshot.provenance if snapshot is not None else {},
                }
                if user_side
                else None
            )
            if user_side and callable(getattr(manager, "side_conversation", None)):
                _, side_meta = manager.side_conversation(job_id)
                meta.update(side_meta)
            return _payload(
                creature_id,
                session_id,
                name=subagent.config.name,
                run=None,
                job_id=job_id,
                live=True,
                interactive=isinstance(subagent, InteractiveSubAgent),
                can_receive=(
                    _side_enabled(agent)
                    and not _side_running(manager, job_id, subagent)
                    if user_side
                    else running
                ),
                messages=messages,
                meta=meta,
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
    agent: Any, creature_id: str, session_id: str, name: str, run: int
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
    job_id = meta.get("job_id") if isinstance(meta, dict) else None
    return _payload(
        creature_id,
        session_id,
        name=name,
        run=run,
        job_id=job_id,
        live=False,
        interactive=False,
        can_receive=bool(
            job_id
            and meta.get("audience") == "user"
            and _side_enabled(agent)
            and not _side_running(getattr(agent, "subagent_manager", None), job_id)
        ),
        messages=messages,
        meta=meta,
    )


def _read_persisted_by_job_id(
    agent: Any, creature_id: str, session_id: str, job_id: str
) -> dict[str, Any] | None:
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
        agent, creature_id, session_id, str(row["name"]), int(row["run"])
    )


async def send_to_subagent(
    agent: Any,
    name: str,
    content: str,
    *,
    job_id: str | None = None,
) -> dict[str, Any]:
    """Message a running child or continue an exact user-side thread."""
    if not content.strip():
        raise InvalidRequestError("message content is required")
    manager = getattr(agent, "subagent_manager", None)
    if manager is not None:
        child = manager.get_live_subagent(job_id) if job_id else None
        if not job_id:
            interactive = manager.get_interactive(name)
            if interactive is None or not interactive.is_active:
                named_child = manager._live_task_by_name(name)
                if getattr(named_child, "_audience", None) == "user":
                    raise ConflictError(
                        "Side conversations require an exact job_id and must be idle"
                    )
        user_side = getattr(child, "_audience", None) == "user"
        if job_id and child is None:
            store = getattr(agent, "session_store", None)
            find_run = getattr(store, "find_subagent_run", None)
            row = (
                find_run(job_id, parent=_parent_name(agent))
                if callable(find_run)
                else None
            )
            user_side = bool(row and (row.get("meta") or {}).get("audience") == "user")
        if user_side:
            if _side_running(manager, job_id, child):
                raise ConflictError("Side conversation is still running")
            try:
                continued_id = await manager.continue_side(job_id, content)
            except ValueError as exc:
                raise InvalidRequestError(str(exc)) from exc
            return {"status": "sent", "name": name, "job_id": continued_id}
        if await manager.send_to_subagent(content, job_id=job_id, name=name):
            return {"status": "sent", "name": name, "job_id": job_id}
    raise ConflictError(
        f"no live sub-agent to receive the message (job_id={job_id!r}, "
        f"name={name!r}); completed task runs are read-only"
    )
