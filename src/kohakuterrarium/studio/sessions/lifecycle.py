"""Manage Studio sessions backed by Terrarium graphs.

A standalone creature starts in a new single-creature graph, while a terrarium
recipe populates one graph with all configured creatures. Per-creature behavior
is implemented by the sibling ``creature_*.py`` modules.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.graph_identity import ensure_graph_name_available
from kohakuterrarium.studio.sessions import cluster_fold, remote_meta, stop as _stop
from kohakuterrarium.studio.sessions import index_hooks as _index_hooks
from kohakuterrarium.studio.sessions.find import (
    apply_creature_name,
    apply_creature_name as _apply_creature_name,  # noqa: F401 — legacy alias
    find_creature,  # noqa: F401 — re-export for external callers
)
from kohakuterrarium.studio.sessions.handles import Session, SessionListing
from kohakuterrarium.studio.sessions.registry import (  # noqa: F401 — re-exports
    get_session_meta,
    get_session_store,
    list_session_stores,
    meta_for,
    register_session_meta,
    stores_for,
)
from kohakuterrarium.studio.sessions.store_attach import (
    attach_session_store_for_creature,  # noqa: F401 — re-export
    session_dir as _session_dir,
)
from kohakuterrarium.terrarium.config import (
    CreatureConfig,
    TerrariumConfig,
    load_terrarium_config,
)
from kohakuterrarium.studio._runtime import as_engine, host_engine_or_none
from kohakuterrarium.studio.deploy import deploy_creature_to_node
from kohakuterrarium.terrarium import TerrariumService
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.multi_node_service import MultiNodeTerrariumService
from kohakuterrarium.utils.logging import get_logger
from kohakuterrarium.utils.mobile_sandbox import default_workdir

logger = get_logger(__name__)


# Session metadata and stores are scoped to the runtime anchor so independent
# engines or services in one process cannot share bookkeeping.

# Preserve private aliases used by compatibility callers and tests.
_cluster_groups = cluster_fold.cluster_groups
_sid_to_primary = cluster_fold.sid_to_primary
_fold_session_listings = cluster_fold.fold_session_listings


def _fold_session_creatures(service, primary_sid, *, live_creatures=None):
    return cluster_fold.fold_session_creatures(
        service, primary_sid, meta_for(service), live_creatures=live_creatures
    )


def _normalize_pwd(pwd: str | None) -> str | None:
    if pwd is None:
        return None
    resolved = str(Path(pwd).expanduser().resolve())
    p = Path(resolved)
    if not p.exists():
        raise ValueError(f"Working directory does not exist: {pwd}")
    if not p.is_dir():
        raise ValueError(f"Working directory is not a directory: {pwd}")
    return resolved


def now_iso() -> str:
    """UTC ISO-8601 timestamp — the studio-tier ``created_at`` format."""
    return datetime.now(timezone.utc).isoformat()


# Preserve the private timestamp alias used by existing callers.
_now_iso = now_iso


async def start_creature(
    service: "TerrariumService",
    *,
    config_path: str | None = None,
    config=None,
    llm: str | None = None,
    pwd: str | None = None,
    name: str | None = None,
    on_node: str = "_host",
) -> Session:
    """Create and start a standalone creature.  Returns a Session handle.

    ``config_path`` may be a path or a ``@pkg/...`` reference; ``config``
    is an already-loaded :class:`AgentConfig`.  Exactly one is required.

    ``on_node`` (default ``"_host"``) selects the runtime node.  For a
    remote worker, the caller must have deployed the recipe to the
    worker first via ``POST /api/nodes/{node_id}/deploy/creature``; the
    ``config_path`` for a remote spawn should be the worker-side
    absolute path returned by the deploy call.
    """
    # Only host-targeted paths can be validated against the host filesystem;
    # remote workers validate their own paths during creature creation.
    if on_node == "_host":
        pwd = _normalize_pwd(pwd)
        # Lab hosts coordinate workers but do not run agents themselves.
        if hasattr(service, "connected_nodes"):
            raise ValueError(
                "lab-host mode runs no agents on the host — spawn on a "
                "worker node (pass on_node=<worker name>)"
            )
        # The engine must receive the display name before autosession attaches;
        # persisted event keys are derived from the configured name at attach time.
        engine = as_engine(service)
        if config_path:
            creature = await engine.add_creature(
                config_path,
                llm=llm,
                pwd=pwd,
                is_privileged=True,
                strict=False,
                name=name,
            )
        elif config is not None:
            creature = await engine.add_creature(
                config,
                llm=llm,
                pwd=pwd,
                is_privileged=True,
                strict=False,
                name=name,
            )
        else:
            raise ValueError("Must provide config_path or config")
        sid = creature.graph_id
        cid = creature.creature_id
        attach_session_store_for_creature(
            engine, creature, config_path=config_path or ""
        )
        meta_for(service)[sid] = {
            "name": creature.name,
            "config_path": config_path or "",
            "pwd": pwd or str(default_workdir()),
            "created_at": _now_iso(),
        }
        logger.info("Creature session started", session_id=sid, creature_id=cid)
        return _build_session_handle(engine, sid, meta_for(service))

    # Remote creation is service-routed; the worker attaches its session store
    # and the controller synthesizes a Session from the returned identity.
    spawn_payload: Any = config if config is not None else config_path
    if spawn_payload is None:
        raise ValueError("Must provide config_path or config")
    # Package references are resolved against the worker's installed packages.
    info = await service.add_creature(
        spawn_payload,
        is_privileged=True,
        pwd=pwd,
        llm=llm,
        on_node=on_node,
        name=name.strip() if name and name.strip() else None,
    )
    sid = info.graph_id
    remote_session_path = ""
    conversation_id = ""
    host = getattr(service, "_host", None)
    if host is not None:
        try:
            response = await host.request(
                to_node=on_node,
                namespace="terrarium.session",
                type="stores",
                body={"session_id": sid},
                timeout=30.0,
            )
            stores = response.get("stores") if isinstance(response, dict) else None
            if isinstance(stores, list) and stores:
                remote_session_path = str(stores[0].get("path") or "")
                conversation_id = str(stores[0].get("conversation_id") or "")
        except Exception as exc:
            logger.warning(
                "Failed to discover remote session store",
                session_id=sid,
                error=str(exc),
            )
    meta_for(service)[sid] = {
        "name": info.name,
        "config_path": config_path or "",
        "pwd": pwd or "",
        "created_at": _now_iso(),
        "on_node": on_node,
        # The host needs the worker identity to reconstruct remote Session handles.
        "creature_id": info.creature_id,
        "remote_session_path": remote_session_path,
        "conversation_id": conversation_id,
        # Cached model metadata keeps remote status readable during brief outages.
        "model": str(getattr(info, "model", "") or ""),
        "llm_name": str(getattr(info, "llm_name", "") or ""),
        "is_privileged": bool(getattr(info, "is_privileged", False)),
        "running": bool(getattr(info, "is_running", True)),
    }
    logger.info(
        "Remote creature session started",
        session_id=sid,
        creature_id=info.creature_id,
        on_node=on_node,
    )
    # Session and creature home-node fields must agree for routing and site UI.
    return Session(
        session_id=sid,
        name=info.name,
        creatures=[
            {
                "creature_id": info.creature_id,
                "name": info.name,
                "home_node": on_node,
                "running": info.is_running,
                "is_privileged": info.is_privileged,
                # An empty model denotes a deferred provider awaiting model selection.
                "model": getattr(info, "model", "") or "",
                # The canonical LLM identifier takes precedence over the display model.
                "llm_name": getattr(info, "llm_name", "") or "",
            }
        ],
        channels=[],
        # ``has_root`` describes recipe structure, not creature privilege.
        has_root=False,
        pwd=pwd or "",
        created_at=_now_iso(),
        config_path=config_path or "",
        home_node=on_node,
    )


def _resolve_engine_for_recipe(service: "TerrariumService") -> Terrarium:
    """Return an engine suitable for ``apply_recipe``.

    Standalone path: ``service.engine`` (same as :func:`as_engine`).
    Lab-host path: the host has no agent engine but exposes a
    ``coordination_engine``; the recipe applies there so the modal
    creates creatures locally on the host even when no worker is
    targeted.  Raises ``ValueError`` when neither is available (a
    lab-host built without a coordination engine — no place to put
    agents).
    """
    if hasattr(service, "connected_nodes"):
        coord = getattr(service, "coordination_engine", None)
        if coord is None:
            raise ValueError(
                "lab-host mode runs no host agent engine and no coordination "
                "engine is configured — recipe spawn has no host to target; "
                "deploy the recipe to a worker first"
            )
        return coord
    return as_engine(service)


async def start_terrarium(
    service: "TerrariumService",
    *,
    config_path: str | None = None,
    config: TerrariumConfig | None = None,
    pwd: str | None = None,
    name: str | None = None,
    llm: str | None = None,
    on_node: str = "_host",
) -> Session:
    """Apply a recipe into a fresh graph; start every creature.

    ``llm`` forwards to ``engine.apply_recipe`` so the
    ``TerrariumCreate.llm`` API field takes effect (previously ignored).

    In lab-host mode the host runs no agent engine but exposes a
    ``coordination_engine`` (a bare Terrarium kept for cross-node
    channel coordination).  ``as_engine`` would raise here — instead
    we apply the recipe against the coordination engine so the
    dashboard's "New Terrarium" modal still works without a worker
    target.  The recipe's creatures then live on the host's
    coordination engine; this is the only place we accept agents on
    that engine, and it is gated on the lab-host's explicit
    presence of one.
    """
    if isinstance(service, MultiNodeTerrariumService):
        return await _start_remote_terrarium(
            service,
            config_path=config_path,
            name=name,
            pwd=pwd,
            llm=llm,
            on_node=on_node,
        )

    engine = _resolve_engine_for_recipe(service)
    pwd = _normalize_pwd(pwd)
    if config_path:
        # The config loader resolves package references.
        cfg = load_terrarium_config(config_path)
    elif config is not None:
        cfg = config
    else:
        raise ValueError("Must provide config_path or config")

    # Studio owns the richer recipe metadata store. Disabling autosession avoids
    # two live handles for the same path, which would prevent deletion on Windows.
    graph = await engine.apply_recipe(
        cfg, pwd=pwd, llm=llm, strict=False, session=False
    )
    sid = graph.graph_id

    # Recipe sessions persist Studio-specific topology metadata.
    try:
        sess_dir = _session_dir()
        Path(sess_dir).mkdir(parents=True, exist_ok=True)
        store = SessionStore(Path(sess_dir) / f"{sid}.kohakutr", writer_lock=True)
        store.init_meta(
            session_id=sid,
            config_type="terrarium",
            config_path=config_path or "",
            pwd=pwd or str(default_workdir()),
            agents=[c.name for c in cfg.creatures] + (["root"] if cfg.root else []),
            terrarium_name=cfg.name,
            terrarium_channels=[
                {
                    "name": ch.name,
                    "type": ch.channel_type,
                    "description": ch.description,
                }
                for ch in cfg.channels
            ],
            terrarium_creatures=[
                {
                    "name": c.name,
                    "listen": c.listen_channels,
                    "send": c.send_channels,
                }
                for c in cfg.creatures
            ],
        )
        await engine.attach_session(sid, store)
        stores_for(service)[sid] = store
        _index_hooks.attach(sid, store, sess_dir)
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("Session store creation failed", error=str(e))

    meta_for(service)[sid] = {
        "name": (name.strip() if name and name.strip() else cfg.name),
        "config_path": config_path or "",
        "pwd": pwd or str(default_workdir()),
        "created_at": _now_iso(),
        "has_root": cfg.root is not None,
    }
    logger.info("Terrarium session started", session_id=sid)
    return _build_session_handle(engine, sid, meta_for(service))


async def _start_remote_terrarium(
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
        session_path=f"sessions/{name or source_path.stem}.kohakutr",
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
        base_name = name or load_terrarium_config(config_path).name
        existing_names = {entry.get("name") for entry in meta_for(service).values()}
        session_name = base_name
        suffix = 2
        while session_name in existing_names:
            session_name = f"{base_name} #{suffix}"
            suffix += 1
        created_at = now_iso()
    except BaseException:
        for creature_id in registered_ids:
            if service._home.get(creature_id) == on_node:
                service._home.pop(creature_id, None)
        for creature in reversed(creatures):
            try:
                await remote_service.remove_creature(creature.creature_id)
            except BaseException:
                logger.exception(
                    "remote recipe compensation failed",
                    extra={"creature_id": creature.creature_id},
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


def list_sessions(service: "TerrariumService") -> list[SessionListing]:
    """List every active session (one per graph).

    Includes both host-local graphs (walked off the engine) AND
    remote-hosted sessions tracked in ``_meta`` from a previous
    ``start_creature(... on_node="worker-X")`` call.  The remote
    branch trusts the meta entry's ``on_node`` field as proof the
    session exists; staleness gets reconciled when the controller
    fans out a ``service.list_creatures()`` round-trip.

    Cross-node clusters are FOLDED post-walk: when
    ``service._cluster_links`` records a pair of remote sids as
    cross-connected, the two per-spawn listings collapse into ONE
    listing addressed by the lex-smallest sid (matching the cluster
    id used by the runtime-graph snapshot fold). This is the studio-
    tier equivalent of the standalone-mode ``session_coord.apply_merge``
    fold — without it the user sees two rail entries after a cross-
    node connect.
    """
    # Standalone services expose local graphs; lab hosts rely on remote metadata.
    engine = host_engine_or_none(service)
    meta_registry = meta_for(service)
    out: list[SessionListing] = []
    seen: set[str] = set()
    for graph in engine.list_graphs() if engine is not None else []:
        meta = meta_registry.get(graph.graph_id, {})
        out.append(
            SessionListing(
                session_id=graph.graph_id,
                name=meta.get("name", graph.graph_id),
                running=True,
                creatures=len(graph.creature_ids),
                node_id=meta.get("on_node", "_host"),
            )
        )
        seen.add(graph.graph_id)
    # Remote metadata makes worker sessions visible, but membership still
    # determines liveness so disconnected workers do not leave zombie listings.
    connected: set[str] = set()
    connected_fn = getattr(service, "connected_nodes", None)
    # An empty membership set is authoritative when the service supports the
    # query; it means every worker is disconnected, not that liveness is unknown.
    have_membership = callable(connected_fn)
    if have_membership:
        try:
            connected = set(connected_fn())
        except Exception:  # pragma: no cover - defensive
            connected = set()
            have_membership = False
    for sid in list(meta_registry.keys()):
        meta = meta_registry.get(sid)
        if meta is None or sid in seen or not meta.get("on_node"):
            continue
        node = meta.get("on_node")
        if have_membership and node not in connected:
            # Purge metadata once its owning worker leaves membership.
            meta_registry.pop(sid, None)
            continue
        out.append(
            SessionListing(
                session_id=sid,
                name=meta.get("name", sid),
                running=True,
                creatures=1,
                node_id=meta.get("on_node", "_host"),
            )
        )
    return cluster_fold.fold_session_listings(out, service)


def get_session(service: "TerrariumService", session_id: str) -> Session:
    """Return a full :class:`Session` handle for a graph_id.

    Raises :class:`KeyError` if the session does not exist.  Remote
    sessions (created via ``start_creature(... on_node=...)``) are
    looked up in ``_meta`` and re-synthesised from there since the
    controller has no engine handle to walk.
    """
    # Cluster sessions are addressed through their primary member.
    session_id = cluster_fold.sid_to_primary(service).get(session_id, session_id)
    # Lab hosts reconstruct sessions from metadata because they have no agent engine.
    engine = host_engine_or_none(service)
    meta_registry = meta_for(service)
    if engine is not None and session_id in {g.graph_id for g in engine.list_graphs()}:
        return _build_session_handle(engine, session_id, meta_registry)
    meta = meta_registry.get(session_id)
    if meta is not None and meta.get("on_node"):
        home = meta.get("on_node", "_host") or "_host"
        # A cluster Session exposes every member creature while preserving each
        # creature's home node.
        clustered = cluster_fold.fold_session_creatures(
            service, session_id, meta_registry
        )
        if clustered is not None:
            return Session(
                session_id=session_id,
                name=meta.get("name", session_id),
                creatures=clustered,
                channels=[],
                has_root=False,
                pwd=meta.get("pwd", ""),
                created_at=meta.get("created_at", ""),
                config_path=meta.get("config_path", ""),
                home_node=home,
            )
        # Older metadata may lack the worker-side creature identity.
        cid = meta.get("creature_id") or session_id
        return Session(
            session_id=session_id,
            name=meta.get("name", session_id),
            creatures=[
                {
                    "creature_id": cid,
                    "name": meta.get("name", ""),
                    "home_node": home,
                    # Model metadata is cached at spawn and model switches.
                    "model": str(meta.get("model", "") or ""),
                    "llm_name": str(meta.get("llm_name", "") or ""),
                    "running": bool(meta.get("running", True)),
                    "is_privileged": bool(meta.get("is_privileged", False)),
                }
            ],
            channels=[],
            # Root presence is a recipe property rather than a privilege flag.
            has_root=bool(meta.get("has_root", False)),
            pwd=meta.get("pwd", ""),
            created_at=meta.get("created_at", ""),
            config_path=meta.get("config_path", ""),
            home_node=home,
        )
    # Non-primary cluster IDs were normalized above, so this ID is unknown.
    raise KeyError(f"session {session_id!r} not found")


def update_remote_creature_model_meta(
    service: "TerrariumService",
    creature_id: str,
    *,
    model: str = "",
    llm_name: str = "",
) -> None:
    """Delegator — see :func:`remote_meta.update_remote_creature_model_meta`."""
    remote_meta.update_remote_creature_model_meta(
        meta_for(service), creature_id, model=model, llm_name=llm_name
    )


async def refresh_remote_creature_meta(
    service: "TerrariumService", session_id: str
) -> None:
    """Delegator — see :func:`remote_meta.refresh_remote_creature_meta`.

    Resolves cluster members via :mod:`cluster_fold` and forwards the
    list so the remote-meta module stays free of cluster-fold imports.
    """
    primary = cluster_fold.sid_to_primary(service).get(session_id)
    groups = cluster_fold.cluster_groups(service) if primary is not None else {}
    members: list[str] = list(groups.get(primary, set())) if primary is not None else []
    await remote_meta.refresh_remote_creature_meta(
        meta_for(service), service, session_id, cluster_members=members
    )


async def get_session_async(service: "TerrariumService", session_id: str) -> Session:
    """Async variant of :func:`get_session` that refreshes remote
    creature meta before returning (B3/B4 — tab-reopen path) and
    populates the cluster's channels by querying each member graph
    (B10 — cluster-folded sessions reported 0 channels)."""
    try:
        engine = host_engine_or_none(service)
        if engine is not None and session_id in {
            g.graph_id for g in engine.list_graphs()
        }:
            return _build_session_handle(engine, session_id, meta_for(service))
    except Exception:  # pragma: no cover - defensive
        pass
    await refresh_remote_creature_meta(service, session_id)
    sess = get_session(service, session_id)
    # Prefer the live cluster roster so newly spawned peers appear before metadata sync.
    live = await cluster_fold.refresh_cluster_creatures_live(service, session_id)
    if live:
        pid = cluster_fold.sid_to_primary(service).get(session_id, session_id)
        folded = _fold_session_creatures(service, pid, live_creatures=live)
        if folded:
            sess.creatures = folded
    # Multi-node clusters have no shared host environment, so channel discovery
    # fans out across members and deduplicates by name.
    primary = cluster_fold.sid_to_primary(service).get(session_id, session_id)
    groups = cluster_fold.cluster_groups(service)
    member_sids = groups.get(primary, {primary})
    channels: list[dict] = []
    seen: set[str] = set()
    for sid in sorted(member_sids):
        try:
            chs = await service.list_channels(sid)
        except (KeyError, Exception):  # noqa: BLE001 — best-effort union
            continue
        for ch in chs or ():
            name = getattr(ch, "name", None) or (
                ch.get("name") if isinstance(ch, dict) else None
            )
            if not name or name in seen:
                continue
            seen.add(name)
            if isinstance(ch, dict):
                channels.append(ch)
            else:
                channels.append(
                    {
                        "name": name,
                        "channel_type": getattr(ch, "channel_type", "broadcast"),
                        "description": getattr(ch, "description", ""),
                    }
                )
    if channels:
        sess.channels = channels
    return sess


def rename_session(service: "TerrariumService", session_id: str, name: str) -> Session:
    """Update the display name of a session. When the session has a
    single creature, the creature is renamed too so the rail label
    and the agent's identity stay in sync.

    Lab-host path: the session lives on a worker, so we cannot reach
    in and mutate the live ``Creature``.  We update the host-side
    ``_meta`` (which drives the rail label) and synthesise the
    Session handle from there.  The worker-side agent keeps its
    config name — a known limitation until a Protocol-level rename
    verb exists.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("name must not be empty")
    engine = host_engine_or_none(service)
    meta_registry = meta_for(service)
    if engine is not None and session_id in {g.graph_id for g in engine.list_graphs()}:
        meta = meta_registry.setdefault(session_id, {})
        meta["name"] = name
        graph = next(g for g in engine.list_graphs() if g.graph_id == session_id)
        if len(graph.creature_ids) == 1:
            for cid in graph.creature_ids:
                try:
                    creature = engine.get_creature(cid)
                except KeyError:
                    continue
                apply_creature_name(creature, name)
                break
        return _build_session_handle(engine, session_id, meta_registry)
    # Remote sessions can update only host-side metadata until rename is service-routed.
    meta = meta_registry.get(session_id)
    if meta is None:
        raise KeyError(f"session {session_id!r} not found")
    meta["name"] = name
    return get_session(service, session_id)


