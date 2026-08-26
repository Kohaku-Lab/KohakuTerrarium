"""Build read-only persisted sub-agent run and conversation payloads."""

from typing import Any

from kohakuterrarium.core.conversation import Conversation
from kohakuterrarium.errors import ConflictError, InvalidRequestError, NotFoundError
from kohakuterrarium.session.store import SessionStore


def _public_run(row: dict[str, Any]) -> dict[str, Any]:
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    return {
        "parent": row["parent"],
        "name": row["name"],
        "run": row["run"],
        "job_id": row.get("job_id"),
        "task": meta.get("task", ""),
        "success": meta.get("success"),
        "turns": meta.get("turns", 0),
        "ts": meta.get("ts"),
        "output_preview": meta.get("output_preview", ""),
        "source": meta.get("source"),
    }


def build_subagent_runs_payload(
    store: SessionStore,
    session_name: str,
    *,
    parent: str | None,
    name: str | None,
) -> dict[str, Any]:
    """Return persisted sub-agent runs suitable for viewer selection."""
    runs = [
        _public_run(row) for row in store.list_subagent_runs(parent=parent, name=name)
    ]
    return {"session_name": session_name, "runs": runs}


def build_subagent_conversation_payload(
    store: SessionStore,
    session_name: str,
    *,
    parent: str,
    job_id: str | None,
    name: str | None,
    run: int | None,
) -> dict[str, Any]:
    """Return one persisted conversation with honest legacy resolution."""
    if not parent:
        raise InvalidRequestError("parent is required")

    resolution = "explicit_run"
    row: dict[str, Any] | None = None
    if job_id:
        row = store.find_subagent_run(job_id, parent=parent)
        if row is not None:
            resolution = "exact_job_id"
            if name is not None and row["name"] != name:
                raise ConflictError(
                    "job_id does not match the requested sub-agent name"
                )
            if run is not None and row["run"] != run:
                raise ConflictError("job_id does not match the requested sub-agent run")
        elif name:
            candidates = store.list_subagent_runs(parent=parent, name=name)
            legacy = [
                candidate for candidate in candidates if not candidate.get("job_id")
            ]
            if len(legacy) == 1:
                row = legacy[0]
                resolution = "unique_legacy_candidate"
            elif len(legacy) > 1:
                raise ConflictError(
                    f"multiple legacy runs match sub-agent {parent}:{name}"
                )
    elif name and run is not None:
        meta = store.load_subagent_meta(parent, name, run)
        if meta is not None:
            row = {
                "parent": parent,
                "name": name,
                "run": run,
                "job_id": meta.get("job_id") if isinstance(meta, dict) else None,
                "meta": meta,
            }
    else:
        raise InvalidRequestError("provide job_id, or name and run")

    if row is None:
        raise NotFoundError("sub-agent conversation not found")

    resolved_name = str(row["name"])
    resolved_run = int(row["run"])
    conv_json = store.load_subagent_conversation(parent, resolved_name, resolved_run)
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else None
    if conv_json is None and meta is None:
        raise NotFoundError(
            f"sub-agent run {parent}:{resolved_name}:{resolved_run} not found"
        )
    messages = Conversation.from_json(conv_json).to_messages() if conv_json else []
    return {
        "session_id": store.session_id,
        "session_name": session_name,
        "parent": parent,
        "name": resolved_name,
        "run": resolved_run,
        "job_id": row.get("job_id"),
        "live": False,
        "interactive": False,
        "can_receive": False,
        "messages": messages,
        "meta": meta,
        "resolution": resolution,
    }
