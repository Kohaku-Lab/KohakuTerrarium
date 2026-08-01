"""Public response construction for one remotely resumed session."""

from dataclasses import asdict
from pathlib import Path
from typing import Any

from kohakuterrarium.studio.sessions.handles import Session
from kohakuterrarium.studio.sessions.lifecycle import now_iso, register_session_meta
from kohakuterrarium.terrarium.service import TerrariumService


def build_single_remote_response(
    service: TerrariumService,
    *,
    sid: str,
    meta: dict[str, Any],
    on_node: str,
    path: Path,
    session_name: str,
    worker_pwd_exists: bool | None,
    remote_session_path: str,
    resumed_creatures: list[dict[str, Any]],
) -> dict[str, Any]:
    """Publish a complete remote handle after adoption and mirror persistence."""
    creatures_payload = resumed_creatures or [
        {"creature_id": str(agent), "name": str(agent)}
        for agent in (meta.get("agents") or [])
        if agent
    ]
    creature_ids = [
        str(creature["creature_id"])
        for creature in creatures_payload
        if creature.get("creature_id")
    ]
    primary_cid = creature_ids[0] if creature_ids else ""
    home = getattr(service, "_home", None)
    if isinstance(home, dict):
        for creature_id in creature_ids:
            home[creature_id] = on_node
    register_session_meta(
        service,
        sid,
        {
            "name": meta.get("terrarium_name") or meta.get("session_id") or sid,
            "config_path": meta.get("config_path", ""),
            "pwd": meta.get("pwd", ""),
            "on_node": on_node,
            "resumed_from": str(path),
            "remote_session_path": remote_session_path,
            "conversation_id": str(meta.get("conversation_id") or ""),
            "creature_id": primary_cid,
            "creature_ids": creature_ids,
        },
    )
    name = meta.get("terrarium_name") or session_name
    synthetic = Session(
        session_id=sid,
        name=name,
        creatures=creatures_payload,
        channels=[],
        has_root=bool(meta.get("terrarium_creatures")),
        pwd=meta.get("pwd", ""),
        created_at=now_iso(),
        config_path=meta.get("config_path", ""),
        home_node=on_node,
    )
    if worker_pwd_exists is not None:
        synthetic.pwd_exists = worker_pwd_exists
    return {
        "instance_id": sid,
        "type": ("terrarium" if meta.get("config_type") == "terrarium" else "agent"),
        "session_name": name,
        "session": asdict(synthetic),
        "on_node": on_node,
    }


__all__ = ["build_single_remote_response"]
