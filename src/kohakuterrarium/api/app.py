"""FastAPI application factory."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from kohakuterrarium.api.deps import get_manager
from kohakuterrarium.api.routes import (
    agents,
    channels,
    configs,
    creatures,
    files,
    registry,
    sessions,
    settings,
    terrariums,
)
from kohakuterrarium.api.ws import (
    agents as ws_agents,
    channels as ws_channels,
    chat as ws_chat,
    files as ws_files,
    logs as ws_logs,
    terminal as ws_terminal,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown."""
    yield
    # Shutdown: stop all running agents/terrariums
    manager = get_manager()
    await manager.shutdown()


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
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Configure config discovery directories
    if creatures_dirs or terrariums_dirs:
        configs.set_config_dirs(
            creatures=creatures_dirs or [],
            terrariums=terrariums_dirs or [],
        )

    # REST routes
    app.include_router(terrariums.router, prefix="/api/terrariums", tags=["terrariums"])
    app.include_router(
        creatures.router,
        prefix="/api/terrariums/{terrarium_id}/creatures",
        tags=["creatures"],
    )
    app.include_router(
        channels.router,
        prefix="/api/terrariums/{terrarium_id}/channels",
        tags=["channels"],
    )
    app.include_router(agents.router, prefix="/api/agents", tags=["agents"])
    app.include_router(configs.router, prefix="/api/configs", tags=["configs"])
    app.include_router(files.router, prefix="/api/files", tags=["files"])
    app.include_router(registry.router, prefix="/api/registry", tags=["registry"])
    app.include_router(sessions.router, prefix="/api/sessions", tags=["sessions"])
    app.include_router(settings.router, prefix="/api/settings", tags=["settings"])

    # WebSocket routes
    app.include_router(ws_channels.router, tags=["ws"])
    app.include_router(ws_agents.router, tags=["ws"])
    app.include_router(ws_chat.router, tags=["ws"])
    app.include_router(ws_files.router, tags=["ws"])
    app.include_router(ws_logs.router, tags=["ws"])
    app.include_router(ws_terminal.router, tags=["ws"])

    # Static file serving for built web frontend (SPA)
    if static_dir and static_dir.is_dir():
        _mount_spa(app, static_dir)

    return app


def _mount_spa(app: FastAPI, static_dir: Path) -> None:
    """Mount built Vue SPA with static assets and catch-all fallback.

    API and WebSocket routes are already registered above, so they take
    precedence. The catch-all only fires for unmatched paths.
    """
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

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
