"""Compensation helpers for distributed cluster resume failures."""

import asyncio
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from kohakuterrarium.api.routes.persistence.resume_remote import (
    rollback_remote_workspace_meta,
)
from kohakuterrarium.studio.sessions import lifecycle

_MISSING = object()


@dataclass(frozen=True)
class ControllerStateSnapshot:
    """Controller registry and routing values replaced by one adoption."""

    session_id: str
    registry_value: Any
    home_values: dict[str, Any]


def snapshot_controller_state(
    service,
    session_id: str,
    creature_ids: Iterable[str],
) -> ControllerStateSnapshot:
    """Capture exact controller values before publishing resumed state."""
    registry = lifecycle.meta_for(service)
    previous = registry.get(session_id, _MISSING)
    if previous is not _MISSING:
        previous = deepcopy(previous)
    home = getattr(service, "_home", None)
    home_values: dict[str, Any] = {}
    if isinstance(home, dict):
        for creature_id in creature_ids:
            value = home.get(creature_id, _MISSING)
            home_values[creature_id] = (
                _MISSING if value is _MISSING else deepcopy(value)
            )
    return ControllerStateSnapshot(session_id, previous, home_values)


async def rollback_cluster_resume(
    service,
    resumed: dict[str, tuple[str, dict, str]],
    registered_session_ids: list[str],
    connected_pairs: list[tuple[str, str]],
    mirror_snapshots: Iterable[Any] = (),
    controller_snapshots: Iterable[ControllerStateSnapshot] = (),
) -> list[str]:
    """Best-effort cleanup in reverse side-effect order."""
    errors: list[str] = []
    for left, right in reversed(connected_pairs):
        try:
            await service.disconnect(left, right, channel="default")
        except BaseException as exc:
            errors.append(f"disconnect {left}/{right}: {exc}")

    snapshots = tuple(controller_snapshots)
    registry = lifecycle.meta_for(service)
    home = getattr(service, "_home", None)
    snapped_ids = {snapshot.session_id for snapshot in snapshots}
    for snapshot in reversed(snapshots):
        if snapshot.registry_value is _MISSING:
            registry.pop(snapshot.session_id, None)
        else:
            registry[snapshot.session_id] = deepcopy(snapshot.registry_value)
        if isinstance(home, dict):
            for creature_id, previous in snapshot.home_values.items():
                if previous is _MISSING:
                    home.pop(creature_id, None)
                else:
                    home[creature_id] = deepcopy(previous)
    for session_id in reversed(registered_session_ids):
        if session_id not in snapped_ids:
            registry.pop(session_id, None)

    for snapshot in reversed(tuple(mirror_snapshots)):
        try:
            await asyncio.to_thread(rollback_remote_workspace_meta, snapshot)
        except BaseException as exc:
            errors.append(f"rollback mirror {getattr(snapshot, 'path', '?')}: {exc}")

    host = getattr(service, "_host", None) or getattr(service, "host", None)
    for _original_sid, (new_sid, _new_meta, node) in reversed(list(resumed.items())):
        try:
            if host is None:
                raise RuntimeError("multi-node service host is unavailable")
            response = await host.request(
                to_node=node,
                namespace="terrarium.session",
                type="rollback_resume",
                body={"graph_id": new_sid},
                timeout=60.0,
            )
            if not isinstance(response, dict) or response.get("ok") is not True:
                raise RuntimeError(f"invalid rollback response: {response!r}")
        except BaseException as exc:
            errors.append(f"rollback {new_sid} on {node}: {exc}")

    links = getattr(service, "_cluster_links", None)
    resumed_ids = {new_sid for new_sid, _new_meta, _node in resumed.values()}
    if isinstance(links, set):
        for link in list(links):
            if any(
                isinstance(endpoint, tuple)
                and len(endpoint) == 2
                and endpoint[1] in resumed_ids
                for endpoint in link
            ):
                links.discard(link)
    elif isinstance(links, dict):
        for new_sid, _new_meta, _node in resumed.values():
            links.pop(new_sid, None)
    return errors


__all__ = [
    "ControllerStateSnapshot",
    "rollback_cluster_resume",
    "snapshot_controller_state",
]
