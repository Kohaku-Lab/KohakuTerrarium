"""Engine-backed session lifecycle.

Replaces ``KohakuManager.agent_create / agent_list / agent_status /
agent_stop`` plus the ``terrarium_create / list / status / stop`` and
``creature_add / list / remove`` clusters.

A *session* is a Terrarium engine *graph*.  ``start_creature`` mints a
fresh 1-creature graph; ``start_terrarium`` applies a recipe into one
graph holding every creature.  Per-creature operations live in
``creature_*.py`` siblings and accept ``(session_id, creature_id)``.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kohakuterrarium.session.store import SessionStore
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
from kohakuterrarium.terrarium import TerrariumService
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.utils.logging import get_logger
from kohakuterrarium.utils.mobile_sandbox import default_workdir

logger = get_logger(__name__)


# Session bookkeeping (meta + attached stores) is INSTANCE-scoped: it
# lives on the runtime (engine / multi-node service) via
# ``studio.sessions.registry`` — see ``meta_for`` / ``stores_for``.
# Two services or engines in one process no longer cross-contaminate.

# Legacy private aliases — tests reach in via these names.
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


# Legacy private alias — internal callers + tests use the old name.
_now_iso = now_iso


# ---------------------------------------------------------------------------
# start_creature — mint a fresh 1-creature graph
# ---------------------------------------------------------------------------


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
    # B5: _normalize_pwd stats the HOST filesystem — only valid for
    # host-targeted spawns. Remote spawns trust the caller's pwd and let
    # the worker engine's add_creature validate against its own disk.
    if on_node == "_host":
        pwd = _normalize_pwd(pwd)
        # Lab-host mode (service exposes connected_nodes) runs NO agents
        # on the host — reject host-targeted spawns; caller must pick a
        # worker. Silent host-engine spawn would wedge the cluster.
        if hasattr(service, "connected_nodes"):
            raise ValueError(
                "lab-host mode runs no agents on the host — spawn on a "
                "worker node (pass on_node=<worker name>)"
            )
        # Standalone path — direct engine call.  ``strict=False``: Studio
        # spawns keep degrade-and-continue (dashboard rebinds models);
        # ``@pkg/...`` refs pass through — ``load_agent_config`` resolves.
        # ``name=`` MUST travel through add_creature (not be applied
        # post-hoc): the engine's autosession may attach a SessionStore
        # during add_creature, and the SessionOutput event-key prefix is
        # baked from ``config.name`` at attach time.  A post-hoc rename
        # left agent events keyed under the config name while the chat
        # WS wrote user events under the display name — history (read by
        # display name) then showed ONLY the user's prompts.
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

    # Remote-node path: route through the service's add_creature with
    # ``on_node=...``.  The controller's _home registry tracks the
    # creature; we return a synthesised Session handle with a
    # placeholder graph_id from the response.  Session-store attach
    # happens on the worker via ``WorkerSessionAttacher``.
    spawn_payload: Any = config if config is not None else config_path
    if spawn_payload is None:
        raise ValueError("Must provide config_path or config")
    # ``@pkg/...`` refs travel verbatim — the WORKER resolves them
    # against its own installed packages.
    info = await service.add_creature(
        spawn_payload,
        is_privileged=True,
        pwd=pwd,
        llm=llm,
        on_node=on_node,
        name=name.strip() if name and name.strip() else None,
    )
    sid = info.graph_id
    meta_for(service)[sid] = {
        "name": info.name,
        "config_path": config_path or "",
        "pwd": pwd or "",
        "created_at": _now_iso(),
        "on_node": on_node,
        # Track the worker-side creature_id so subsequent
        # ``get_session`` calls can re-synthesise the Session with the
        # real id (the host has no engine handle to walk for remote).
        "creature_id": info.creature_id,
        # Cache the resolved model + llm_name so subsequent host-side
        # reads (``get_session`` / ``list_creatures`` remote branch)
        # surface a non-empty model chip even when the worker is
        # briefly unreachable.  Without these the UI flips to
        # "No model" on the next read after spawn (B3 / B4).
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
    # Synthesise a minimal Session — listing endpoints union via
    # service.list_creatures(). Both session-level and per-creature
    # ``home_node`` MUST carry ``on_node`` so the frontend renders the
    # site chip, PTY badge, and cluster routing correctly.
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
                # Surface the resolved model so the frontend's spawn
                # path renders the model chip without a follow-up status
                # fetch.  Empty string when the agent ended up with a
                # ``DeferredLLMProvider`` — that is the "no model
                # configured yet, user must switch_model" state.
                "model": getattr(info, "model", "") or "",
                # The canonical identifier (e.g. ``"openai/gpt-4o"``).
                # ModelSwitcher.vue prefers this over ``model`` — without
                # it the model chip falls back to "No model" the moment
                # the next read happens (B3).
                "llm_name": getattr(info, "llm_name", "") or "",
            }
        ],
        channels=[],
        # ``has_root`` is the recipe-level flag (only ``start_terrarium``
        # sets it); single-creature spawn has no recipe so no root — do
        # NOT conflate with ``is_privileged`` or the frontend addresses
        # chat via literal ``"root"`` and 404s (B9).
        has_root=False,
        pwd=pwd or "",
        created_at=_now_iso(),
        config_path=config_path or "",
        home_node=on_node,
    )


# ---------------------------------------------------------------------------
# start_terrarium — apply a recipe into a single graph
# ---------------------------------------------------------------------------


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
    engine = _resolve_engine_for_recipe(service)
    pwd = _normalize_pwd(pwd)
    if config_path:
        # ``@pkg/...`` refs resolve inside ``load_terrarium_config``.
        cfg = load_terrarium_config(config_path)
    elif config is not None:
        cfg = config
    else:
        raise ValueError("Must provide config_path or config")

    # ``session=False``: Studio mints its own richer store below
    # (terrarium_name / terrarium_channels / terrarium_creatures meta
    # the resume + viewer paths read).  Under autosession
    # (``Terrarium(session_dir=...)``) ``apply_recipe`` would otherwise
    # mint a store at the SAME ``<graph_id>.kohakutr`` path first; the
    # Studio store then re-opens that file as a second live handle and
    # the engine-minted handle leaks — on Windows the lingering handle
    # locks the file, so deleting the saved session after stop 409s
    # with WinError 32.
    graph = await engine.apply_recipe(
        cfg, pwd=pwd, llm=llm, strict=False, session=False
    )
    sid = graph.graph_id

    # Session-store auto-attach.
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


# ---------------------------------------------------------------------------
# query / stop
# ---------------------------------------------------------------------------


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
    # Lab-host mode runs no host engine — ``host_engine_or_none``
    # returns ``None`` and we go straight to the remote ``_meta``
    # branch.  Standalone walks its host-local graphs as before.
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
    # Remote sessions — tracked by remote-spawn but invisible to the
    # local engine.  Without this branch the new ``on_node`` spawn
    # path successfully creates a creature on a worker but the
    # listing endpoints never show it.
    #
    # Liveness is NOT assumed: a ``_meta`` entry only proves the session
    # was spawned, not that its worker is still connected. We cross-check
    # against the service's currently-connected nodes (a sync read on
    # ``MultiNodeTerrariumService``) and PURGE the stale entry when the
    # worker is gone — otherwise a disconnected worker leaves a zombie
    # session listed ``running`` forever.
    connected: set[str] = set()
    connected_fn = getattr(service, "connected_nodes", None)
    # ``have_membership`` distinguishes "the service can tell me which
    # workers are connected" from "an empty connected set".  An empty
    # set is a LEGITIMATE state — every worker disconnected — and MUST
    # still purge zombie ``_meta`` entries.  Gating the purge on a
    # *non-empty* ``connected`` (the old behaviour, which worked only
    # because ``_host`` was always in the set) left the last worker's
    # session listed ``running`` forever after it left.
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
            # The worker hosting this session disconnected — drop the
            # stale meta so the listing self-heals on the next poll.
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
    # S6-1: redirect live non-primary cluster sid to primary before
    # meta lookup; the trailing fallback only fires for purged meta.
    session_id = cluster_fold.sid_to_primary(service).get(session_id, session_id)
    # Standalone walks its host engine; lab-host has none — fall
    # straight through to the remote ``_meta`` branch.
    engine = host_engine_or_none(service)
    meta_registry = meta_for(service)
    if engine is not None and session_id in {g.graph_id for g in engine.list_graphs()}:
        return _build_session_handle(engine, session_id, meta_registry)
    meta = meta_registry.get(session_id)
    if meta is not None and meta.get("on_node"):
        home = meta.get("on_node", "_host") or "_host"
        # Cross-node cluster fold: when ``session_id`` is the primary
        # of a cluster recorded in ``service._cluster_links``, union
        # creatures from every member so the single Session handle
        # exposes both alpha + bravo (preserving each creature's
        # per-member ``home_node``). Without this, opening the primary
        # cluster sid returns only the primary's own creature and the
        # frontend can't render the multi-site chat tab.
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
        # Real worker-side creature_id was stored at spawn time; the
        # session_id fallback is only used for pre-1.5.0 meta entries.
        cid = meta.get("creature_id") or session_id
        return Session(
            session_id=session_id,
            name=meta.get("name", session_id),
            creatures=[
                {
                    "creature_id": cid,
                    "name": meta.get("name", ""),
                    "home_node": home,
                    # B3/B4: surface the cached model + llm_name (kept
                    # in sync at spawn + on switch_model) so the model
                    # chip survives tab close / reopen and never shows
                    # "No model" while the worker is reachable.
                    "model": str(meta.get("model", "") or ""),
                    "llm_name": str(meta.get("llm_name", "") or ""),
                    "running": bool(meta.get("running", True)),
                    "is_privileged": bool(meta.get("is_privileged", False)),
                }
            ],
            channels=[],
            # Recipe-only flag (B9) — read cached recipe flag, not privilege.
            has_root=bool(meta.get("has_root", False)),
            pwd=meta.get("pwd", ""),
            created_at=meta.get("created_at", ""),
            config_path=meta.get("config_path", ""),
            home_node=home,
        )
    # S6-1 top-of-function redirect already mapped non-primary cluster
    # sids → primary; if we reach here the sid is genuinely unknown.
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
    # CF-12: re-fold the cluster with a live service.list_creatures()
    # roster so freshly spawned peers surface before _meta catches up.
    live = await cluster_fold.refresh_cluster_creatures_live(service, session_id)
    if live:
        pid = cluster_fold.sid_to_primary(service).get(session_id, session_id)
        folded = _fold_session_creatures(service, pid, live_creatures=live)
        if folded:
            sess.creatures = folded
    # B10: union channels across cluster members. Standalone reads channels
    # live from env.shared_channels; multi-node has no host-side env so we
    # fan out to each member via service.list_channels and dedupe by name.
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
    # Lab-host / remote-session path — meta-only update.
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
    # Lab-host path — find the session this creature belongs to via
    # the home registry and update host-side meta only.
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
    """Thin delegator — see :func:`studio.sessions.stop.stop_session`.

    Passes the lifecycle-owned registries by reference.
    """
    await _stop.stop_session(
        service,
        session_id,
        meta=meta_for(service),
        session_stores=stores_for(service),
        mirror_dir=Path(_session_dir()) / "mirror",
        index_hooks=_index_hooks.registry(),
    )


# ---------------------------------------------------------------------------
# creature add / remove inside a running session (hot-plug)
# ---------------------------------------------------------------------------


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
    # Standalone path — direct engine call so session-store attach and
    # graph membership behave exactly as the existing tests expect.
    engine = host_engine_or_none(service)
    if engine is not None:
        if session_id not in {g.graph_id for g in engine.list_graphs()}:
            raise KeyError(f"session {session_id!r} not found")
        creature = await engine.add_creature(config, graph=session_id)
        # Reuse the graph-level store (always present here — the session
        # already exists).  ``config_type`` must be a resumable literal:
        # this call used to pass ``"creature"``, which ``init_meta``
        # accepted and resume then rejected as an unknown session type.
        attach_session_store_for_creature(service, creature, config_type="agent")
        return creature.creature_id

    # Lab-host / remote-session path — the session was spawned via the
    # remote branch of ``start_creature`` and tracked in ``_meta`` with
    # an ``on_node`` field.  Route the hot-plug through the service so
    # the worker hosting the session gets the new creature.
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
    # Standalone walks its host engine; lab-host has none — drop
    # straight to the remote ``_meta`` fallback.
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

    # Remote-graph fallback — the host engine doesn't know about it
    # but ``_meta`` was populated at remote-spawn time.
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
                # B3/B4: surface the cached model + llm_name so the
                # per-session creatures listing surfaces a non-empty
                # model chip after spawn / switch_model — without these
                # the modal renders "No model" on every read.
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

    # Lab-host / remote-session path.  Validate the session is one we
    # tracked at remote-spawn time; route the removal through the
    # service so it reaches the creature's home worker.
    meta = meta_for(service).get(session_id)
    if meta is None or not meta.get("on_node"):
        raise KeyError(f"session {session_id!r} not found")
    try:
        await service.remove_creature(creature_id)
    except KeyError:
        return False
    return True


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


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
