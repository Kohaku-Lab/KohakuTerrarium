"""Standalone agent routes."""

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from kohakuterrarium.api.deps import get_manager
from kohakuterrarium.api.schemas import (
    AgentChat,
    AgentCreate,
    MessageEdit,
    ModelSwitch,
    SlashCommand,
)
from kohakuterrarium.session.history import collect_branch_metadata

router = APIRouter()


# Env var keys that must be filtered out of /env responses (case-insensitive).
_ENV_REDACT_SUBSTRINGS = (
    "secret",
    "key",
    "token",
    "password",
    "pass",
    "private",
    "auth",
    "credential",
)


def _redacted_env() -> dict[str, str]:
    """Return a sanitized copy of os.environ with credentials filtered out."""
    out = {}
    for k, v in os.environ.items():
        lk = k.lower()
        if any(sub in lk for sub in _ENV_REDACT_SUBSTRINGS):
            continue
        out[k] = v
    return out


class ScratchpadPatch(BaseModel):
    """Scratchpad update payload. Null value → delete the key."""

    updates: dict[str, str | None]


@router.post("")
async def create_agent(req: AgentCreate, manager=Depends(get_manager)):
    """Create and start a standalone agent."""
    try:
        agent_id = await manager.agent_create(
            config_path=req.config_path, llm_override=req.llm, pwd=req.pwd
        )
        return {"agent_id": agent_id, "status": "running"}
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("")
async def list_agents(manager=Depends(get_manager)):
    """List all running agents."""
    return manager.agent_list()


