"""FastAPI application factory."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from kohakuterrarium.api._io_executor import run_in_io_executor
from kohakuterrarium.api.auth import load_auth_config
from kohakuterrarium.api.auth import router as auth_router
from kohakuterrarium.api.auth.db import ensure_migrated as ensure_auth_migrated
from kohakuterrarium.api.auth.engine_pool import EnginePool
from kohakuterrarium.api.auth.middleware import HostTokenMiddleware
from kohakuterrarium.api.deps import _session_dir, get_engine, set_service
from kohakuterrarium.laboratory import HostConfig
from kohakuterrarium.laboratory._internal.host import HostEngine
from kohakuterrarium.laboratory._internal.membership import MembershipEvent
from kohakuterrarium.laboratory._internal.transport_ws import WebSocketTransport
from kohakuterrarium.laboratory.adapters import (
    StudioCatalogAdapter,
    StudioIdentityAdapter,
    TerrariumBroadcastAdapter,
    TerrariumOutputWireAdapter,
)
from kohakuterrarium.serving.process_metrics import get_aggregator
from kohakuterrarium.session.sync import SessionMirrorWriter
from kohakuterrarium.studio.sessions.lifecycle import get_session_meta
from kohakuterrarium.terrarium import MultiNodeTerrariumService, Terrarium
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Phase 0 stub routers — empty APIRouter()s pre-mounted so Phase 1
# agents only need to populate handlers, not edit ``app.py``. Each
# subpackage maps to a future Studio tier (catalog / identity /
# sessions / persistence / attach). The legacy single-file routes
# were removed in Phase 3; the studio layer is the only path now.
from kohakuterrarium.api.routes import app_update as app_update_route
from kohakuterrarium.api.routes import health as health_route
from kohakuterrarium.api.routes import lab_clients as lab_clients_route
from kohakuterrarium.api.routes import lab_status as lab_status_route
from kohakuterrarium.api.routes import metrics as metrics_route
from kohakuterrarium.api.routes import nodes as nodes_route
from kohakuterrarium.api.routes.attach import files as catalog_attach_files
from kohakuterrarium.api.routes.attach import policies as attach_policies
from kohakuterrarium.api.routes.catalog import builtins as catalog_builtins
from kohakuterrarium.api.routes.catalog import commands as catalog_commands
from kohakuterrarium.api.routes.catalog import creatures as catalog_creatures
from kohakuterrarium.api.routes.catalog import creatures_scan as catalog_creatures_scan
from kohakuterrarium.api.routes.catalog import extensions as catalog_extensions
from kohakuterrarium.api.routes.catalog import manifest as catalog_manifest
from kohakuterrarium.api.routes.catalog import models as catalog_models
from kohakuterrarium.api.routes.catalog import modules as catalog_modules
from kohakuterrarium.api.routes.catalog import packages as catalog_packages
from kohakuterrarium.api.routes.catalog import registry as catalog_registry
from kohakuterrarium.api.routes.catalog import schema as catalog_schema
from kohakuterrarium.api.routes.catalog import server_info as catalog_server_info
from kohakuterrarium.api.routes.catalog import skills as catalog_skills
from kohakuterrarium.api.routes.catalog import templates as catalog_templates
from kohakuterrarium.api.routes.catalog import (
    terrariums_scan as catalog_terrariums_scan,
)
from kohakuterrarium.api.routes.catalog import validate as catalog_validate
from kohakuterrarium.api.routes.catalog import workspace as catalog_workspace
from kohakuterrarium.api.routes.identity import api_keys as identity_api_keys
from kohakuterrarium.api.routes.identity import codex as identity_codex
from kohakuterrarium.api.routes.identity import config_files as identity_config_files
from kohakuterrarium.api.routes.identity import llm as identity_llm
from kohakuterrarium.api.routes.identity import mcp as identity_mcp
from kohakuterrarium.api.routes.identity import settings as identity_settings
from kohakuterrarium.api.routes.identity import ui_prefs as identity_ui_prefs
from kohakuterrarium.api.routes.persistence import artifacts as persistence_artifacts
from kohakuterrarium.api.routes.persistence import fork as persistence_fork
from kohakuterrarium.api.routes.persistence import history as persistence_history
from kohakuterrarium.api.routes.persistence import (
    memory_index as persistence_memory_index,
)
from kohakuterrarium.api.routes.persistence import resume as persistence_resume
from kohakuterrarium.api.routes.persistence import saved as persistence_saved
from kohakuterrarium.api.routes.persistence import viewer as persistence_viewer
from kohakuterrarium.api.routes import runtime_graph as runtime_graph_route
from kohakuterrarium.api.routes.sessions_v2 import active as sessions_active
from kohakuterrarium.api.routes.sessions_v2 import (
    creatures_chat as sessions_creatures_chat,
)
from kohakuterrarium.api.routes.sessions_v2 import (
    creatures_command as sessions_creatures_command,
)
from kohakuterrarium.api.routes.sessions_v2 import (
    creatures_ctl as sessions_creatures_ctl,
)
from kohakuterrarium.api.routes.sessions_v2 import (
    creatures_model as sessions_creatures_model,
)
from kohakuterrarium.api.routes.sessions_v2 import (
    creatures_modules as sessions_creatures_modules,
)
from kohakuterrarium.api.routes.sessions_v2 import (
    creatures_plugins as sessions_creatures_plugins,
)
from kohakuterrarium.api.routes.sessions_v2 import (
    creatures_state as sessions_creatures_state,
)
from kohakuterrarium.api.routes.sessions_v2 import memory as sessions_memory
from kohakuterrarium.api.routes.sessions_v2 import topology as sessions_topology
from kohakuterrarium.api.routes.sessions_v2 import wiring as sessions_wiring
from kohakuterrarium.api.studio import build_studio_router
from kohakuterrarium.api.ws import daemon_logs as ws_daemon_logs
from kohakuterrarium.api.ws import files as ws_files
from kohakuterrarium.api.ws import io as ws_io
from kohakuterrarium.api.ws import logs as ws_logs
from kohakuterrarium.api.ws import memory_build as ws_memory_build
from kohakuterrarium.api.ws import observer as ws_observer
from kohakuterrarium.api.ws import pty as ws_pty
from kohakuterrarium.api.ws import runtime_graph as ws_runtime_graph
from kohakuterrarium.api.ws import trace as ws_trace


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown.

    In standalone mode the lifespan attaches the runtime-graph prompt to
    the local engine and shuts it down at exit.  In ``lab-host`` mode it
    additionally starts a :class:`HostEngine`, wires a
    :class:`MultiNodeTerrariumService`, and installs it as the active
    service so routes calling :func:`get_service` see the multi-node
    surface; ``get_engine`` still returns the host's own local engine
    for the routes that haven't migrated.
    """
    # Process-metrics: subscribe the aggregator at startup so we
    # capture events the very first turn produces, not from the first
    # snapshot poll.
    get_aggregator()

    # Auth DB: apply pending migrations BEFORE any auth route handler
    # can fire.  Idempotent — second startup in the same process is a
    # cheap no-op.  Runs regardless of which auth layers are enabled
    # because turning L4 on at runtime later needs a ready schema.
    try:
        ensure_auth_migrated()
    except Exception:  # pragma: no cover - boot failures get logged + re-raised
        logger.exception("auth: schema migration failed at startup")
        raise

    # Per-user engine-pool reaper — sweeps idle engines so a long-running
    # multi-user host doesn't leak resources.  No-op when L4 is off
    # because no per-user engines get pooled in that mode.
    engine_pool: EnginePool | None = getattr(app.state, "engine_pool", None)
    if engine_pool is not None:
        await engine_pool.start_reaper()

    lab_mode = getattr(app.state, "lab_mode", "standalone")
    host_engine = None
    multi_node_service = None
    coordination_engine = None
    membership_task = None
    identity_adapter = None
    catalog_adapter = None
    broadcast_adapter = None
    output_wire_adapter = None
    mirror_writer = None

    if lab_mode == "lab-host":
        # Pre-create the operator blocklist so the lab-clients route
        # never has to do the lazy-init dance. Lifespan runs on one
        # task before any request handler — no race possible here.
        if not hasattr(app.state, "lab_blocklist"):
            app.state.lab_blocklist = set()
        bind_host, bind_port = _parse_bind(app.state.lab_bind)
        host_engine = HostEngine(
            HostConfig(
                bind_host=bind_host,
                bind_port=bind_port,
                token=app.state.lab_token,
                heartbeat_timeout_seconds=30.0,
            ),
            WebSocketTransport(),
        )
        await host_engine.start()
        # The lab-host runs NO agents — only worker processes run
        # agents.  The host keeps just a *coordination* engine: a bare
        # Terrarium that holds cross-node channel objects for the
        # broadcast / output-wire forwarders.  Nothing ever calls
        # ``add_creature`` on it; ``MultiNodeTerrariumService`` routes
        # every agent op to a connected worker.
        coordination_engine = Terrarium(session_dir=_session_dir())
        multi_node_service = MultiNodeTerrariumService(
            host=host_engine, coordination_engine=coordination_engine
        )
        # Wire the meta-lookup so ``runtime_graph_snapshot`` enriches
        # each worker graph with the studio-tier session metadata (name
        # / kind / created_at / config_path).  Terrarium tier can't
        # reach studio directly, so we inject the callable here at boot.
        multi_node_service.set_runtime_graph_meta_lookup(get_session_meta)
        set_service(multi_node_service)
        # Host-side adapters that workers query.
        identity_adapter = StudioIdentityAdapter(host_engine)
        # Catalog adapter on the host answers "what's installed here"
        # reads from the aggregator.  ``is_host=True`` rejects the
        # mutating ops (install/uninstall) from any worker — the host's
        # local installs go through the operator-facing Studio API, not
        # through this RPC, so an authed worker can't ``git clone`` on
        # the operator's machine.
        catalog_adapter = StudioCatalogAdapter(host_engine, is_host=True)
        # Cross-node channel forwarder on the host side.  Binds the
        # coordination engine so cross-node channel objects have a home,
        # and answers ``subscribe`` / ``inject`` RPCs from workers
        # wiring up cross-node connects.
        broadcast_adapter = TerrariumBroadcastAdapter(coordination_engine, host_engine)
        # Cross-node output-wiring forwarder on the host side.  The
        # controller installs a target resolver driven by the multi-
        # node service's ``_home`` registry so an emit can be forwarded
        # to the worker that hosts the target name.
        output_wire_adapter = TerrariumOutputWireAdapter(
            coordination_engine, host_engine
        )
        output_wire_adapter.set_target_resolver(
            _make_output_wire_target_resolver(multi_node_service)
        )
        # Session mirror — workers tee their session events here so
        # Studio's persistence reads stay local-fast.  Mirror dir is
        # under the controller's configured session dir.
        mirror_dir = Path(_session_dir()) / "mirror"
        mirror_writer = SessionMirrorWriter(host_engine, mirror_dir)
        # Membership watcher: keep the multi-node service's remote
        # registry in sync with the host's connected clients.
        membership_task = asyncio.create_task(
            _watch_membership(host_engine, multi_node_service)
        )
        # Stash on app.state so a programmatic caller (or a follow-up
        # admin route) can reach them without poking module globals.
        app.state.lab_host_engine = host_engine
        app.state.identity_adapter = identity_adapter
        app.state.session_mirror = mirror_writer

    # The runtime-graph prompt block is a host-agent feature.  In
    # lab-host mode the host runs no agents, so there is nothing to
    # attach it to — only standalone mode has a host-local engine.
    if multi_node_service is None:
        get_engine()._runtime_prompt.attach()
    try:
        yield
    finally:
        # Detach loop-bound listeners so repeated lifespan cycles can
        # reattach them to the next event loop.  Standalone only — the
        # lab-host never attached one.
        if multi_node_service is None:
            try:
                engine = get_engine()
                engine._runtime_prompt.detach()
            except Exception:  # pragma: no cover - defensive
                pass
        # Tear down the engine pool's reaper + any cached engines so
        # repeated lifespan cycles don't leak background tasks.
        # ``evict_all_async`` actually awaits the engine shutdown
        # coroutines (audit-caught: the sync variant just scheduled
        # them with create_task, leaving them to "Task exception was
        # never retrieved" warnings when the loop closed).
        if engine_pool is not None:
            try:
                await engine_pool.stop_reaper()
            except Exception:  # pragma: no cover - defensive
                logger.exception("engine_pool.stop_reaper raised")
            try:
                await engine_pool.evict_all_async()
            except Exception:  # pragma: no cover - defensive
                logger.exception("engine_pool.evict_all_async raised")
        # Cancel the membership watcher first so it stops feeding the
        # service while we're tearing it down, then ``await`` the
        # cancellation to avoid "Task was destroyed but is pending".
        if membership_task is not None:
            membership_task.cancel()
            await asyncio.gather(membership_task, return_exceptions=True)
        # Close the mirror writer before stopping the host so any
        # in-flight session-sync events stop being dispatched to a
        # half-closed SessionStore.
        if mirror_writer is not None:
            try:
                mirror_writer.close()
            except Exception:  # pragma: no cover - defensive
                logger.exception("session_mirror.close failed")
        if identity_adapter is not None:
            try:
                identity_adapter.detach()
            except Exception:  # pragma: no cover - defensive
                logger.exception("identity_adapter.detach failed")
        if catalog_adapter is not None:
            try:
                catalog_adapter.detach()
            except Exception:  # pragma: no cover - defensive
                logger.exception("catalog_adapter.detach failed")
        if broadcast_adapter is not None:
            try:
                broadcast_adapter.detach()
            except Exception:  # pragma: no cover - defensive
                logger.exception("broadcast_adapter.detach failed")
        if output_wire_adapter is not None:
            try:
                output_wire_adapter.detach()
            except Exception:  # pragma: no cover - defensive
                logger.exception("output_wire_adapter.detach failed")
        # In lab-host mode the service runs no host agent engine — its
        # shutdown() is a no-op.  What DOES need tearing down is the
        # coordination engine (cross-node channel objects).  Standalone
        # mode falls through to the host-local engine path.
        if multi_node_service is not None:
            try:
                await multi_node_service.shutdown()
            except Exception:  # pragma: no cover - defensive
                logger.exception("multi_node_service.shutdown failed")
            if coordination_engine is not None:
                try:
                    await coordination_engine.shutdown()
                except Exception:  # pragma: no cover - defensive
                    logger.exception("coordination_engine.shutdown failed")
        else:
            try:
                await get_engine().shutdown()
            except Exception:  # pragma: no cover - defensive
                logger.exception("engine.shutdown failed")
        if host_engine is not None:
            try:
                await host_engine.stop()
            except Exception:  # pragma: no cover - defensive
                logger.exception("host_engine.stop failed")


