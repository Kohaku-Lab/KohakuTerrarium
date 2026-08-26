"""FastAPI application factory."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from kohakuterrarium.api._io_executor import run_in_io_executor
from kohakuterrarium.api.auth import load_auth_config
from kohakuterrarium.api.auth import router as auth_router
from kohakuterrarium.api.auth.db import ensure_migrated as ensure_auth_migrated
from kohakuterrarium.api.auth.engine_pool import EnginePool
from kohakuterrarium.api.auth.middleware import HostTokenMiddleware
from kohakuterrarium.api.deps import _session_dir, get_engine, set_service
from kohakuterrarium.errors import ConflictError, KTError, NotFoundError
from kohakuterrarium.terrarium.drive.errors import (
    DriveBackpressureError,
    DriveError,
    DrivePermissionError,
    DriveRegistrationDisabledError,
    DriveRegistrationIncompatibleError,
    DriveRegistrationNotFoundError,
    DriveTransitionError,
)
from kohakuterrarium.laboratory import HostConfig
from kohakuterrarium.laboratory._internal.client import (
    RequestAbortedError,
    RequestTimeoutError,
)
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
from kohakuterrarium.studio.identity import drive_settings as _drive_settings
from kohakuterrarium.studio.sessions.lifecycle import get_session_meta
from kohakuterrarium.terrarium import MultiNodeTerrariumService, Terrarium
from kohakuterrarium.terrarium.drive.config import DriveRuntimeConfig
from kohakuterrarium.terrarium.multi_node_channels import cluster_members_for
from kohakuterrarium.utils.logging import get_logger
from kohakuterrarium.utils.startup_trace import mark as mark_startup

logger = get_logger(__name__)

# Route modules are grouped by Studio concern so this factory only owns
# application-wide mounting, middleware, and lifecycle coordination.
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
from kohakuterrarium.api.routes.catalog import marketplace as catalog_marketplace
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
from kohakuterrarium.api.routes.identity import grok as identity_grok
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
from kohakuterrarium.api.routes.persistence import open_sessions as persistence_open
from kohakuterrarium.api.routes.persistence import resume as persistence_resume
from kohakuterrarium.api.routes.persistence import saved as persistence_saved
from kohakuterrarium.api.routes.persistence import viewer as persistence_viewer
from kohakuterrarium.api.routes.persistence import saved_drives as persistence_drives
from kohakuterrarium.api.routes import runtime_graph as runtime_graph_route
from kohakuterrarium.api.routes.sessions_v2 import (
    active as sessions_active,
    creatures_chat as sessions_creatures_chat,
    creatures_command as sessions_creatures_command,
    creatures_ctl as sessions_creatures_ctl,
    creatures_model as sessions_creatures_model,
    creatures_modules as sessions_creatures_modules,
    creatures_plugins as sessions_creatures_plugins,
    creatures_state as sessions_creatures_state,
    creatures_subagents as sessions_creatures_subagents,
    drives as sessions_drives,
    memory as sessions_memory,
    topology as sessions_topology,
    wiring as sessions_wiring,
)
from kohakuterrarium.api.studio import build_studio_router
from kohakuterrarium.studio.persistence.session_index import close_session_index
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
    """Manage API-wide resources for standalone and Laboratory host modes.

    Standalone mode owns the local engine and its runtime prompt. Laboratory host
    mode owns the transport, coordination engine, multi-node service, adapters,
    membership watcher, and session mirror.
    """
    # Subscribe before requests can start turns so the first emitted event is captured.
    get_aggregator()

    # The schema must be ready before any auth handler runs. Migration is
    # idempotent and remains required when user auth is enabled after startup.
    try:
        ensure_auth_migrated()
    except Exception:  # pragma: no cover - startup failure path
        logger.exception("auth: schema migration failed at startup")
        raise

    # Reap idle per-user engines so long-running multi-user hosts release resources.
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
        # Initialize shared mutable state before request tasks begin, avoiding
        # concurrent first-write handling in the lab-clients route.
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
        # The host's Terrarium stores only cross-node channel and output-wire
        # coordination state. Creatures and Drive runtimes remain worker-local.
        coordination_engine = Terrarium(
            session_dir=_session_dir(),
            drive_config=DriveRuntimeConfig(enabled=False),
        )
        multi_node_service = MultiNodeTerrariumService(
            host=host_engine, coordination_engine=coordination_engine
        )
        # Inject Studio metadata at the API composition boundary because the
        # Terrarium tier must not depend directly on Studio.
        multi_node_service.set_runtime_graph_meta_lookup(
            partial(get_session_meta, multi_node_service)
        )
        set_service(multi_node_service)
        # Workers query host-owned identity and catalog state through adapters.
        identity_adapter = StudioIdentityAdapter(host_engine)
        # Host catalog RPC is read-only: package mutations must pass through the
        # operator-facing Studio API rather than execute on behalf of a worker.
        catalog_adapter = StudioCatalogAdapter(host_engine, is_host=True)
        # Cross-node channel objects live in the coordination engine and are
        # exposed to workers through subscribe and inject RPCs.
        broadcast_adapter = TerrariumBroadcastAdapter(coordination_engine, host_engine)
        # Output-wire emissions use the service's cached creature locations to
        # select the worker that owns each remote target.
        output_wire_adapter = TerrariumOutputWireAdapter(
            coordination_engine, host_engine
        )
        output_wire_adapter.set_name_cache(multi_node_service._creature_name_cache)
        output_wire_adapter.set_cluster_members(
            lambda graph_id: {
                member_graph_id
                for _node_id, member_graph_id in cluster_members_for(
                    multi_node_service, graph_id
                )
            }
        )
        # Mirror worker events under the host session directory so Studio reads
        # do not require a remote round trip.
        mirror_dir = Path(_session_dir()) / "mirror"
        mirror_writer = SessionMirrorWriter(host_engine, mirror_dir)
        # Membership events are the source of truth for the service's remote registry.
        membership_task = asyncio.create_task(
            _watch_membership(host_engine, multi_node_service)
        )
        # App state exposes lifecycle-owned resources without module-level globals.
        app.state.lab_host_engine = host_engine
        app.state.identity_adapter = identity_adapter
        app.state.session_mirror = mirror_writer

    # Only standalone mode has a local agent engine that can consume this prompt.
    if multi_node_service is None:
        get_engine()._runtime_prompt.attach()
    mark_startup(
        "api_lifespan_ready",
        surface=os.environ.get("KT_STARTUP_SURFACE", "web"),
    )
    try:
        yield
    finally:
        # Loop-bound listeners must detach before a later lifespan uses another loop.
        if multi_node_service is None:
            try:
                engine = get_engine()
                engine._runtime_prompt.detach()
            except Exception:  # pragma: no cover - defensive
                pass
        # Await both the reaper and cached-engine shutdowns so no tasks outlive
        # the event loop that owns them.
        if engine_pool is not None:
            try:
                await engine_pool.stop_reaper()
            except Exception:  # pragma: no cover - defensive
                logger.exception("engine_pool.stop_reaper raised")
            try:
                await engine_pool.evict_all_async()
            except Exception:  # pragma: no cover - defensive
                logger.exception("engine_pool.evict_all_async raised")
        # Stop membership updates before dismantling the service, and await
        # cancellation so the watcher cannot remain pending at loop shutdown.
        if membership_task is not None:
            membership_task.cancel()
            await asyncio.gather(membership_task, return_exceptions=True)
        # Stop session dispatch before the host and its stores become unavailable.
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
        # Laboratory mode owns a separate coordination engine; standalone mode
        # instead owns the process-local agent engine.
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

        # Close the session index last because earlier shutdown work may still read it.
        try:
            close_session_index()
        except Exception:  # pragma: no cover - defensive
            logger.exception("close_session_index failed")


def kt_error_status(exc: KTError) -> int:
    """Map framework errors to HTTP while keeping Studio transport-agnostic.

    Not-found checks must precede value checks because some configuration errors
    inherit from both families.
    """
    if isinstance(exc, (NotFoundError, FileNotFoundError)):
        return 404
    if isinstance(exc, ConflictError):
        return 409
    if isinstance(exc, ValueError):
        return 400
    return 500


async def kt_error_handler(request: Request, exc: KTError) -> JSONResponse:
    """Return a :class:`KTError` using the API's stable ``detail`` body shape."""
    _ = request
    return JSONResponse(status_code=kt_error_status(exc), content={"detail": str(exc)})


