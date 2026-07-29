"""Stop sessions while keeping persisted conversation lifecycle consistent."""

import os
from pathlib import Path
from typing import Any

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.sessions import cluster_fold
from kohakuterrarium.studio._runtime import host_engine_or_none
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def persist_cluster_members_to_mirror(
    service, session_id: str, mirror_dir: Path
) -> None:
    """Persist cluster membership before live service links disappear."""
    cluster_fold.persist_cluster_members_to_mirror(service, session_id, mirror_dir)


def _set_store_lifecycle(store: SessionStore, *, is_open: bool, status: str) -> None:
    store.set_conversation_open(is_open)
    store.update_status(status)
    store.checkpoint()


def _update_store_lifecycle(store: SessionStore, *, end_conversation: bool) -> None:
    is_open = not end_conversation
    _set_store_lifecycle(
        store,
        is_open=is_open,
        status="paused" if is_open else "completed",
    )


def _read_mirror_lifecycle(path: str) -> tuple[bool, str] | None:
    mirror = Path(path)
    if not mirror.is_file():
        return None
    store = SessionStore.open_readonly(mirror)
    try:
        return (
            bool(store.meta.get("conversation_open")),
            str(store.meta.get("status") or "running"),
        )
    finally:
        store.close(update_status=False)


def _set_mirror_lifecycle(path: str, *, is_open: bool, status: str) -> None:
    mirror = Path(path)
    if not mirror.is_file():
        return
    store = SessionStore(mirror)
    try:
        _set_store_lifecycle(store, is_open=is_open, status=status)
    finally:
        store.close(update_status=False)


def _update_mirror_lifecycle(path: str, *, end_conversation: bool) -> None:
    mirror = Path(path)
    if not mirror.is_file():
        return
    store = SessionStore(mirror)
    try:
        _update_store_lifecycle(store, end_conversation=end_conversation)
    finally:
        store.close(update_status=False)


def _path_key(path: str | Path) -> str:
    return os.path.normcase(str(Path(path).expanduser().resolve(strict=False)))


def _host_mirror_paths(
    session_ids: list[str],
    meta: dict[str, dict[str, Any]],
    mirror_dir: Path,
    *,
    live_store: SessionStore | None,
) -> list[str]:
    """Return each distinct host-side saved copy that needs lifecycle updates."""
    live_key = _path_key(live_store.path) if live_store is not None else None
    paths: list[str] = []
    seen: set[str] = set()
    for member_id in session_ids:
        entry = meta.get(member_id) or {}
        candidates = [
            str(entry.get("resumed_from") or ""),
            str(mirror_dir / f"{member_id}.kohakutr"),
        ]
        for candidate in candidates:
            if not candidate or not Path(candidate).is_file():
                continue
            key = _path_key(candidate)
            if key == live_key or key in seen:
                continue
            seen.add(key)
            paths.append(candidate)
    return paths


async def _update_remote_lifecycle(
    service,
    session_id: str,
    entry: dict[str, Any],
    *,
    end_conversation: bool,
    status_override: str | None = None,
) -> None:
    node_id = str(entry.get("on_node") or "")
    session_path = str(entry.get("remote_session_path") or "")
    host = getattr(service, "_host", None)
    if not node_id or not session_path or host is None:
        if not entry.get("conversation_id"):
            return
        raise RuntimeError(
            f"remote session {session_id!r} has no lifecycle synchronization target"
        )
    response = await host.request(
        to_node=node_id,
        namespace="terrarium.session",
        type="set_lifecycle",
        body={
            "session_path": session_path,
            "conversation_open": not end_conversation,
            "status": status_override
            or ("completed" if end_conversation else "paused"),
        },
        timeout=60.0,
    )
    if not isinstance(response, dict) or response.get("ok") is not True:
        raise RuntimeError(f"remote lifecycle update failed: {response!r}")