def _parse_bind(bind: str) -> tuple[str, int]:
    """Parse ``host:port`` into a tuple; ``port == 0`` selects ephemeral."""
    if ":" not in bind:
        raise ValueError(f"invalid lab bind {bind!r}; expected host:port")
    host, _, port_str = bind.rpartition(":")
    return host, int(port_str)


def _make_output_wire_target_resolver(service: MultiNodeTerrariumService):
    """Build a ``target_name -> (node_id, creature_id)`` lookup.

    Used by the controller's :class:`TerrariumOutputWireAdapter` when a
    local output-wiring emit can't resolve a target locally.  We scan
    every connected node's last-known ``_home`` mapping plus the
    creatures cached from prior ``list_creatures`` fan-outs.  The
    resolver does NOT block — it reads what the service already knows.
    A miss is fine: the resolver returns ``None`` and the source's emit
    falls through to its existing "log and skip" branch.

    Returned tuple's ``node_id`` is ``"_host"`` for host-local creatures,
    which the adapter then treats as "don't forward, prefer local" by
    surfacing ``None`` to the resolver.
    """

    def resolve(target_name: str) -> tuple[str, str] | None:
        cache = getattr(service, "_creature_name_cache", None) or {}
        entry = cache.get(target_name)
        if entry is not None:
            return entry
        # No cache miss-walk: building one would require an async
        # call, which we can't do from a sync resolver.  Callers can
        # populate the cache by running ``list_creatures`` (the
        # multi-node service already refreshes it as a side effect).
        return None

    return resolve


