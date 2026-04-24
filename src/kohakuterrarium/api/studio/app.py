"""Composite router for the studio backend.

All studio REST endpoints live under ``/api/studio/*`` and all
websocket endpoints under ``/ws/studio/*``. ``build_studio_router``
returns a single ``APIRouter`` the core app includes via one line
in ``kohakuterrarium/api/app.py`` (touch point T1).
"""

from fastapi import APIRouter

from kohakuterrarium.api.studio.routes import (
    catalog,
    creatures,
    manifest,
    meta,
    modules,
    packages,
    schema,
    skills,
    templates,
    validate,
    workspace,
)


def build_studio_router() -> APIRouter:
    """Build the composite router for studio endpoints.

    Each sub-router is included with its own ``/api/studio/<slug>``
    prefix and a tag that groups routes in the OpenAPI doc.
    """
    r = APIRouter()
    r.include_router(meta.router, prefix="/api/studio/meta", tags=["studio.meta"])
    r.include_router(
        workspace.router, prefix="/api/studio/workspace", tags=["studio.workspace"]
    )
    r.include_router(
        manifest.router,
        prefix="/api/studio/workspace/manifest",
        tags=["studio.manifest"],
    )
    r.include_router(
        creatures.router, prefix="/api/studio/creatures", tags=["studio.creatures"]
    )
    r.include_router(
        modules.router, prefix="/api/studio/modules", tags=["studio.modules"]
    )
    r.include_router(
        catalog.router, prefix="/api/studio/catalog", tags=["studio.catalog"]
    )
    r.include_router(
        packages.router, prefix="/api/studio/packages", tags=["studio.packages"]
    )
    r.include_router(
        templates.router, prefix="/api/studio/templates", tags=["studio.templates"]
    )
    r.include_router(
        validate.router, prefix="/api/studio/validate", tags=["studio.validate"]
    )
    r.include_router(
        schema.router, prefix="/api/studio/module_schema", tags=["studio.schema"]
    )
    r.include_router(skills.router, prefix="/api/studio/skills", tags=["studio.skills"])
    return r