def _cluster_session_ids(service, session_id: str) -> list[str]:
    groups = cluster_fold.cluster_groups(service)
    for primary, members in groups.items():
        if session_id == primary or session_id in members:
            return [primary, *sorted(member for member in members if member != primary)]
    return [session_id]


async def _update_remote_cluster_lifecycle(
    service,
    session_ids: list[str],
    meta: dict[str, dict[str, Any]],
    *,
    end_conversation: bool,
    status_override: str | None = None,
) -> None:
    updated: list[tuple[str, dict[str, Any]]] = []
    try:
        for member_id in session_ids:
            entry = meta.get(member_id)
            if entry is None:
                raise KeyError(f"cluster member {member_id!r} metadata is missing")
            await _update_remote_lifecycle(
                service,
                member_id,
                entry,
                end_conversation=end_conversation,
                status_override=status_override,
            )
            updated.append((member_id, entry))
    except Exception:
        for member_id, entry in reversed(updated):
            try:
                await _update_remote_lifecycle(
                    service,
                    member_id,
                    entry,
                    end_conversation=False,
                    status_override="running",
                )
            except Exception as rollback_error:
                logger.error(
                    "Failed to roll back remote lifecycle marker",
                    session_id=member_id,
                    error=str(rollback_error),
                    exc_info=True,
                )
        raise


async def _remote_creature_ids(
    service,
    session_ids: list[str],
    meta: dict[str, dict[str, Any]],
) -> list[str]:
    """Resolve every creature in the remote member graphs before teardown."""
    by_session: dict[str, list[str]] = {session_id: [] for session_id in session_ids}
    list_creatures = getattr(service, "list_creatures", None)
    if callable(list_creatures):
        try:
            roster = await list_creatures()
        except Exception as exc:  # noqa: BLE001 - metadata remains a safe fallback
            logger.warning(
                "Failed to enumerate remote session creatures",
                error=str(exc),
                exc_info=True,
            )
            roster = ()
        for info in roster or ():
            graph_id = getattr(info, "graph_id", None) or (
                info.get("graph_id") if isinstance(info, dict) else None
            )
            creature_id = getattr(info, "creature_id", None) or (
                info.get("creature_id") if isinstance(info, dict) else None
            )
            if graph_id in by_session and creature_id:
                by_session[graph_id].append(str(creature_id))

    resolved: list[str] = []
    seen: set[str] = set()
    missing: list[str] = []
    for session_id in session_ids:
        candidates = by_session[session_id]
        entry = meta.get(session_id) or {}
        fallback_ids = entry.get("creature_ids")
        if isinstance(fallback_ids, list):
            candidates.extend(
                str(creature_id)
                for creature_id in fallback_ids
                if isinstance(creature_id, str) and creature_id
            )
        fallback = str(entry.get("creature_id") or "")
        if fallback and fallback not in candidates:
            candidates.append(fallback)
        if not candidates:
            missing.append(session_id)
            continue
        for creature_id in candidates:
            if creature_id in seen:
                continue
            seen.add(creature_id)
            resolved.append(creature_id)
    if missing:
        raise RuntimeError(
            "remote session members have no creature teardown target: "
            + ", ".join(sorted(missing))
        )
    return resolved


