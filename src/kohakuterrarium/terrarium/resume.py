"""Engine-level resume — adopt a saved session into a live engine.

Resume is an engine concern: rebuild creatures from saved config,
inject the saved conversation / scratchpad / triggers / events, wrap
each agent in a :class:`Creature`, attach the :class:`SessionStore`
at the graph level, and start everything.  The Studio tier sits on
top of this and only adds metadata bookkeeping (``_meta`` /
``_session_stores`` in :mod:`studio.sessions.lifecycle`) plus the
HTTP / CLI orchestration.
"""

import os
from pathlib import Path
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import kohakuterrarium.terrarium.graph_checkpoint as _checkpoint
import kohakuterrarium.terrarium.graph_manifest as _manifest
import kohakuterrarium.terrarium.topology_snapshot as _topo_snap
import kohakuterrarium.terrarium.workspace_resume as _workspace
from kohakuterrarium.errors import SessionNotResumableError
from kohakuterrarium.builtins.inputs.none import NoneInput
from kohakuterrarium.core.config_serde import pack_agent_config
from kohakuterrarium.session.migrations import latest_readable_version
from kohakuterrarium.session.readonly import read_session_meta
from kohakuterrarium.session.resume import (
    _open_store_with_migration,
    detect_session_type,
    inject_saved_state,
    preflight_legacy_workspace,
    resume_agent,
)
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.config import load_terrarium_config
from kohakuterrarium.terrarium.creature_host import (
    Creature,
    _safe_creature_id,
)
from kohakuterrarium.terrarium.resume_manifest import (
    resume_manifest_into_engine as _resume_manifest_into_engine,
    schedule_drive_reconcile as _schedule_drive_reconcile,
)
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.terrarium.engine import Terrarium

logger = get_logger(__name__)


def _mark_conversation_open(store: SessionStore) -> None:
    """Persist the UI lifecycle marker when the store supports it."""
    setter = getattr(store, "set_conversation_open", None)
    if callable(setter):
        setter(True)


def _finish_conversation_resume(store: SessionStore) -> None:
    """Persist UI lifecycle state only after runtime adoption succeeds."""
    _mark_conversation_open(store)
    store.update_status("running")
    checkpoint = getattr(store, "checkpoint", None)
    if callable(checkpoint):
        checkpoint()


def prepare_resume_workspace(
    store: SessionStore | str | Path,
    *,
    pwd: str | None = None,
    workspace_overrides: dict[str, str] | None = None,
) -> "_workspace.WorkspaceResumePlan | None":
    """Read and validate workspace state without opening a writer/runtime."""
    if pwd is not None and workspace_overrides:
        raise ValueError("pwd and workspace_overrides are mutually exclusive")
    path = latest_readable_version(_resolve_store_path(store))
    meta = read_session_meta(path)
    resume_state = meta.get("workspace_resume_state")
    if (
        isinstance(resume_state, Mapping)
        and resume_state.get("status") == "partial_dirty"
    ):
        raise SessionNotResumableError(
            "Session has an incomplete workspace rollback and must be repaired"
        )
    raw_manifest = meta.get(_manifest.MANIFEST_KEY)
    if raw_manifest is None:
        preflight_legacy_workspace(path, pwd)
        return None
    manifest = _manifest.parse_manifest(raw_manifest)
    replacements = workspace_overrides
    if pwd is not None:
        replacements = {item.creature_id: pwd for item in manifest.creatures}
    return _workspace.plan_workspace_resume(
        manifest,
        replacements,
        allow_valid_targets=pwd is not None,
    )


async def resume_new_engine(
    engine_cls: type["Terrarium"],
    store: SessionStore | str | Path,
    *,
    pwd: str | None = None,
    workspace_overrides: dict[str, str] | None = None,
    llm: Any = None,
    drive_config: Any = None,
    drive_registrations: tuple[Any, ...] | list[Any] | None = None,
    drive_store: Any = None,
) -> "Terrarium":
    """Preflight, construct, and adopt into a fresh engine."""
    prepared = prepare_resume_workspace(
        store,
        pwd=pwd,
        workspace_overrides=workspace_overrides,
    )
    engine = engine_cls(
        pwd=pwd,
        drive_config=drive_config,
        drive_registrations=drive_registrations,
        drive_store=drive_store,
    )
    engine._running = True
    try:
        resume_kwargs = {"pwd": pwd, "llm": llm}
        if workspace_overrides is not None:
            resume_kwargs["workspace_overrides"] = workspace_overrides
        if prepared is not None:
            resume_kwargs["prepared_workspace"] = prepared
        await resume_into_engine(engine, store, **resume_kwargs)
    except BaseException:
        await engine.shutdown()
        raise
    return engine