async def _watch_membership(
    host_engine: HostEngine,
    service: MultiNodeTerrariumService,
) -> None:
    """Keep the multi-node service in sync with the host's membership.

    Cancellation (during lifespan teardown) propagates naturally; any
    other exception is logged before silencing so a buggy subscriber
    doesn't take the rest of the FastAPI app down with it.
    """
    try:
        async for event, node_id in host_engine.membership.subscribe():
            if event == MembershipEvent.JOINED:
                service.add_remote(node_id)
            elif event in (MembershipEvent.LEFT, MembershipEvent.LOST):
                service.drop_remote(node_id)
    except asyncio.CancelledError:
        raise
    except Exception:  # pragma: no cover - defensive
        logger.exception("membership watcher crashed; multi-node routing stale")


def create_app(
    creatures_dirs: list[str] | None = None,
    terrariums_dirs: list[str] | None = None,
    static_dir: Path | None = None,
    *,
    lab_mode: str = "standalone",
    lab_bind: str | None = None,
    lab_token: str | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        creatures_dirs: Directories to scan for creature configs.
        terrariums_dirs: Directories to scan for terrarium configs.
        static_dir: Path to built web frontend (web_dist/).
            When provided, serves the SPA at / with API at /api/*.
        lab_mode: ``"standalone"`` (default) or ``"lab-host"``.
        lab_bind: ``host:port`` for the Lab WebSocket transport
            (lab-host only).
        lab_token: Shared token clients must present (lab-host only).
    """
    app = FastAPI(
        title="KohakuTerrarium API",
        description="HTTP API for managing agents and terrariums",
        version="1.5.0",
        lifespan=lifespan,
    )
    # Lifespan reads these off app.state to start the Lab transport.
    app.state.lab_mode = lab_mode
    app.state.lab_bind = lab_bind or "127.0.0.1:8100"
    app.state.lab_token = lab_token or ""

    # Snapshot the auth config once at boot.  ``get_auth_config``
    # dependency reads from ``app.state.auth_config`` so all per-request
    # auth decisions see one coherent view even if env vars change
    # mid-process.  Tests that flip env mid-suite construct a fresh
    # app or reassign ``app.state.auth_config`` explicitly.
    app.state.auth_config = load_auth_config()

    # Per-user engine pool — drives ``deps.get_service`` to a
    # per-user :class:`Terrarium` when L4 is enabled.  When L4 is
    # off, the pool exists but is bypassed by the dependency (a
    # single shared engine handles every request).  Building the
    # pool here unconditionally keeps the lifespan path identical
    # across modes.  Capacity values are tunable via future
    # ``[auth]`` config knobs; defaults work for family-server scale.
    app.state.engine_pool = EnginePool(max_active=10, idle_timeout_s=1800)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # L2 — host token gate (no-op when ``auth.host_token`` empty).
    # Added AFTER CORS so the preflight short-circuits inside CORS
    # without ever hitting the auth check (preflights don't carry
    # Authorization headers; the browser sends the real request only
    # after preflight succeeds).
    app.add_middleware(HostTokenMiddleware)

    # Configure config discovery directories on the new catalog scan routers.
    if creatures_dirs or terrariums_dirs:
        catalog_creatures_scan.set_creatures_dirs(creatures_dirs or [])
        catalog_terrariums_scan.set_terrariums_dirs(terrariums_dirs or [])

    # Auth router — mounted under ``/api/auth`` exposing
    # ``/capabilities`` (Phase A) and later phases'
    # ``/register`` / ``/login`` / ``/me`` / ``/tokens`` / ...
    app.include_router(auth_router, prefix="/api/auth", tags=["auth"])

    # Sessions URL preservation — the new persistence + sessions/memory
    # routers carry the legacy ``/api/sessions/*`` URL surface that the
    # frontend's ``sessionAPI`` already calls. They are also mounted
    # under their per-concern ``/api/persistence/*`` and
    # ``/api/sessions/memory`` prefixes by ``_mount_phase0_stubs`` so
    # the studio-cleanup target shape is reachable. Both prefixes hit
    # the same router — there is no shim layer.
    app.include_router(
        persistence_saved.router, prefix="/api/sessions", tags=["sessions"]
    )
    app.include_router(
        persistence_resume.router, prefix="/api/sessions", tags=["sessions"]
    )
    app.include_router(
        persistence_fork.router, prefix="/api/sessions", tags=["sessions"]
    )
    app.include_router(
        persistence_history.router, prefix="/api/sessions", tags=["sessions"]
    )
    app.include_router(
        persistence_artifacts.router, prefix="/api/sessions", tags=["sessions"]
    )
    app.include_router(
        persistence_viewer.router, prefix="/api/sessions", tags=["sessions"]
    )
    app.include_router(
        sessions_memory.router, prefix="/api/sessions", tags=["sessions"]
    )
    # Memory-index build + status (kt embedding equivalent). Mounted
    # under /api/sessions to match the search route's URL shape.
    app.include_router(
        persistence_memory_index.router, prefix="/api/sessions", tags=["sessions"]
    )

    # Legacy URL preservation — the new catalog routers also serve under
    # the original ``/api/registry`` and ``/api/configs/*`` prefixes the
    # frontend already calls. This is URL preservation, not a shim:
    # there is exactly one router behind each URL.
    app.include_router(
        catalog_packages.router, prefix="/api/registry", tags=["registry"]
    )
    app.include_router(
        catalog_registry.router, prefix="/api/registry/remote", tags=["registry"]
    )
    # Extensions — aggregated view of plugin / tool / trigger / etc.
    # modules contributed by installed packages. Pure read-only over
    # ``packages.walk.list_packages``; no separate state.
    app.include_router(
        catalog_extensions.router,
        prefix="/api/registry/extensions",
        tags=["extensions"],
    )
    app.include_router(
        catalog_creatures_scan.router,
        prefix="/api/configs/creatures",
        tags=["configs"],
    )
    app.include_router(
        catalog_terrariums_scan.router,
        prefix="/api/configs/terrariums",
        tags=["configs"],
    )
    app.include_router(
        catalog_server_info.router,
        prefix="/api/configs/server-info",
        tags=["configs"],
    )
    app.include_router(
        catalog_models.router, prefix="/api/configs/models", tags=["configs"]
    )
    app.include_router(
        catalog_commands.router, prefix="/api/configs/commands", tags=["configs"]
    )

    # Studio (embedded authoring tool) — touch point T1
    app.include_router(build_studio_router())

    # Process-wide metrics snapshot — read by the Stats tab + the
    # Dashboard mini-strip. Subscribes the aggregator on first call so
    # mounting the route is enough to start collecting data.
    app.include_router(metrics_route.router, prefix="/api/metrics", tags=["metrics"])
    # /api/nodes — lab-only routes (404 in standalone mode).
    app.include_router(nodes_route.router, prefix="/api/nodes", tags=["nodes"])

    # Health probes — /healthz (liveness) + /readyz (readiness).
    # Mounted at root (no /api prefix) so reverse-proxy active-health
    # checks don't need to know the API namespace.  Both routes are
    # mode-aware via ``request.app.state.lab_mode``.
    app.include_router(health_route.router, tags=["health"])
    # /api/lab/status — operator dashboard snapshot of the cluster.
    app.include_router(lab_status_route.router, prefix="/api/lab", tags=["lab"])
    # /api/lab/clients/* + /api/lab/pairing-tokens/* — Sites tab verbs.
    app.include_router(lab_clients_route.router, prefix="/api/lab", tags=["lab"])
    # /api/app/* — wrapper-aware self-update HTTP surface.
    app.include_router(app_update_route.router, prefix="/api/app", tags=["app-update"])
    # /ws/app/update — progress stream for the update flow (no prefix).
    app.include_router(app_update_route.ws_router, tags=["app-update"])

    # Runtime graph snapshot — read by the graph editor data layer.
    app.include_router(
        runtime_graph_route.router, prefix="/api/runtime", tags=["runtime"]
    )

    # ── Phase 0 stub routers (empty APIRouter()s pre-mounted) ────────
    # Phase 1 agents will populate the handler bodies; mounting here
    # so URL prefixes are stable and ``app.py`` does not need to be
    # touched again.
    _mount_phase0_stubs(app)

    # WebSocket routes
    app.include_router(ws_daemon_logs.router, tags=["ws"])
    app.include_router(ws_files.router, tags=["ws"])
    app.include_router(ws_io.router, tags=["ws"])
    app.include_router(ws_logs.router, tags=["ws"])
    app.include_router(ws_memory_build.router, tags=["ws"])
    app.include_router(ws_observer.router, tags=["ws"])
    app.include_router(ws_pty.router, tags=["ws"])
    app.include_router(ws_runtime_graph.router, tags=["ws"])
    app.include_router(ws_trace.router, tags=["ws"])

    # Static file serving for built web frontend (SPA)
    if static_dir and static_dir.is_dir():
        _mount_spa(app, static_dir)

    return app


def _mount_phase0_stubs(app: FastAPI) -> None:
    """Mount the Phase 0 stub routers under their target prefixes.

    Each include_router call attaches an empty router; the URL prefix
    is reserved so Phase 1 agents only have to write the handler
    bodies. Existing legacy routes (``/api/agents``, ``/api/terrariums``,
    ``/api/sessions``, ``/api/settings``, ``/api/registry``,
    ``/api/configs``, ``/api/files``) continue to serve traffic
    alongside these stubs until the cutover lands.
    """
    # Catalog — read-only discovery
    app.include_router(
        catalog_packages.router, prefix="/api/catalog/packages", tags=["catalog"]
    )
    app.include_router(
        catalog_registry.router, prefix="/api/catalog/registry", tags=["catalog"]
    )
    app.include_router(
        catalog_creatures_scan.router,
        prefix="/api/catalog/creatures-scan",
        tags=["catalog"],
    )
    app.include_router(
        catalog_terrariums_scan.router,
        prefix="/api/catalog/terrariums-scan",
        tags=["catalog"],
    )
    app.include_router(
        catalog_models.router, prefix="/api/catalog/models", tags=["catalog"]
    )
    app.include_router(
        catalog_server_info.router,
        prefix="/api/catalog/server-info",
        tags=["catalog"],
    )
    app.include_router(
        catalog_commands.router, prefix="/api/catalog/commands", tags=["catalog"]
    )
    app.include_router(
        catalog_creatures.router, prefix="/api/catalog/creatures", tags=["catalog"]
    )
    app.include_router(
        catalog_modules.router, prefix="/api/catalog/modules", tags=["catalog"]
    )
    app.include_router(
        catalog_builtins.router, prefix="/api/catalog/builtins", tags=["catalog"]
    )
    app.include_router(
        catalog_schema.router, prefix="/api/catalog/schema", tags=["catalog"]
    )
    app.include_router(
        catalog_skills.router, prefix="/api/catalog/skills", tags=["catalog"]
    )
    app.include_router(
        catalog_templates.router, prefix="/api/catalog/templates", tags=["catalog"]
    )
    app.include_router(
        catalog_validate.router, prefix="/api/catalog/validate", tags=["catalog"]
    )
    app.include_router(
        catalog_workspace.router, prefix="/api/catalog/workspace", tags=["catalog"]
    )
    app.include_router(
        catalog_manifest.router, prefix="/api/catalog/manifest", tags=["catalog"]
    )

    # Identity — configuration state. All identity routes mount under
    # ``/api/settings`` so Phase 1's URL contract matches the legacy
    # ``/api/settings/*`` shape that ``settingsAPI`` already calls.
    app.include_router(identity_llm.router, prefix="/api/settings", tags=["identity"])
    app.include_router(
        identity_api_keys.router, prefix="/api/settings", tags=["identity"]
    )
    app.include_router(identity_codex.router, prefix="/api/settings", tags=["identity"])
    app.include_router(identity_mcp.router, prefix="/api/settings", tags=["identity"])
    app.include_router(
        identity_ui_prefs.router, prefix="/api/settings", tags=["identity"]
    )
    app.include_router(
        identity_settings.router, prefix="/api/settings", tags=["identity"]
    )
    # Advanced — raw config-file listing + editor surface.
    app.include_router(
        identity_config_files.router, prefix="/api/settings", tags=["identity"]
    )

    # Sessions — engine-backed creature ops. Stub routers live in
    # ``api/routes/sessions_v2/`` (the directory name avoids a Python
    # collision with the legacy ``api/routes/sessions.py`` module).
    # The per-creature router groups all share the URL shape
    # ``/api/sessions/{sid}/creatures/{cid}/...`` per plan §6.
    app.include_router(
        sessions_active.router, prefix="/api/sessions/active", tags=["sessions"]
    )
    app.include_router(
        sessions_topology.router,
        prefix="/api/sessions/topology",
        tags=["sessions"],
    )
    app.include_router(
        sessions_wiring.router, prefix="/api/sessions/wiring", tags=["sessions"]
    )
    app.include_router(
        sessions_creatures_ctl.router, prefix="/api/sessions", tags=["sessions"]
    )
    app.include_router(
        sessions_creatures_chat.router, prefix="/api/sessions", tags=["sessions"]
    )
    app.include_router(
        sessions_creatures_state.router, prefix="/api/sessions", tags=["sessions"]
    )
    app.include_router(
        sessions_creatures_plugins.router, prefix="/api/sessions", tags=["sessions"]
    )
    app.include_router(
        sessions_creatures_modules.router, prefix="/api/sessions", tags=["sessions"]
    )
    app.include_router(
        sessions_creatures_model.router, prefix="/api/sessions", tags=["sessions"]
    )
    app.include_router(
        sessions_creatures_command.router, prefix="/api/sessions", tags=["sessions"]
    )
    app.include_router(
        sessions_memory.router, prefix="/api/sessions/memory", tags=["sessions"]
    )

    # Persistence — file-backed saved sessions
    app.include_router(
        persistence_saved.router, prefix="/api/persistence/saved", tags=["persistence"]
    )
    app.include_router(
        persistence_resume.router,
        prefix="/api/persistence/resume",
        tags=["persistence"],
    )
    app.include_router(
        persistence_fork.router, prefix="/api/persistence/fork", tags=["persistence"]
    )
    app.include_router(
        persistence_history.router,
        prefix="/api/persistence/history",
        tags=["persistence"],
    )
    app.include_router(
        persistence_artifacts.router,
        prefix="/api/persistence/artifacts",
        tags=["persistence"],
    )
    app.include_router(
        persistence_viewer.router,
        prefix="/api/persistence/viewer",
        tags=["persistence"],
    )

    # Attach — workspace files HTTP shell. Mounts at ``/api/files``
    # (the legacy URL); Phase 1 Agent D's attach/files takes over.
    app.include_router(
        catalog_attach_files.router, prefix="/api/files", tags=["attach"]
    )

    # Attach — policy hints (informational; consumed by the macro shell's
    # Inspector Overview "IO bindings" line — never used to gate UI).
    app.include_router(attach_policies.router, prefix="/api/attach", tags=["attach"])


def _mount_spa(app: FastAPI, static_dir: Path) -> None:
    """Mount built Vue SPA with static assets and catch-all fallback.

    API and WebSocket routes are already registered above, so they take
    precedence. The catch-all only fires for unmatched paths.

    Performance: the per-request filesystem checks (``is_file`` + two
    ``resolve()`` calls) used to run synchronously on the event loop —
    under concurrent traffic ``GET /`` could block other requests by
    dozens of milliseconds (Windows path resolution + symlink traversal
    is not free).  We now:

    1. Cache ``static_dir.resolve()`` once at mount time.
    2. Short-circuit ``GET /`` (empty path) straight to ``index.html``
       — the common SPA-entry case never touches ``is_file``.
    3. Cheap-string traversal-defence (``..`` / leading slash) skips
       the resolve dance for obviously-malicious paths.
    4. Off-load the existence check to the dedicated I/O executor for
       genuine asset requests, so a slow filesystem call doesn't stall
       every concurrent route.
    """
    # Serve hashed build assets (JS, CSS, images)
    assets_dir = static_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    index_html = static_dir / "index.html"
    static_dir_resolved = static_dir.resolve()

    def _resolve_static_path(full_path: str) -> Path | None:
        """Sync helper run on the I/O executor.

        Returns the file path to serve if ``full_path`` resolves to a
        real file under ``static_dir``, else ``None``.
        """
        if not full_path or full_path.startswith("/"):
            return None
        if ".." in full_path.split("/"):
            return None
        candidate = static_dir / full_path
        if not candidate.is_file():
            return None
        try:
            resolved = candidate.resolve()
        except (OSError, ValueError):
            return None
        if not resolved.is_relative_to(static_dir_resolved):
            return None
        return candidate

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        # Common case: ``GET /`` → index.html, no filesystem walk.
        if not full_path:
            return FileResponse(str(index_html))
        # Real-file path: off-load the existence check so a slow disk
        # doesn't stall the event loop.  ``FileResponse`` itself
        # streams via anyio's threadpool, so once we've decided what
        # to serve the body transfer is non-blocking.
        target = await run_in_io_executor(_resolve_static_path, full_path)
        if target is not None:
            return FileResponse(str(target))
        # Everything else → index.html (Vue Router handles client-side routing).
        return FileResponse(str(index_html))
