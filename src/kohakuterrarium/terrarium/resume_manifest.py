"""Atomic restoration of manifest-backed Terrarium graph sessions."""

from copy import deepcopy
from typing import TYPE_CHECKING, Any

import kohakuterrarium.terrarium.channels as _channels
import kohakuterrarium.terrarium.graph_checkpoint as _checkpoint
import kohakuterrarium.terrarium.graph_manifest as _manifest
import kohakuterrarium.terrarium.topology as _topology
import kohakuterrarium.terrarium.workspace_resume as _workspace
from kohakuterrarium.errors import (
    GraphManifestCollisionError,
    SessionNotResumableError,
)
from kohakuterrarium.session.resume import (
    _open_store_with_migration,
    inject_saved_state,
)
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.creature_host import (
    Creature,
    apply_creature_name,
)
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from pathlib import Path

    from kohakuterrarium.terrarium.engine import Terrarium

logger = get_logger(__name__)
_MISSING = object()
_ROLLBACK_META_KEYS = (
    "pwd",
    "workspace_resume_state",
    "runtime_topology",
    "conversation_open",
    "status",
    "last_active",
)


def schedule_drive_reconcile(engine: "Terrarium", creature: Creature) -> None:
    """Arm Drive reconciliation after a resumed creature starts."""
    runtime = getattr(engine, "_drive_runtime", None)
    if runtime is not None:
        runtime.schedule_reconcile(creature)


async def resume_manifest_into_engine(
    engine: "Terrarium",
    path: "Path",
    workspace_plan: _workspace.WorkspaceResumePlan,
    *,
    replacements: dict[str, str] | None = None,
    allow_valid_targets: bool = False,
    llm: Any = None,
) -> str | None:
    """Reserve identities, revalidate the writer view, then restore a manifest.

    ``None`` means the manifest was tombstoned after the read-only preflight;
    the caller may continue through the legacy resume path.
    """
    runtime_ids = [item.creature_id for item in workspace_plan.manifest.creatures]
    async with engine._recipe_identities.reserve_exact(engine, runtime_ids):
        store = _open_store_with_migration(path, writer_lock=True)
        try:
            locked_manifest = _manifest.load_manifest(store)
        except BaseException:
            store.close(update_status=False)
            raise
        if locked_manifest is None:
            store.close(update_status=False)
            return None
        if locked_manifest.revision != workspace_plan.manifest.revision:
            store.close(update_status=False)
            raise _workspace.WorkspaceResumeError(
                _workspace.WorkspaceResumeFailure.STALE_MANIFEST,
                "Session workspace metadata changed during resume preflight",
            )
        locked_plan = _workspace.plan_workspace_resume(
            locked_manifest,
            replacements,
            allow_valid_targets=allow_valid_targets,
        )
        return await _resume_reserved_manifest(
            engine,
            path,
            locked_plan.manifest,
            llm=llm,
            store=store,
        )