@router.get("/{agent_id}")
async def get_agent(agent_id: str, manager=Depends(get_manager)):
    """Get status of a specific agent."""
    try:
        return manager.agent_status(agent_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.delete("/{agent_id}")
async def stop_agent(agent_id: str, manager=Depends(get_manager)):
    """Stop and cleanup an agent."""
    try:
        await manager.agent_stop(agent_id)
        return {"status": "stopped"}
    except ValueError as e:
        raise HTTPException(404, str(e))


class WorkingDirRequest(BaseModel):
    """Switch the agent's tool-side cwd. Body shape: ``{"path": "<dir>"}``.

    The new path is resolved server-side (``~`` expansion + absolute) and
    must exist as a directory. Rejected with 409 when the agent is in the
    middle of a turn — interrupt first.
    """

    path: str


@router.get("/{agent_id}/working-dir")
async def get_agent_working_dir(agent_id: str, manager=Depends(get_manager)):
    """Return the agent's current working directory."""
    try:
        return {"pwd": manager.agent_get_working_dir(agent_id)}
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.put("/{agent_id}/working-dir")
async def set_agent_working_dir(
    agent_id: str,
    req: WorkingDirRequest,
    manager=Depends(get_manager),
):
    """Switch the agent's tool-side cwd mid-session.

    Updates the executor's working directory, rebuilds the path-boundary
    guard against the new root, clears file-read tracking, and persists
    the new pwd to session metadata so resume restores it.
    """
    try:
        applied = manager.agent_set_working_dir(agent_id, req.path)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        # Distinguish "agent not found" (404) from "bad path" (400) by
        # looking at the message — both raise ValueError today.
        msg = str(e)
        if msg.startswith("Agent not found"):
            raise HTTPException(404, msg)
        raise HTTPException(400, msg)
    return {"status": "saved", "pwd": applied}


class NativeToolOptionsRequest(BaseModel):
    """Partial update for one provider-native tool's option values.

    Body shape: ``{"tool": "image_gen", "values": {"size": "...", ...}}``.
    Empty / missing values clear the override (tool reverts to its
    constructor defaults).
    """

    tool: str
    values: dict[str, Any] = {}


@router.get("/{agent_id}/native-tool-options")
async def get_agent_native_tool_options(agent_id: str, manager=Depends(get_manager)):
    """Return the agent's session-wise provider-native tool overrides.

    Combines the registered tools (with their option schema) and the
    user-set override values. Frontends render this directly into a
    schema-driven form.
    """
    try:
        return {
            "tools": manager.agent_native_tool_inventory(agent_id),
        }
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.put("/{agent_id}/native-tool-options")
async def set_agent_native_tool_options(
    agent_id: str,
    req: NativeToolOptionsRequest,
    manager=Depends(get_manager),
):
    """Replace the override dict for one provider-native tool."""
    try:
        applied = manager.agent_set_native_tool_options(
            agent_id, req.tool, req.values or {}
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"status": "saved", "tool": req.tool, "values": applied}


@router.post("/{agent_id}/interrupt")
async def interrupt_agent(agent_id: str, manager=Depends(get_manager)):
    """Interrupt the agent's current processing. Agent stays alive."""
    try:
        manager.agent_interrupt(agent_id)
        return {"status": "interrupted"}
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/{agent_id}/regenerate")
async def regenerate_response(agent_id: str, manager=Depends(get_manager)):
    """Regenerate the last assistant response (uses current model/settings)."""
    session = manager._agents.get(agent_id)
    if not session:
        raise HTTPException(404, f"Agent {agent_id} not found")
    await session.agent.regenerate_last_response()
    return {"status": "regenerating"}


@router.post("/{agent_id}/messages/{msg_idx}/edit")
async def edit_message(
    agent_id: str,
    msg_idx: int,
    req: MessageEdit,
    manager=Depends(get_manager),
):
    """Edit a user message at a given index and re-run from there."""
    session = manager._agents.get(agent_id)
    if not session:
        raise HTTPException(404, f"Agent {agent_id} not found")
    edited = await session.agent.edit_and_rerun(
        msg_idx,
        req.content,
        turn_index=req.turn_index,
        user_position=req.user_position,
    )
    if not edited:
        raise HTTPException(400, "Invalid edit target; expected a user message")
    return {
        "status": "edited",
        "turn_index": req.turn_index,
        "user_position": req.user_position,
    }


@router.post("/{agent_id}/messages/{msg_idx}/rewind")
async def rewind_conversation(
    agent_id: str, msg_idx: int, manager=Depends(get_manager)
):
    """Drop messages from msg_idx onward without re-running."""
    session = manager._agents.get(agent_id)
    if not session:
        raise HTTPException(404, f"Agent {agent_id} not found")
    await session.agent.rewind_to(msg_idx)
    return {"status": "rewound"}


@router.post("/{agent_id}/promote/{job_id}")
async def promote_task(agent_id: str, job_id: str, manager=Depends(get_manager)):
    """Promote a running direct task to background."""
    try:
        session = manager._agents.get(agent_id)
        if not session:
            raise ValueError(f"Agent {agent_id} not found")
        ok = session.agent._promote_handle(job_id)
        return {"status": "promoted" if ok else "not_found"}
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/{agent_id}/plugins")
async def list_plugins(agent_id: str, manager=Depends(get_manager)):
    """List plugins and their enabled/disabled status."""
    session = manager._agents.get(agent_id)
    if not session:
        raise HTTPException(404, f"Agent {agent_id} not found")
    if not session.agent.plugins:
        return []
    return session.agent.plugins.list_plugins()


@router.post("/{agent_id}/plugins/{plugin_name}/toggle")
async def toggle_plugin(agent_id: str, plugin_name: str, manager=Depends(get_manager)):
    """Enable or disable a plugin at runtime."""
    session = manager._agents.get(agent_id)
    if not session:
        raise HTTPException(404, f"Agent {agent_id} not found")
    if not session.agent.plugins:
        raise HTTPException(404, "No plugins loaded")
    mgr = session.agent.plugins
    if mgr.is_enabled(plugin_name):
        mgr.disable(plugin_name)
        return {"name": plugin_name, "enabled": False}
    else:
        mgr.enable(plugin_name)
        await mgr.load_pending()
        return {"name": plugin_name, "enabled": True}


@router.get("/{agent_id}/jobs")
async def agent_jobs(agent_id: str, manager=Depends(get_manager)):
    """List running background jobs for an agent."""
    try:
        return manager.agent_get_jobs(agent_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/{agent_id}/tasks/{job_id}/stop")
async def stop_agent_task(agent_id: str, job_id: str, manager=Depends(get_manager)):
    """Stop a specific background task."""
    try:
        if await manager.agent_cancel_job(agent_id, job_id):
            return {"status": "cancelled", "job_id": job_id}
        raise HTTPException(404, f"Task not found or already completed: {job_id}")
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/{agent_id}/history")
async def agent_history(agent_id: str, manager=Depends(get_manager)):
    """Get conversation history + event log for a standalone agent.

    Returns the raw event stream including sibling branches; clients
    are expected to compute branch metadata client-side via the same
    helpers used by ``replay_conversation``. Use ``/branches`` for a
    pre-aggregated turn→branches map.
    """
    try:
        session = manager._agents.get(agent_id)
        history = manager.agent_get_history(agent_id)
        is_processing = False
        if session is not None:
            is_processing = bool(getattr(session.agent, "_processing_task", None))
        return {"agent_id": agent_id, "events": history, "is_processing": is_processing}
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/{agent_id}/branches")
async def agent_branches(agent_id: str, manager=Depends(get_manager)):
    """Return per-turn branch metadata for the navigator UI.

    Response shape::

        {
          "agent_id": "...",
          "turns": [
            {"turn_index": 1, "branches": [1, 2], "latest_branch": 2},
            ...
          ]
        }
    """
    try:
        events = manager.agent_get_history(agent_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    meta = collect_branch_metadata(events)
    turns = [
        {
            "turn_index": ti,
            "branches": info["branches"],
            "latest_branch": info["latest_branch"],
        }
        for ti, info in sorted(meta.items())
    ]
    return {"agent_id": agent_id, "turns": turns}


@router.post("/{agent_id}/model")
async def switch_agent_model(
    agent_id: str, req: ModelSwitch, manager=Depends(get_manager)
):
    """Switch the agent's LLM model mid-session."""
    try:
        model = manager.agent_switch_model(agent_id, req.model)
        return {"status": "switched", "model": model}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{agent_id}/command")
async def execute_command(
    agent_id: str, req: SlashCommand, manager=Depends(get_manager)
):
    """Execute a slash command on an agent (e.g. /model, /status)."""
    try:
        return await manager.agent_execute_command(agent_id, req.command, req.args)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{agent_id}/chat")
async def chat_agent(agent_id: str, req: AgentChat, manager=Depends(get_manager)):
    """Non-streaming chat with an agent."""
    try:
        content = req.content if req.content is not None else (req.message or "")
        chunks = []
        async for chunk in manager.agent_chat(agent_id, content):
            chunks.append(chunk)
        return {"response": "".join(chunks)}
    except ValueError as e:
        raise HTTPException(404, str(e))


# ----------------------------------------------------------------------
# Read-only inspection endpoints (added for frontend refactor Phase 1).
# Every endpoint below is a thin wrapper over existing agent runtime
# state. None of them add new backend behavior; they only expose state
# the manager already tracks.
# ----------------------------------------------------------------------


@router.get("/{agent_id}/scratchpad")
async def get_scratchpad(agent_id: str, manager=Depends(get_manager)) -> dict[str, str]:
    """Return the agent's current scratchpad as a dict."""
    session = manager._agents.get(agent_id)
    if not session:
        raise HTTPException(404, f"Agent {agent_id} not found")
    return session.agent.scratchpad.to_dict()


@router.patch("/{agent_id}/scratchpad")
async def patch_scratchpad(
    agent_id: str, req: ScratchpadPatch, manager=Depends(get_manager)
) -> dict[str, str]:
    """Merge updates into the scratchpad.

    Each entry in ``req.updates`` either sets a key to the given string
    value or, when the value is ``None``, deletes the key.
    """
    session = manager._agents.get(agent_id)
    if not session:
        raise HTTPException(404, f"Agent {agent_id} not found")
    pad = session.agent.scratchpad
    for key, value in req.updates.items():
        if value is None:
            pad.delete(key)
        else:
            pad.set(key, value)
    return pad.to_dict()


@router.get("/{agent_id}/triggers")
async def list_agent_triggers(
    agent_id: str, manager=Depends(get_manager)
) -> list[dict[str, Any]]:
    """Return the active triggers on an agent (read-only)."""
    session = manager._agents.get(agent_id)
    if not session:
        raise HTTPException(404, f"Agent {agent_id} not found")
    tm = session.agent.trigger_manager
    if tm is None:
        return []
    return [
        {
            "trigger_id": info.trigger_id,
            "trigger_type": info.trigger_type,
            "running": info.running,
            "created_at": info.created_at.isoformat(),
        }
        for info in tm.list()
    ]


@router.get("/{agent_id}/env")
async def get_agent_env(agent_id: str, manager=Depends(get_manager)) -> dict[str, Any]:
    """Return the agent's working directory and redacted environment."""
    session = manager._agents.get(agent_id)
    if not session:
        raise HTTPException(404, f"Agent {agent_id} not found")
    agent = session.agent
    pwd = getattr(agent, "_working_dir", None) or os.getcwd()
    return {
        "pwd": str(pwd),
        "env": _redacted_env(),
    }


@router.get("/{agent_id}/system-prompt")
async def get_agent_system_prompt(
    agent_id: str, manager=Depends(get_manager)
) -> dict[str, str]:
    """Return the agent's current assembled system prompt."""
    session = manager._agents.get(agent_id)
    if not session:
        raise HTTPException(404, f"Agent {agent_id} not found")
    return {"text": session.agent.get_system_prompt()}
