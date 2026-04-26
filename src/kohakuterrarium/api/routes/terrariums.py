"""Terrarium CRUD + lifecycle + chat routes."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from kohakuterrarium.api.deps import get_manager
from kohakuterrarium.api.events import get_event_log
from kohakuterrarium.api.routes.agents import _redacted_env
from kohakuterrarium.api.schemas import AgentChat, ChannelAdd, TerrariumCreate
from kohakuterrarium.core.scratchpad import is_reserved_scratchpad_key
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


class ScratchpadPatch(BaseModel):
    updates: dict[str, str | None]


def _mount_target(manager, terrarium_id: str, target: str):
    if target.startswith("ch:"):
        raise HTTPException(400, f"Target {target} is a channel, not an agent")
    try:
        return manager.terrarium_mount(terrarium_id, target)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("")
async def create_terrarium(req: TerrariumCreate, manager=Depends(get_manager)):
    """Create and start a terrarium from a config path."""
    try:
        tid = await manager.terrarium_create(config_path=req.config_path, pwd=req.pwd)
        return {"terrarium_id": tid, "status": "running"}
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("")
async def list_terrariums(manager=Depends(get_manager)):
    """List all running terrariums."""
    return manager.terrarium_list()


@router.get("/{terrarium_id}")
async def get_terrarium(terrarium_id: str, manager=Depends(get_manager)):
    """Get status of a specific terrarium."""
    try:
        return manager.terrarium_status(terrarium_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.delete("/{terrarium_id}")
async def stop_terrarium(terrarium_id: str, manager=Depends(get_manager)):
    """Stop and cleanup a terrarium."""
    try:
        await manager.terrarium_stop(terrarium_id)
        return {"status": "stopped"}
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/{terrarium_id}/channels")
async def add_channel(terrarium_id: str, req: ChannelAdd, manager=Depends(get_manager)):
    """Add a channel to a running terrarium."""
    try:
        await manager.terrarium_channel_add(
            terrarium_id, req.name, req.channel_type, req.description
        )
        return {"status": "created", "channel": req.name}
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/{terrarium_id}/history/{target}")
async def terrarium_history(
    terrarium_id: str, target: str, manager=Depends(get_manager)
):
    """Get full history for a creature or root agent.

    target: "root" for root agent, or creature name.
    Returns conversation messages + event log.

    Events are read from the SessionStore (persistent, survives resume)
    with fallback to the in-memory event log.
    """
    try:
        # Channel history: read from channels table
        if target.startswith("ch:"):
            ch_name = target[3:]
            runtime = manager._get_runtime(terrarium_id)
            store = runtime.session_store
            messages = []
            if store:
                messages = store.get_channel_messages(ch_name)
            return {
                "terrarium_id": terrarium_id,
                "target": target,
                "messages": [],
                "events": [
                    {
                        "type": "channel_message",
                        "channel": ch_name,
                        "sender": m.get("sender", ""),
                        "content": m.get("content", ""),
                        "ts": m.get("ts", 0),
                    }
                    for m in messages
                ],
                "is_processing": False,
            }

        # Agent/creature history
        session = manager.terrarium_mount(terrarium_id, target)
        mount_key = f"{terrarium_id}:{target}"

        # Prefer SessionStore events (persistent, works after resume)
        events = []
        agent = session.agent
        if hasattr(agent, "session_store") and agent.session_store:
            try:
                events = agent.session_store.get_resumable_events(target)
            except Exception as e:
                logger.debug(
                    "Failed to load session events", error=str(e), exc_info=True
                )

        # Fallback to in-memory log
        if not events:
            events = get_event_log(mount_key)

        return {
            "terrarium_id": terrarium_id,
            "target": target,
            "messages": agent.conversation_history,
            "events": events,
            "is_processing": bool(getattr(agent, "_processing_task", None)),
        }
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/{terrarium_id}/chat/{target}")
async def chat_terrarium(
    terrarium_id: str,
    target: str,
    req: AgentChat,
    manager=Depends(get_manager),
):
    """Non-streaming chat with a creature or root agent."""
    try:
        content = req.content if req.content is not None else (req.message or "")
        chunks = []
        async for chunk in manager.terrarium_chat(terrarium_id, target, content):
            chunks.append(chunk)
        return {"response": "".join(chunks)}
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/{terrarium_id}/scratchpad/{target}")
async def terrarium_scratchpad(
    terrarium_id: str, target: str, manager=Depends(get_manager)
):
    session = _mount_target(manager, terrarium_id, target)
    return session.agent.scratchpad.to_dict()


@router.patch("/{terrarium_id}/scratchpad/{target}")
async def patch_terrarium_scratchpad(
    terrarium_id: str,
    target: str,
    req: ScratchpadPatch,
    manager=Depends(get_manager),
):
    session = _mount_target(manager, terrarium_id, target)
    pad = session.agent.scratchpad
    for key, value in req.updates.items():
        if is_reserved_scratchpad_key(key):
            raise HTTPException(400, f"Reserved scratchpad key: {key}")
        if value is None:
            pad.delete(key)
        else:
            pad.set(key, value)
    return pad.to_dict()


@router.get("/{terrarium_id}/triggers/{target}")
async def terrarium_triggers(
    terrarium_id: str, target: str, manager=Depends(get_manager)
):
    session = _mount_target(manager, terrarium_id, target)
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


@router.get("/{terrarium_id}/plugins/{target}")
async def terrarium_plugins(
    terrarium_id: str, target: str, manager=Depends(get_manager)
):
    session = _mount_target(manager, terrarium_id, target)
    if not session.agent.plugins:
        return []
    return session.agent.plugins.list_plugins()


@router.post("/{terrarium_id}/plugins/{target}/{plugin_name}/toggle")
async def terrarium_toggle_plugin(
    terrarium_id: str, target: str, plugin_name: str, manager=Depends(get_manager)
):
    session = _mount_target(manager, terrarium_id, target)
    if not session.agent.plugins:
        raise HTTPException(404, "No plugins loaded")
    mgr = session.agent.plugins
    if mgr.is_enabled(plugin_name):
        mgr.disable(plugin_name)
        return {"name": plugin_name, "enabled": False}
    mgr.enable(plugin_name)
    await mgr.load_pending()
    return {"name": plugin_name, "enabled": True}


@router.get("/{terrarium_id}/env/{target}")
async def terrarium_env(terrarium_id: str, target: str, manager=Depends(get_manager)):
    session = _mount_target(manager, terrarium_id, target)
    agent = session.agent
    pwd = getattr(agent, "_working_dir", None)
    return {"pwd": str(pwd) if pwd is not None else "", "env": _redacted_env()}


@router.get("/{terrarium_id}/system-prompt/{target}")
async def terrarium_system_prompt(
    terrarium_id: str, target: str, manager=Depends(get_manager)
):
    session = _mount_target(manager, terrarium_id, target)
    return {"text": session.agent.get_system_prompt()}