def rename_creature(service: "TerrariumService", creature_id: str, name: str) -> dict:
    """Rename a creature. Mirrors onto session meta name only when
    the creature is the sole inhabitant of its session — otherwise
    the rail still shows the session's display name and individual
    creatures are addressed by name within the session.

    Lab-host path: the creature lives on a worker. We do not have a
    Protocol-level rename verb yet, so we update only the host-side
    session ``_meta["name"]`` (which drives the rail label) when the
    target session is solo-creature, and return a synthesised status
    dict.  The worker-side agent keeps its config name until a
    Protocol-level rename exists; this avoids 500-ing the route in
    lab-host mode where the route used to crash on engine access.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("name must not be empty")
    engine = host_engine_or_none(service)
    meta_registry = meta_for(service)
    if engine is not None:
        creature = engine.get_creature(creature_id)
        topology = getattr(engine, "_topology", None)
        graph_id = (
            topology.creature_to_graph.get(creature.creature_id)
            if topology is not None
            else None
        )
        if graph_id is not None:
            ensure_graph_name_available(
                engine._topology,
                engine._creatures,
                graph_id=graph_id,
                name=name,
                exclude_id=creature.creature_id,
            )
        apply_creature_name(creature, name)
        sid = creature.graph_id
        graph = next(
            (g for g in engine.list_graphs() if g.graph_id == sid),
            None,
        )
        if graph is not None and len(graph.creature_ids) == 1:
            meta = meta_registry.get(sid)
            if meta is not None:
                meta["name"] = name
        return creature.get_status()
    # Remote rename updates host metadata after resolving ownership.
    home_lookup = getattr(service, "_home", None)
    if not isinstance(home_lookup, dict) or creature_id not in home_lookup:
        raise KeyError(f"creature {creature_id!r} not found")
    sid = None
    for candidate_sid, meta in meta_registry.items():
        if meta.get("creature_id") == creature_id:
            sid = candidate_sid
            break
    if sid is not None:
        meta_registry[sid]["name"] = name
    return {
        "creature_id": creature_id,
        "name": name,
        "graph_id": sid or "",
        "home_node": home_lookup.get(creature_id, ""),
    }


def _persist_cluster_members_to_mirror(service, session_id):
    """Thin delegator — see ``cluster_fold.persist_cluster_members_to_mirror``."""
    cluster_fold.persist_cluster_members_to_mirror(
        service, session_id, Path(_session_dir()) / "mirror"
    )


async def stop_session(service: "TerrariumService", session_id: str) -> None:
    """Stop a runtime while keeping the conversation open and dormant."""
    await _stop.stop_session(
        service,
        session_id,
        meta=meta_for(service),
        session_stores=stores_for(service),
        mirror_dir=Path(_session_dir()) / "mirror",
        index_hooks=_index_hooks.registry(),
    )


async def end_session(service: "TerrariumService", session_id: str) -> None:
    """Explicitly end a conversation and remove its runtime."""
    await _stop.stop_session(
        service,
        session_id,
        meta=meta_for(service),
        session_stores=stores_for(service),
        mirror_dir=Path(_session_dir()) / "mirror",
        index_hooks=_index_hooks.registry(),
        end_conversation=True,
    )


async def add_creature(
    service: "TerrariumService", session_id: str, config: CreatureConfig
) -> str:
    """Hot-plug a creature into an existing session.  Returns creature_id.

    The new creature is bound to the session's existing session store
    so its turns / tool calls / events persist like every other
    creature in the graph — without this it would run un-persisted and
    its history would be lost on resume.

    Lab-host path: the session lives on a worker, so route the spawn
    through ``service.add_creature(..., on_node=<worker>)``.  Without
    this branch the helper would call ``as_engine(service)`` and 500
    in lab-host mode (the host runs no agent engine).
    """
    # Local additions use the engine so graph membership and store attachment agree.
    engine = host_engine_or_none(service)
    if engine is not None:
        if session_id not in {g.graph_id for g in engine.list_graphs()}:
            raise KeyError(f"session {session_id!r} not found")
        creature = await engine.add_creature(config, graph=session_id)
        # The existing graph store is reused, and its config type must be resumable.
        attach_session_store_for_creature(service, creature, config_type="agent")
        return creature.creature_id

    # Remote additions route to the worker recorded in session metadata.
    meta = meta_for(service).get(session_id)
    if meta is None or not meta.get("on_node"):
        raise KeyError(f"session {session_id!r} not found")
    on_node = meta["on_node"]
    info = await service.add_creature(
        config,
        graph_id=session_id,
        on_node=on_node,
    )
    return info.creature_id


def list_creatures(service: "TerrariumService", session_id: str) -> list[dict]:
    """List every creature currently in a session.

    Each entry is annotated with ``home_node`` so the frontend can
    show a site chip without cross-referencing the runtime graph.

    Local-graph path: walk ``engine.list_graphs()`` and read each
    creature off the host engine.  ``home_node`` reflects the
    service's ``_home`` registry when present, else falls back to
    ``service.node_id`` / ``_host``.

    Remote-graph path: the host engine has no entry for the graph,
    but the lifecycle ``_meta`` registry remembers the remote spawn
    and we synthesise a one-creature listing from it.  Without this
    fallback the route 404s for every worker-spawned session.
    """
    # Lab hosts skip local graph lookup and use remote metadata.
    engine = host_engine_or_none(service)
    graph = None
    if engine is not None:
        for g in engine.list_graphs():
            if g.graph_id == session_id:
                graph = g
                break

    if graph is not None:
        home_lookup = getattr(service, "_home", None)
        default_home = getattr(service, "node_id", None) or "_host"
        out: list[dict] = []
        for cid in graph.creature_ids:
            try:
                c = engine.get_creature(cid)
            except KeyError:
                continue
            status = c.get_status()
            if isinstance(home_lookup, dict) and cid in home_lookup:
                status["home_node"] = home_lookup[cid]
            else:
                status["home_node"] = default_home
            out.append(status)
        return out

    # Remote-spawn metadata supplies the listing when no local graph exists.
    meta = meta_for(service).get(session_id)
    if meta is not None and meta.get("on_node"):
        home = meta.get("on_node", "_host") or "_host"
        return [
            {
                "creature_id": meta.get("creature_id") or session_id,
                "agent_id": meta.get("creature_id") or session_id,
                "name": meta.get("name", ""),
                "graph_id": session_id,
                "running": bool(meta.get("running", True)),
                "home_node": home,
                "is_privileged": bool(meta.get("is_privileged", False)),
                # Cached model metadata keeps remote creature listings informative.
                "model": str(meta.get("model", "") or ""),
                "llm_name": str(meta.get("llm_name", "") or ""),
            }
        ]
    raise KeyError(f"session {session_id!r} not found")


async def remove_creature(
    service: "TerrariumService", session_id: str, creature_id: str
) -> bool:
    """Remove a creature from a running session.

    Lab-host path: route through ``service.remove_creature`` so the
    worker hosting the creature gets the removal RPC.  Without this
    branch the helper would call ``as_engine(service)`` and 500 in
    lab-host mode.
    """
    engine = host_engine_or_none(service)
    if engine is not None:
        if session_id not in {g.graph_id for g in engine.list_graphs()}:
            raise KeyError(f"session {session_id!r} not found")
        try:
            engine.get_creature(creature_id)
        except KeyError:
            return False
        await engine.remove_creature(creature_id)
        return True

    # Remote removal requires tracked ownership and service routing to the worker.
    meta = meta_for(service).get(session_id)
    if meta is None or not meta.get("on_node"):
        raise KeyError(f"session {session_id!r} not found")
    try:
        await service.remove_creature(creature_id)
    except KeyError:
        return False
    return True


def _build_session_handle(
    engine: Terrarium,
    session_id: str,
    meta_registry: dict[str, dict[str, Any]] | None = None,
) -> Session:
    """Build a :class:`Session` handle from a live graph.

    ``meta_registry`` is the caller's per-runtime meta dict (see
    :mod:`studio.sessions.registry`); falls back to the engine's own
    registry — correct for every standalone path, where service and
    engine share one anchor.
    """
    graph = None
    for g in engine.list_graphs():
        if g.graph_id == session_id:
            graph = g
            break
    if graph is None:
        raise KeyError(f"session {session_id!r} not found")

    if meta_registry is None:
        meta_registry = meta_for(engine)
    meta = meta_registry.get(session_id, {})
    home_node = meta.get("on_node", "_host") or "_host"
    creatures: list[dict] = []
    for cid in graph.creature_ids:
        try:
            c = engine.get_creature(cid)
        except KeyError:
            continue
        status = c.get_status()
        # Every creature in a graph shares the graph's home site.
        status["home_node"] = home_node
        creatures.append(status)

    channels: list[dict] = []
    env = engine._environments.get(session_id)
    if env is not None:
        channels = env.shared_channels.get_channel_info()

    return Session(
        session_id=session_id,
        name=meta.get("name", session_id),
        creatures=creatures,
        channels=channels,
        created_at=meta.get("created_at", ""),
        config_path=meta.get("config_path", ""),
        pwd=meta.get("pwd", ""),
        has_root=meta.get("has_root", False),
        home_node=home_node,
    )


async def find_session_for_creature(
    service: "TerrariumService", creature_id: str
) -> str | None:
    """Look up the session_id (graph_id) hosting a creature.

    Routes through the :class:`TerrariumService` Protocol — NOT a local
    engine reach-in — so a creature living on a worker node resolves
    just like a host-local one. ``as_engine(service)`` would only ever
    see the host's own engine and 404 every remote creature.
    """
    info = await service.get_creature_info(creature_id)
    return info.graph_id if info is not None else None