async def resume_into_engine(
    engine: "Terrarium",
    store: SessionStore | str | Path,
    *,
    pwd: str | None = None,
    workspace_overrides: dict[str, str] | None = None,
    llm: Any = None,
    prepared_workspace: "_workspace.WorkspaceResumePlan | None" = None,
) -> str:
    """Adopt a saved session into ``engine`` and return its graph ID."""
    if pwd is not None and workspace_overrides:
        raise ValueError("pwd and workspace_overrides are mutually exclusive")
    path = _resolve_store_path(store)
    if isinstance(store, SessionStore):
        store.close(update_status=False)
    path = latest_readable_version(path)
    meta = read_session_meta(path)
    # None is the checkpoint tombstone — legacy resume, not a manifest.
    dirty_state = meta.get("workspace_resume_state")
    if isinstance(dirty_state, dict) and dirty_state.get("status") == "partial_dirty":
        raise SessionNotResumableError(
            "Session has an incomplete workspace rollback and must be repaired before resume"
        )
    raw_manifest = meta.get(_manifest.MANIFEST_KEY)
    if raw_manifest is not None:
        manifest = _manifest.parse_manifest(raw_manifest)
        replacements = workspace_overrides
        if pwd is not None:
            if workspace_overrides:
                raise ValueError(
                    "pwd and workspace_overrides are mutually exclusive; "
                    "pwd is the explicit whole-team compatibility override"
                )
            replacements = {item.creature_id: pwd for item in manifest.creatures}
        workspace_plan = prepared_workspace or _workspace.plan_workspace_resume(
            manifest,
            replacements,
            allow_valid_targets=pwd is not None,
        )
        resumed = await _resume_manifest_into_engine(
            engine,
            path,
            workspace_plan,
            replacements=replacements,
            allow_valid_targets=pwd is not None,
            llm=llm,
        )
        if resumed is not None:
            return resumed
        # Tombstoned between the read-only probe and writer-lock revalidation.
    preflight_legacy_workspace(path, pwd)
    session_type = detect_session_type(path)

    if session_type == "agent":
        return await _resume_agent_into_engine(engine, path, pwd=pwd, llm=llm)
    if session_type == "terrarium":
        return await _resume_terrarium_into_engine(engine, path, pwd=pwd, llm=llm)
    raise SessionNotResumableError(f"Unknown saved-session type: {session_type!r}")


def _resolve_store_path(store: SessionStore | str | Path) -> Path:
    if isinstance(store, SessionStore):
        # SessionStore exposes ``path`` (set in __init__) — fall back
        # to ``str(store)`` only if the attribute is missing on a future
        # store implementation.
        return Path(getattr(store, "path", str(store)))
    return Path(str(store))