def drive_error_status(exc: DriveError) -> int:
    """Map Drive-specific failure semantics onto HTTP statuses.

    Invalid registration state and transitions are unprocessable, permission
    failures are forbidden, backpressure requests a retry, and all remaining
    cases retain the generic framework mapping.
    """
    if isinstance(exc, DrivePermissionError):
        return 403
    if isinstance(
        exc,
        (
            DriveTransitionError,
            DriveRegistrationNotFoundError,
            DriveRegistrationDisabledError,
            DriveRegistrationIncompatibleError,
        ),
    ):
        return 422
    if isinstance(exc, DriveBackpressureError):
        return 429
    return kt_error_status(exc)


async def drive_error_handler(request: Request, exc: DriveError) -> JSONResponse:
    """Drive-specific ``KTError`` → HTTP status (403/422/429 beyond the generic
    table), same ``{"detail": ...}`` body shape as :func:`kt_error_handler`."""
    _ = request
    return JSONResponse(
        status_code=drive_error_status(exc), content={"detail": str(exc)}
    )


async def lab_transport_error_handler(
    request: Request, exc: TimeoutError
) -> JSONResponse:
    """Map Laboratory transport failures onto gateway statuses: a
    worker that vanished mid-request is 502, a deadline expiry 504 —
    neither is an internal server error."""
    _ = request
    status = 502 if isinstance(exc, RequestAbortedError) else 504
    return JSONResponse(
        status_code=status,
        content={"detail": str(exc) or type(exc).__name__},
    )