async def stop_session(
    service,
    session_id: str,
    *,
    meta: dict[str, dict[str, Any]],
    session_stores: dict[str, SessionStore],
    mirror_dir: Path,
    index_hooks: dict[str, Any] | None = None,
    end_conversation: bool = False,
) -> None:
    """Stop a runtime, optionally ending its persisted conversation.

    Lifecycle is synchronized before runtime teardown. A failed remote marker write
    therefore leaves the runtime intact and lets the caller retry safely.
    """
    persist_cluster_members_to_mirror(service, session_id, mirror_dir)
    cluster_session_ids = _cluster_session_ids(service, session_id)
    try:
        engine = host_engine_or_none(service)
    except (AttributeError, TypeError):
        engine = None
    graph = None
    if engine is not None:
        graph = next(
            (item for item in engine.list_graphs() if item.graph_id == session_id),
            None,
        )

    engine_stores = getattr(engine, "_session_stores", None) if engine else None
    store = session_stores.get(session_id)
    if isinstance(engine_stores, dict):
        store = engine_stores.get(session_id) or store
    entry = meta.get(session_id)
    remote_creature_ids: list[str] = []
    if graph is None and entry is not None:
        remote_creature_ids = await _remote_creature_ids(
            service,
            cluster_session_ids,
            meta,
        )

    original_store_lifecycle = None
    if store is not None:
        original_store_lifecycle = (
            bool(store.meta.get("conversation_open")),
            str(store.meta.get("status") or "running"),
        )
        _update_store_lifecycle(store, end_conversation=end_conversation)
    elif graph is None:
        if entry is None:
            raise KeyError(f"session {session_id!r} not found")
        await _update_remote_cluster_lifecycle(
            service,
            cluster_session_ids,
            meta,
            end_conversation=end_conversation,
        )

    mirror_lifecycles: list[tuple[str, tuple[bool, str]]] = []
    try:
        for mirror_path in _host_mirror_paths(
            cluster_session_ids,
            meta,
            mirror_dir,
            live_store=store,
        ):
            original = _read_mirror_lifecycle(mirror_path)
            if original is None:
                continue
            _update_mirror_lifecycle(
                mirror_path,
                end_conversation=end_conversation,
            )
            mirror_lifecycles.append((mirror_path, original))
    except Exception:
        for updated_path, original in reversed(mirror_lifecycles):
            _set_mirror_lifecycle(
                updated_path,
                is_open=original[0],
                status=original[1],
            )
        if store is not None and original_store_lifecycle is not None:
            _set_store_lifecycle(
                store,
                is_open=original_store_lifecycle[0],
                status=original_store_lifecycle[1],
            )
        elif graph is None:
            await _update_remote_cluster_lifecycle(
                service,
                cluster_session_ids,
                meta,
                end_conversation=False,
                status_override="running",
            )
        raise

    try:
        if graph is not None:
            for creature_id in list(graph.creature_ids):
                try:
                    await engine.remove_creature(creature_id)
                except KeyError:
                    pass
        else:
            if entry is None or not entry.get("on_node"):
                raise KeyError(f"session {session_id!r} not found")
            for creature_id in remote_creature_ids:
                try:
                    await service.remove_creature(creature_id)
                except KeyError:
                    pass
    except Exception:
        if store is not None and original_store_lifecycle is not None:
            _set_store_lifecycle(
                store,
                is_open=original_store_lifecycle[0],
                status=original_store_lifecycle[1],
            )
        elif graph is None:
            await _update_remote_cluster_lifecycle(
                service,
                cluster_session_ids,
                meta,
                end_conversation=False,
                status_override="running",
            )
        for mirror_path, original_mirror_lifecycle in reversed(mirror_lifecycles):
            _set_mirror_lifecycle(
                mirror_path,
                is_open=original_mirror_lifecycle[0],
                status=original_mirror_lifecycle[1],
            )
        raise

    for member_id in cluster_session_ids:
        meta.pop(member_id, None)
    session_stores.pop(session_id, None)
    if isinstance(engine_stores, dict):
        engine_stores.pop(session_id, None)
    if index_hooks is not None:
        hook = index_hooks.pop(session_id, None)
        if hook is not None:
            try:
                hook.flush()
                hook.detach()
            except Exception as exc:
                logger.warning(
                    "Failed to detach session-index hook on stop",
                    session_id=session_id,
                    error=str(exc),
                    exc_info=True,
                )
    if store is not None and hasattr(store, "close"):
        try:
            store.close(update_status=False)
        except Exception as exc:
            logger.warning(
                "Failed to close session store on stop",
                session_id=session_id,
                error=str(exc),
                exc_info=True,
            )
    logger.info(
        "Session ended" if end_conversation else "Session stopped",
        session_id=session_id,
    )
