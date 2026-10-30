"""Publish merge resume successors after the destination checkpoint commits."""

from copy import deepcopy
from pathlib import Path

from kohakuterrarium.errors import SessionNotResumableError
from kohakuterrarium.core.conversation.continuation import (
    live_branch_path,
    persist_branch_view,
)
from kohakuterrarium.session.store.identity import new_conversation_id
from kohakuterrarium.session.resume.target import SUCCESSOR_KEY
from kohakuterrarium.session.store.records.snapshot import compact_state, snapshot_tag
from kohakuterrarium.session.store import SessionStore


def stage_successor(engine, source, targets, *, kind="merge") -> None:
    """Retire a source immediately; keep execution blocked until publication."""
    for target in targets:
        if not target.meta.get("conversation_id"):
            target.meta["conversation_id"] = new_conversation_id()
            target.checkpoint()
    refs = [
        {
            "path": str(Path(target.path).resolve()),
            "conversation_id": target.meta.get("conversation_id"),
        }
        for target in targets
    ]
    manifest = source.meta.get("live_graph_manifest")
    agents = (
        [member["name"] for member in manifest.get("creatures", [])]
        if isinstance(manifest, dict)
        else list(source.meta.get("agents") or [])
    )
    marker = {
        "kind": kind,
        "state": "pending",
        "targets": refs,
        "agents": agents,
    }
    source.meta[SUCCESSOR_KEY] = marker
    source.set_conversation_open(False)
    source.update_status("completed")
    source.checkpoint()
    pending = getattr(engine, "_pending_session_successors", None)
    if pending is None:
        pending = engine._pending_session_successors = []
    pending.append((source, marker))
    completed = getattr(engine, "_successor_checkpoint_paths", set())
    completed.difference_update(ref["path"] for ref in refs)


def publish_successors(engine, graph_id: str) -> None:
    """Checkpoint live branches and publish durably resolved successor routes."""
    pending = getattr(engine, "_pending_session_successors", [])
    destination = engine._session_stores[graph_id]
    graph = engine._topology.graphs[graph_id]
    for creature_id in graph.creature_ids:
        agent = engine._creatures[creature_id].agent
        if getattr(agent, "is_processing", False):
            continue
        output = getattr(agent, "_session_output", None)
        if output is not None:
            output.flush_sync()
        conversation = getattr(getattr(agent, "controller", None), "conversation", None)
        if conversation is None:
            continue
        name = agent.config.name
        view = live_branch_path(agent)
        tag = snapshot_tag(agent)
        if not view and not tag.get("context_cutoff_event_id"):
            continue
        destination.save_branch_snapshot(
            name,
            conversation.snapshot_messages(),
            branch=tag,
            watermark=destination.max_event_id(name),
            compact_state=compact_state(getattr(agent, "compact_manager", None)),
        )
        persist_branch_view(destination, name, view)
    destination.checkpoint()
    if not pending:
        return
    completed = getattr(engine, "_successor_checkpoint_paths", None)
    if completed is None:
        completed = engine._successor_checkpoint_paths = set()
    completed.add(str(Path(destination.path).resolve()))
    for source, marker in list(pending):
        if not all(ref["path"] in completed for ref in marker["targets"]):
            continue
        if any(not ref["conversation_id"] for ref in marker["targets"]):
            raise SessionNotResumableError(
                "Cannot publish a successor without conversation identity"
            )
        reopened = bool(getattr(source, "_closed", False))
        store = SessionStore(source.path, writer_lock=True) if reopened else source
        try:
            if store.meta.get(SUCCESSOR_KEY) != marker:
                raise SessionNotResumableError(
                    "Session successor changed during checkpoint"
                )
            ready = deepcopy(marker)
            ready["state"] = "ready"
            store.meta[SUCCESSOR_KEY] = ready
            store.checkpoint()
        finally:
            if reopened:
                store.close(update_status=False)
        pending.remove((source, marker))
