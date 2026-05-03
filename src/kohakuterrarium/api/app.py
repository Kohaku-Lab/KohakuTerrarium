"""FastAPI application factory."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from kohakuterrarium.api.deps import get_engine
from kohakuterrarium.serving.process_metrics import get_aggregator

# Phase 0 stub routers — empty APIRouter()s pre-mounted so Phase 1
# agents only need to populate handlers, not edit ``app.py``. Each
# subpackage maps to a future Studio tier (catalog / identity /
# sessions / persistence / attach). The legacy single-file routes
# were removed in Phase 3; the studio layer is the only path now.
from kohakuterrarium.api.routes import metrics as metrics_route
from kohakuterrarium.api.routes.attach import files as catalog_attach_files
from kohakuterrarium.api.routes.attach import policies as attach_policies
from kohakuterrarium.api.routes.catalog import builtins as catalog_builtins
from kohakuterrarium.api.routes.catalog import commands as catalog_commands
from kohakuterrarium.api.routes.catalog import creatures as catalog_creatures
from kohakuterrarium.api.routes.catalog import creatures_scan as catalog_creatures_scan
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
from kohakuterrarium.api.routes.identity import llm as identity_llm
from kohakuterrarium.api.routes.identity import mcp as identity_mcp
from kohakuterrarium.api.routes.identity import settings as identity_settings
from kohakuterrarium.api.routes.identity import ui_prefs as identity_ui_prefs
from kohakuterrarium.api.routes.persistence import artifacts as persistence_artifacts
from kohakuterrarium.api.routes.persistence import fork as persistence_fork
from kohakuterrarium.api.routes.persistence import history as persistence_history
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
from kohakuterrarium.api.ws import files as ws_files
from kohakuterrarium.api.ws import io as ws_io
from kohakuterrarium.api.ws import logs as ws_logs
from kohakuterrarium.api.ws import observer as ws_observer
from kohakuterrarium.api.ws import pty as ws_pty
from kohakuterrarium.api.ws import runtime_graph as ws_runtime_graph
from kohakuterrarium.api.ws import trace as ws_trace


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown."""
    # Process-metrics: subscribe the aggregator at startup so we
    # capture events the very first turn produces, not from the first
    # snapshot poll.
    get_aggregator()
    yield
    # Shutdown: stop the engine (cleans up every active session).
    engine = get_engine()
    await engine.shutdown()


def create_app(
    creatures_dirs: list[str] | None = None,
    terrariums_dirs: list[str] | None = None,
    static_dir: Path | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        creatures_dirs: Directories to scan for creature configs.
        terrariums_dirs: Directories to scan for terrarium configs.
        static_dir: Path to built web frontend (web_dist/).
            When provided, serves the SPA at / with API at /api/*.
    """
    app = FastAPI(
        title="KohakuTerrarium API",
        description="HTTP API for managing agents and terrariums",
        version="1.3.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Configure config discovery directories on the new catalog scan routers.
    if creatures_dirs or terrariums_dirs:
        catalog_creatures_scan.set_creatures_dirs(creatures_dirs or [])
        catalog_terrariums_scan.set_terrariums_dirs(terrariums_dirs or [])

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
    app.include_router(ws_files.router, tags=["ws"])
    app.include_router(ws_io.router, tags=["ws"])
    app.include_router(ws_logs.router, tags=["ws"])
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
    """
    # Serve hashed build assets (JS, CSS, images)
    assets_dir = static_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    index_html = static_dir / "index.html"

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        # Serve actual files (favicon.ico, robots.txt, etc.)
        file_path = static_dir / full_path
        if (
            full_path
            and file_path.is_file()
            and file_path.resolve().is_relative_to(static_dir.resolve())
        ):
            return FileResponse(str(file_path))
        # Everything else → index.html (Vue Router handles client-side routing)
        return FileResponse(str(index_html))
