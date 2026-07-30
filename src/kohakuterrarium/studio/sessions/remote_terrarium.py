"""Start Studio terrarium sessions on a selected laboratory worker."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from kohakuterrarium.studio.deploy import deploy_creature_to_node
from kohakuterrarium.studio.sessions.handles import Session
from kohakuterrarium.studio.sessions.registry import meta_for
from kohakuterrarium.terrarium.config import load_terrarium_config
from kohakuterrarium.terrarium.multi_node_service import MultiNodeTerrariumService
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


async def start_remote_terrarium(
    service: MultiNodeTerrariumService,
    *,
    config_path: str | None,
    name: str | None,
    pwd: str | None,
    llm: str | None,
    on_node: str,
) -> Session:
    """Deploy and apply one complete recipe on one explicitly selected worker."""
    if on_node == "_host":
        raise ValueError("Lab recipe deployment requires an explicit worker node")
    if on_node not in service.connected_nodes():
        raise KeyError(f"worker node {on_node!r} is not connected")
    if not config_path:
        raise ValueError("remote recipe deployment requires config_path")

    source_path = Path(config_path).resolve()
    if source_path.is_file():
        allowed = {source_path.name, "system.md", "config.yaml", "config.yml"}
        unexpected = [
            item.name
            for item in source_path.parent.iterdir()
            if item.is_file() and item.name not in allowed
        ]
        if unexpected:
            raise ValueError(
                "remote recipe directory contains unrelated files; "
                f"move the recipe into a dedicated directory: {unexpected[0]!r}"
            )
    bundle_path = source_path.parent if source_path.is_file() else source_path
    remote_root = await deploy_creature_to_node(
        service.host,
        on_node,
        bundle_path,
        name=f"recipe-{source_path.stem}",
    )
    remote_recipe = (
        str(Path(remote_root) / source_path.name)
        if source_path.is_file()
        else str(remote_root)
    )

    remote_service = service.service_for(on_node)
    graph, creatures = await remote_service.apply_recipe(
        remote_recipe,
        pwd=pwd,
        llm=llm,
        persist=True,
    )
    registered_ids: list[str] = []
    try:
        collisions = [
            creature.creature_id
            for creature in creatures
            if creature.creature_id in service._home
            and service._home[creature.creature_id] != on_node
        ]
        if collisions:
            raise ValueError(f"remote recipe creature_id collision: {collisions[0]!r}")
        for creature in creatures:
            service._home[creature.creature_id] = on_node
            registered_ids.append(creature.creature_id)
        requested_name = name.strip() if name and name.strip() else None
        base_name = requested_name or load_terrarium_config(config_path).name
        existing_names = {entry.get("name") for entry in meta_for(service).values()}
        session_name = base_name
        suffix = 2
        while session_name in existing_names:
            session_name = f"{base_name} #{suffix}"
            suffix += 1
        created_at = datetime.now(timezone.utc).isoformat()
    except BaseException:
        for creature_id in registered_ids:
            if service._home.get(creature_id) == on_node:
                service._home.pop(creature_id, None)
        try:
            await _discard_remote_recipe(remote_service, graph.graph_id)
        except BaseException:
            logger.exception(
                "remote recipe compensation failed",
                extra={"graph_id": graph.graph_id},
            )
        raise
    meta_for(service)[graph.graph_id] = {
        "session_id": graph.graph_id,
        "name": session_name,
        "kind": "terrarium",
        "status": "running",
        "created_at": created_at,
        "on_node": on_node,
        "creature_id": creatures[0].creature_id if creatures else None,
        "creature_ids": [creature.creature_id for creature in creatures],
        "creatures": [creature.name for creature in creatures],
    }
    return Session(
        session_id=graph.graph_id,
        name=session_name,
        creatures=[
            {
                "creature_id": creature.creature_id,
                "name": creature.name,
                "home_node": on_node,
            }
            for creature in creatures
        ],
        channels=[{"name": channel} for channel in sorted(graph.channels)],
        created_at=created_at,
        config_path=config_path,
        pwd=pwd or "",
        has_root=any(creature.is_privileged for creature in creatures),
        home_node=on_node,
    )


async def _discard_remote_recipe(remote_service, graph_id: str) -> None:
    """Finish the worker-side discard even when the caller is cancelled."""
    cleanup = asyncio.create_task(remote_service.discard_recipe(graph_id))
    try:
        await asyncio.shield(cleanup)
    except asyncio.CancelledError:
        await cleanup
        raise