async def _resume_agent_into_engine(
    engine: "Terrarium",
    path: Path,
    *,
    pwd: str | None,
    llm: Any,
) -> str:
    """Standalone-agent resume: rebuild Agent, wrap, adopt, attach.

    The rebuilt agent is adopted into a live engine and driven through
    the engine's wiring / attach WebSocket — never its config's own
    ``input: cli`` loop. ``input_module=NoneInput()`` suppresses that
    loop exactly as ``engine.add_creature(io="none")`` does for
    the Studio / Lab spawn path; without it a worker-side resume boots
    a stdin reader with no TTY and wedges the worker.
    """
    # session.resume.resume_agent does the heavy lifting: opens store
    # with migration, rebuilds Agent from the saved config, injects
    # every state slot, and calls agent.attach_session_store(store).
    # Its own handler closes the store if the rebuild fails; the guard
    # below covers the adopt-into-engine steps that run after it returns.
    agent, store = resume_agent(
        path,
        pwd_override=pwd,
        io_mode=None,
        llm=llm,
        input_module=NoneInput(),
        mark_conversation_open=False,
    )
    created: list[str] = []
    try:
        meta = getattr(store, "meta", {})
        snapshot = meta.get("config_snapshot") if hasattr(meta, "get") else None
        if snapshot is None:
            try:
                snapshot = pack_agent_config(agent.config)
            except (AttributeError, TypeError):
                snapshot = {"name": agent.config.name}
        creature_obj = Creature(
            creature_id=_safe_creature_id(agent.config.name),
            name=agent.config.name,
            agent=agent,
            config=agent.config,
            config_snapshot=snapshot,
            source_ref=meta.get("config_path") if hasattr(meta, "get") else None,
            build_pwd=str(
                pwd
                or (meta.get("pwd") if hasattr(meta, "get") else None)
                or os.getcwd()
            ),
        )
        # ``session=False``: the SAVED store attaches below — autosession
        # minting a fresh sibling file here would orphan it on disk.
        # ``start=False``: ``add_creature`` inserts into the topology +
        # ``_creatures`` before awaiting startup, so the id must be
        # recorded at insertion — a start failure would else leave the
        # creature adopted but absent from the rollback list.
        creature = await engine.add_creature(creature_obj, start=False, session=False)
        created.append(creature.creature_id)

        # Attach at graph level. ``Agent.attach_session_store`` is
        # idempotent for the same store, so this updates graph bookkeeping
        # without adding a duplicate SessionOutput sink. Resume OPENED this
        # store — register it as engine-owned so shutdown closes it (a
        # leaked writer lock blocks any later adopt of the same file).
        await engine.attach_session(creature.graph_id, store)
        engine._owned_sessions.add(creature.graph_id)

        await creature.start()
        # The restoration barrier gates Drive reconciliation. The session store
        # and Drive repository are already attached, so persisted Drives
        # redeliver only after startup settles.
        _schedule_drive_reconcile(engine, creature)
        await _checkpoint.checkpoint(engine, creature.graph_id)
        _finish_conversation_resume(store)

        logger.info(
            "Agent session resumed into engine",
            session_id=creature.graph_id,
            creature_id=creature.creature_id,
            path=str(path),
        )
        return creature.graph_id
    except BaseException:
        await _rollback_failed_adoption(engine, store, created)
        raise


async def _resume_terrarium_into_engine(
    engine: "Terrarium",
    path: Path,
    *,
    pwd: str | None,
    llm: Any = None,
) -> str:
    """Multi-creature recipe resume: rebuild graph, inject per-creature."""
    store = _open_store_with_migration(path, writer_lock=True)
    # ``apply_recipe`` appends every creature it adds here, so a failure
    # rolls back exactly this adoption's creatures — never one a
    # concurrent task added meanwhile.
    created: list[str] = []
    try:
        return await _resume_terrarium_body(
            engine, path, store, created, pwd=pwd, llm=llm
        )
    except BaseException:
        await _rollback_failed_adoption(engine, store, created)
        raise


