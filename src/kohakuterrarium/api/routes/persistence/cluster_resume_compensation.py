"""Compensation helpers for distributed cluster resume failures."""

from kohakuterrarium.studio.sessions import lifecycle


async def rollback_cluster_resume(
    service,
    resumed: dict[str, tuple[str, dict, str]],
    registered_session_ids: list[str],
    connected_pairs: list[tuple[str, str]],
) -> list[str]:
    """Best-effort cleanup of links, metadata, and worker runtimes."""
    errors: list[str] = []
    for left, right in reversed(connected_pairs):
        try:
            await service.disconnect(left, right, channel="default")
        except Exception as exc:
            errors.append(f"disconnect {left}/{right}: {exc}")

    registry = lifecycle.meta_for(service)
    for session_id in reversed(registered_session_ids):
        registry.pop(session_id, None)

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
        except Exception as exc:
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
