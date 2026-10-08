"""Merge a terrarium recipe into a running session.

Applies the recipe's channels and creatures into the session's existing
graph (not a fresh one) in the session's working folder, binds each new
creature to the session's store before starting it, and returns the new
creature ids. Hosted (local-engine) sessions only. Store binding and start
run inside the engine's recipe transaction, so a failure rolls the whole
merge back without splitting the graph. The session's creature cap is kept.
"""

from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from kohakuterrarium.errors import InvalidRequestError, NotFoundError
from kohakuterrarium.studio._runtime import host_engine_or_none
from kohakuterrarium.terrarium.config import load_terrarium_config
from kohakuterrarium.studio.sessions.registry import meta_for, session_pwd
from kohakuterrarium.studio.sessions.store_attach import (
    attach_session_store_for_creature,
)

if TYPE_CHECKING:
    from kohakuterrarium.terrarium import TerrariumService


async def apply_recipe_to_session(
    service: "TerrariumService",
    session_id: str,
    *,
    config_path: str,
) -> list[str]:
    """Apply ``config_path``'s recipe into ``session_id``; return the added creature ids.

    A relative ``config_path`` resolves against the session's working
    folder. Raises ``NotFoundError`` for an unknown session;
    ``InvalidRequestError`` for a worker-hosted session, an unreadable or
    empty recipe, or a recipe ``root`` joining a graph that already has a
    privileged node; and the engine's ``ValueError`` for name collisions.
    Every failure leaves the graph as it was.
    """
    engine = host_engine_or_none(service)
    graph = next(
        (
            g
            for g in (engine.list_graphs() if engine is not None else [])
            if g.graph_id == session_id
        ),
        None,
    )
    if graph is None:
        if (meta_for(service).get(session_id) or {}).get("on_node"):
            raise InvalidRequestError(
                "merging a recipe into a worker-hosted session is not supported"
            )
        raise NotFoundError(f"session {session_id!r} not found")

    pwd = session_pwd(service, session_id)
    source: str | Path = config_path
    if pwd and not config_path.startswith("@"):
        local = Path(config_path).expanduser()
        source = local if local.is_absolute() else Path(pwd) / local
    try:
        recipe = load_terrarium_config(source)
    except (yaml.YAMLError, AttributeError, TypeError, KeyError) as e:
        raise InvalidRequestError(f"not a valid terrarium recipe: {e}") from e
    if not recipe.creatures and recipe.root is None:
        raise InvalidRequestError(f"{config_path!r} defines no creatures")
    if recipe.root is not None and any(
        engine.get_creature(cid).is_privileged for cid in graph.creature_ids
    ):
        raise InvalidRequestError(
            "the recipe has a root, but this session already has a privileged node"
        )

    recipe.max_creatures = 0
    before = set(graph.creature_ids)

    async def bind_and_start(topo) -> None:
        added = sorted(set(topo.creature_ids) - before)
        for creature_id in added:
            await attach_session_store_for_creature(
                service, engine.get_creature(creature_id), config_type="agent"
            )
        for creature_id in added:
            await engine.start(creature_id)

    created: list[str] = []
    await engine.apply_recipe(
        recipe,
        graph=session_id,
        pwd=pwd,
        strict=False,
        start=False,
        session=False,
        created_ids=created,
        on_applied=bind_and_start,
    )
    return created