async def _resume_terrarium_body(
    engine: "Terrarium",
    path: Path,
    store: SessionStore,
    created: list[str],
    *,
    pwd: str | None,
    llm: Any = None,
) -> str:
    """Rebuild + rehydrate the terrarium graph from an already-open store.

    Split out of :func:`_resume_terrarium_into_engine` so the caller can
    guard the whole flow with one close-and-rollback handler.  ``created``
    accumulates the ids of the creatures this adoption adds.
    """
    meta = store.load_meta()
    config_path = meta.get("config_path", "")
    if not config_path:
        raise SessionNotResumableError("Saved terrarium has no config_path in metadata")

    # ``pwd`` flows into ``apply_recipe`` for per-creature workspaces;
    # resume must not change the process-wide working directory.
    saved_pwd = meta.get("pwd")
    pwd = pwd or saved_pwd
    if not (pwd and os.path.isdir(pwd)):
        source = "override" if pwd != saved_pwd else "saved"
        raise SessionNotResumableError(
            f"The {source} working directory is missing or invalid: {pwd!r}. "
            "Choose a replacement directory or open the session history."
        )

    config = load_terrarium_config(config_path)

    # Build the topology via the engine — creates every creature and
    # wires channels, but ``start=False``: a started creature schedules
    # its input drive and startup triggers immediately, which would run
    # against an EMPTY conversation before the saved state lands. The
    # per-creature start happens below, after injection.
    #
    # ``session=False``: the SAVED store attaches below.  Under
    # autosession (``Terrarium(session_dir=...)`` — every API-server
    # engine) ``apply_recipe`` would otherwise mint a fresh
    # ``<new_gid>.kohakutr`` that the saved-store attach immediately
    # orphans: an empty ghost file stuck at ``status="running"`` in
    # the saved-session list, plus a leaked open handle.  Mirrors the
    # ``session=False`` in ``_resume_agent_into_engine``.
    graph = await engine.apply_recipe(
        config, pwd=pwd, llm=llm, session=False, start=False, created_ids=created
    )
    sid = graph.graph_id

    # Per-creature state injection.
    #
    # Resolve each rebuilt creature to its *saved* runtime name. Saved
    # events are keyed by ``<saved_name>:e:<id>``, but ``apply_recipe``
    # may have produced a creature whose current ``config.name`` does
    # not match that key (the fresh build can re-roll random suffixes,
    # or the saved recipe stored an explicit per-creature name that the
    # rebuild collapsed to the config default). Match in this order:
    #
    #   1. fresh name already exists in the saved agents list;
    #   2. otherwise consume the next unused saved name positionally.
    #
    # Without this we'd inject under the fresh name and recover zero
    # events. Same root cause as the creature-resume bug fixed in
    # ``session.resume.align_agent_name``.
    saved_agents = list(meta.get("agents") or [])
    saved_set = set(saved_agents)
    consumed: set[str] = set()
    for cid in graph.creature_ids:
        try:
            creature = engine.get_creature(cid)
        except KeyError:
            continue
        fresh = creature.agent.config.name
        if fresh in saved_set and fresh not in consumed:
            agent_name = fresh
        else:
            # Pull the next saved name we haven't consumed yet.
            agent_name = next(
                (n for n in saved_agents if n not in consumed),
                fresh,
            )
        consumed.add(agent_name)
        inject_saved_state(creature.agent, store, agent_name)
        # ``inject_saved_state`` aligns ``agent.config.name``; mirror
        # the saved name onto the Creature wrapper too so chat-history
        # lookups (which key off ``creature.name``) hit the same
        # namespace as the events we just injected.
        creature.name = agent_name
        if creature.config is not None:
            creature.config.name = agent_name
        creature.agent.attach_session_store(store)

    # Attach at graph level. Each creature was already attached just
    # above, but ``Agent.attach_session_store`` is idempotent for the
    # same store so this preserves graph/session bookkeeping safely.
    # Must precede the topology replay — the replay reads
    # ``runtime_topology`` off the engine-attached store. Resume OPENED
    # this store — register it as engine-owned so shutdown closes it.
    await engine.attach_session(sid, store)
    engine._owned_sessions.add(sid)

    # Replay runtime topology mutations on top of the recipe-rebuilt
    # graph BEFORE anything starts: any channel the user added via
    # ``service.add_channel`` / any wire from ``service.connect`` after
    # the original spawn lives in ``meta["runtime_topology"]``. A
    # startup trigger must see the restored channels + wires, not the
    # bare recipe topology.
    await _topo_snap.replay(engine, sid)

    # Saved state, session, and topology are in — NOW the creatures may
    # start (startup triggers and inbound input see the restored
    # conversation and graph, not the bare recipe).
    for cid in graph.creature_ids:
        try:
            creature = engine.get_creature(cid)
        except KeyError:
            continue
        await creature.start()
        # The restoration barrier gates Drive reconciliation. The session and
        # Drive repository are attached above, so persisted Drives redeliver
        # only once each creature's startup settles.
        _schedule_drive_reconcile(engine, creature)

    if sid in engine._topology.graphs:
        await _checkpoint.checkpoint(engine, sid)
    _finish_conversation_resume(store)
    logger.info(
        "Terrarium session resumed into engine",
        session_id=sid,
        path=str(path),
        creatures=len(graph.creature_ids),
    )
    return sid


async def _rollback_failed_adoption(
    engine: "Terrarium",
    store: SessionStore,
    created_ids: list[str],
) -> None:
    """Best-effort undo of a partial session adoption.

    Detaches ``store`` from the engine by identity FIRST so the creature
    removals below don't run split coordination against it, removes only
    the creatures this adoption created (by exact id, so a creature a
    concurrent task added mid-adoption is never touched), then closes the
    store to release its writer lock.  Pre-existing graphs are left
    untouched.
    """
    for gid, s in list(engine._session_stores.items()):
        if s is store:
            engine._session_stores.pop(gid, None)
            engine._owned_sessions.discard(gid)
    for cid in created_ids:
        if cid not in engine._creatures:
            continue
        try:
            await engine.remove_creature(cid)
        except Exception:
            logger.warning(
                "resume rollback: remove_creature failed",
                creature_id=cid,
                exc_info=True,
            )
    try:
        store.close(update_status=False)
    except Exception:
        logger.warning("resume rollback: store close failed", exc_info=True)