async def _resume_reserved_manifest(
    engine: "Terrarium",
    path: "Path",
    manifest: _manifest.GraphManifest,
    *,
    llm: Any,
    store: SessionStore,
) -> str:
    """Restore a graph while all persisted creature identities are reserved."""
    if manifest.graph_id in engine._topology.graphs:
        store.close(update_status=False)
        raise GraphManifestCollisionError("graph_id", manifest.graph_id)
    for item in manifest.creatures:
        if (
            item.creature_id in engine._creatures
            or item.creature_id in engine._topology.creature_to_graph
        ):
            store.close(update_status=False)
            raise GraphManifestCollisionError("creature_id", item.creature_id)

    created: list[Creature] = []
    workspace_persisted = False
    original_manifest = _manifest.parse_manifest(store.meta[_manifest.MANIFEST_KEY])
    original_meta = {}
    for key in _ROLLBACK_META_KEYS:
        value = store.meta.get(key, _MISSING)
        original_meta[key] = _MISSING if value is _MISSING else deepcopy(value)
    engine._create_restore_graph(manifest.graph_id)
    try:
        for item in manifest.creatures:
            creature = await engine.add_creature(
                _manifest.unpack_creature_config(item),
                llm=llm,
                pwd=item.pwd,
                io="none",
                strict=False,
                session=False,
                start=False,
                graph=manifest.graph_id,
                creature_id=item.creature_id,
                name=item.name,
                is_privileged=item.is_privileged,
                parent_creature_id=item.parent_creature_id,
                _identity_reserved=True,
            )
            creature.injected_runtime = ()
            created.append(creature)

        graph = engine._topology.graphs[manifest.graph_id]
        environment = engine._environments[manifest.graph_id]
        for channel in manifest.channels:
            info = _topology.add_channel(
                engine._topology,
                manifest.graph_id,
                channel.name,
                description=channel.description,
            )
            _channels.register_channel_in_environment(
                environment.shared_channels,
                info,
                engine=engine,
                graph_id=manifest.graph_id,
            )
        for creature_id, channel_name in manifest.listen:
            _topology.set_listen(
                engine._topology, creature_id, channel_name, listening=True
            )
        for creature_id, channel_name in manifest.send:
            _topology.set_send(
                engine._topology, creature_id, channel_name, sending=True
            )

        for creature in created:
            creature.listen_channels = sorted(
                graph.listen_edges.get(creature.creature_id, set())
            )
            creature.send_channels = sorted(
                graph.send_edges.get(creature.creature_id, set())
            )
            _channels.bind_creature_to_environment(creature, environment)
            for channel_name in creature.listen_channels:
                _channels.inject_channel_trigger(
                    creature.agent,
                    subscriber_id=creature.name,
                    channel_name=channel_name,
                    registry=environment.shared_channels,
                    ignore_sender_id=creature.creature_id,
                )

        for creature in created:
            inject_saved_state(creature.agent, store, creature.name)
            apply_creature_name(creature, creature.name)
            await creature.start()

        # Keep the authoritative store detached until every synchronous start
        # succeeds so a failed resume cannot append startup state or events.
        with _checkpoint.suppress(engine):
            await engine.attach_session(manifest.graph_id, store)
        engine._owned_sessions.add(manifest.graph_id)
        for creature in created:
            schedule_drive_reconcile(engine, creature)
        workspace_persisted = True
        store.meta["pwd"] = _legacy_pwd_projection(manifest)
        if not await _checkpoint.checkpoint(engine, manifest.graph_id):
            raise SessionNotResumableError(
                "Workspace resume checkpoint did not persist a valid graph manifest"
            )
        setter = getattr(store, "set_conversation_open", None)
        if callable(setter):
            setter(True)
        store.update_status("running")
        store.checkpoint()
        return manifest.graph_id
    except BaseException as exc:
        if workspace_persisted:
            try:
                _restore_meta(store, original_meta)
                _manifest.save_manifest(store, original_manifest)
                store.checkpoint()
            except Exception as rollback_exc:
                try:
                    store.meta["workspace_resume_state"] = {
                        "status": "partial_dirty",
                        "error": str(exc),
                        "rollback_error": str(rollback_exc),
                    }
                    store.checkpoint()
                except Exception:
                    logger.exception(
                        "Unable to persist workspace resume dirty marker",
                        extra={"session_path": str(path)},
                    )
                logger.exception(
                    "Workspace resume checkpoint failed and rollback was incomplete",
                    extra={"session_path": str(path)},
                )
        environment = engine._environments.get(manifest.graph_id)
        for creature in reversed(created):
            try:
                if creature.is_running:
                    await creature.stop()
                registry = getattr(environment, "shared_channels", None)
                for channel_name in creature.listen_channels:
                    channel = (
                        registry.get(channel_name) if registry is not None else None
                    )
                    if channel is not None:
                        channel.unsubscribe(creature.name)
            except BaseException:
                pass
            engine._creatures.pop(creature.creature_id, None)
            getattr(engine, "_runtime_contexts", {}).pop(creature.creature_id, None)
            engine._topology.creature_to_graph.pop(creature.creature_id, None)
        if engine._session_stores.get(manifest.graph_id) is store:
            engine._session_stores.pop(manifest.graph_id, None)
        engine._owned_sessions.discard(manifest.graph_id)
        runtime = getattr(engine, "_drive_runtime", None)
        if runtime is not None:
            try:
                await runtime.detach_graph(manifest.graph_id)
            except BaseException:
                pass
        engine._environments.pop(manifest.graph_id, None)
        engine._topology.graphs.pop(manifest.graph_id, None)
        try:
            store.close(update_status=False)
        except Exception:
            logger.warning(
                "manifest resume rollback: store close failed", exc_info=True
            )
        raise


def _legacy_pwd_projection(manifest: _manifest.GraphManifest) -> str:
    paths = {item.pwd for item in manifest.creatures}
    return next(iter(paths)) if len(paths) == 1 else ""


def _restore_meta(store: SessionStore, original: dict[str, Any]) -> None:
    """Restore metadata touched by attach/checkpoint/status publication."""
    for key, value in original.items():
        if value is _MISSING:
            store.meta.pop(key, None)
        else:
            store.meta[key] = value