def _parse_bind(bind: str) -> tuple[str, int]:
    """Parse ``host:port`` into a tuple; ``port == 0`` selects ephemeral."""
    if ":" not in bind:
        raise ValueError(f"invalid lab bind {bind!r}; expected host:port")
    host, _, port_str = bind.rpartition(":")
    return host, int(port_str)


async def _watch_membership(
    host_engine: HostEngine,
    service: MultiNodeTerrariumService,
) -> None:
    """Mirror host membership into remote routing state.

    Lifecycle cancellation propagates, while subscriber failures are contained so
    they do not terminate the FastAPI application.
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


class PollingAccessLogFilter(logging.Filter):
    """Drop uvicorn access-log lines for successful GET polls of
    high-frequency endpoints. Failures, other methods, and lookalike
    paths (path-boundary check) always stay visible."""

    _SUPPRESSED_PATHS = ("/api/sessions/active",)

    def filter(self, record: logging.LogRecord) -> bool:
        args = getattr(record, "args", None)
        if not isinstance(args, tuple) or len(args) < 5:
            return True
        method, path, status = args[1], args[2], args[4]
        if method != "GET" or not isinstance(path, str):
            return True
        if not isinstance(status, int) or status >= 400:
            return True
        for base in self._SUPPRESSED_PATHS:
            if path == base or path.startswith((base + "/", base + "?")):
                return False
        return True


def _install_access_log_filter() -> None:
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, PollingAccessLogFilter) for f in access.filters):
        access.addFilter(PollingAccessLogFilter())


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
    _install_access_log_filter()
    app = FastAPI(
        title="KohakuTerrarium API",
        description="HTTP API for managing agents and terrariums",
        version="1.5.0",
        lifespan=lifespan,
    )
    # Typed-error adapter: studio raises ``kohakuterrarium.errors``
    # types; this single handler maps them onto HTTP status codes.
    app.add_exception_handler(KTError, kt_error_handler)
    # Drive errors need 403/422/429 beyond the generic table; Starlette picks
    # the most specific handler by MRO, so this wins for every ``DriveError``.
    app.add_exception_handler(DriveError, drive_error_handler)
    # RequestAbortedError subclasses RequestTimeoutError, so this one
    # registration covers both (Starlette dispatches by MRO).
    app.add_exception_handler(RequestTimeoutError, lab_transport_error_handler)

    # Lifespan reads these off app.state to start the Lab transport.
    app.state.lab_mode = lab_mode
    app.state.lab_bind = lab_bind or "127.0.0.1:8100"
    app.state.lab_token = lab_token or ""

    # Freeze one auth configuration per application instance so all requests
    # observe coherent policy even if process environment variables change.
    app.state.auth_config = load_auth_config()

    # User-authenticated requests receive isolated Terrarium instances; other
    # modes bypass this pool. Each engine resolves a fresh immutable snapshot of
    # the operator's shared Drive policy.
    app.state.engine_pool = EnginePool(
        max_active=10,
        idle_timeout_s=1800,
        drive_resolver=_drive_settings.resolve_drive_kwargs,
    )

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

    # Authentication endpoints share one namespace for capabilities, identity,
    # sessions, invitations, and API tokens.
    app.include_router(auth_router, prefix="/api/auth", tags=["auth"])

    # Session-facing and concern-specific prefixes mount the same router objects,
    # preserving the frontend contract without a forwarding shim.
    app.include_router(
        persistence_open.router, prefix="/api/sessions", tags=["sessions"]
    )
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

    # Registry and config prefixes mount the catalog routers directly so existing
    # frontend URLs and concern-specific URLs share one implementation.
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

    # The non-empty Studio prefix permits composite routers to expose empty-path
    # index routes under FastAPI's prefix/path validation rules.
    app.include_router(build_studio_router(), prefix="/api/studio")

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

    # Mount the concern-specific API surfaces under their canonical prefixes.
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
    """Mount catalog, identity, session, persistence, and attach routers."""
    # Catalog — read-only discovery
    app.include_router(
        catalog_packages.router, prefix="/api/catalog/packages", tags=["catalog"]
    )
    app.include_router(
        catalog_registry.router, prefix="/api/catalog/registry", tags=["catalog"]
    )
    # Marketplace — TerrariumMarket-backed browse + install + source mgmt.
    # Separate from /api/catalog/registry (which is the legacy bundled
    # static index) — this one hits live remote sources via
    # ``packages/marketplace.py``.
    app.include_router(
        catalog_marketplace.router,
        prefix="/api/catalog/marketplace",
        tags=["catalog"],
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

    # Identity configuration remains under ``/api/settings`` to match the
    # frontend settings contract.
    app.include_router(identity_llm.router, prefix="/api/settings", tags=["identity"])
    app.include_router(
        identity_api_keys.router, prefix="/api/settings", tags=["identity"]
    )
    app.include_router(identity_codex.router, prefix="/api/settings", tags=["identity"])
    app.include_router(identity_grok.router, prefix="/api/settings", tags=["identity"])
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

    # Engine-backed creature operations share the
    # ``/api/sessions/{sid}/creatures/{cid}/...`` URL hierarchy.
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
        sessions_creatures_subagents.router, prefix="/api/sessions", tags=["sessions"]
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
        sessions_drives.router, prefix="/api/sessions", tags=["sessions"]
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
    app.include_router(
        persistence_drives.router,
        prefix="/api/persistence/viewer",
        tags=["persistence"],
    )

    # Workspace file operations retain the frontend's ``/api/files`` namespace.
    app.include_router(
        catalog_attach_files.router, prefix="/api/files", tags=["attach"]
    )

    # Attach — policy hints (informational; consumed by the macro shell's
    # Inspector Overview "IO bindings" line — never used to gate UI).
    app.include_router(attach_policies.router, prefix="/api/attach", tags=["attach"])


def _mount_spa(app: FastAPI, static_dir: Path) -> None:
    """Mount static assets and an SPA fallback after API and WebSocket routes.

    The root path avoids filesystem discovery, obvious traversal paths are rejected
    before resolution, and genuine asset checks run on the dedicated I/O pool so
    filesystem latency cannot stall the event loop.
    """
    # Serve hashed build assets (JS, CSS, images)
    assets_dir = static_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    index_html = static_dir / "index.html"
    static_dir_resolved = static_dir.resolve()

    def _resolve_static_path(full_path: str) -> Path | None:
        """Return an existing file only when it resolves beneath ``static_dir``."""
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
