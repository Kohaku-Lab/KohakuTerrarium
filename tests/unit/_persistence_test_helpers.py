"""Helpers for tests that exercise the persistence HTTP routes.

The studio-cleanup refactor split the legacy ``api/routes/sessions.py``
into a tier of helpers under ``studio/persistence/`` and one router
per concern under ``api/routes/persistence/``. Every test that
previously did

    from kohakuterrarium.api.routes import sessions as sessions_routes
    monkeypatch.setattr(sessions_routes, "_SESSION_DIR", tmp_path)
    app.include_router(sessions_routes.router, prefix="/api/sessions")

now wires up the same surface through this helper.
"""

from fastapi import FastAPI

from kohakuterrarium.api.routes.persistence import artifacts as persistence_artifacts
from kohakuterrarium.api.routes.persistence import fork as persistence_fork
from kohakuterrarium.api.routes.persistence import history as persistence_history
from kohakuterrarium.api.routes.persistence import resume as persistence_resume
from kohakuterrarium.api.routes.persistence import saved as persistence_saved
from kohakuterrarium.api.routes.persistence import viewer as persistence_viewer
from kohakuterrarium.api.routes.sessions_v2 import memory as sessions_memory


def mount_session_routes(app: FastAPI, prefix: str = "/api/sessions") -> None:
    """Mount the full ``/api/sessions/*`` URL surface on a test app.

    Includes saved (list / delete), resume, fork, history, artifacts,
    viewer (tree / summary / turns / events / export / diff) and
    memory search routers — i.e. every endpoint the legacy
    ``sessions.router`` exposed before the studio-cleanup split.
    """
    app.include_router(persistence_saved.router, prefix=prefix)
    app.include_router(persistence_resume.router, prefix=prefix)
    app.include_router(persistence_fork.router, prefix=prefix)
    app.include_router(persistence_history.router, prefix=prefix)
    app.include_router(persistence_artifacts.router, prefix=prefix)
    app.include_router(persistence_viewer.router, prefix=prefix)
    app.include_router(sessions_memory.router, prefix=prefix)
