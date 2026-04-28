"""Composite router for the studio backend.

URL preservation: while the catalog read/write routes physically live
under ``api/routes/catalog/`` (and are mounted at ``/api/catalog/*``),
they are also mounted here at ``/api/studio/*`` so existing frontend
code (``frontend/src/utils/studio/*``) keeps working.

The remaining studio-only endpoints (``meta``, ``packages``) live under
``api/studio/routes/`` and are included as before.
"""

from fastapi import APIRouter

from kohakuterrarium.api.routes.catalog import builtins as catalog_builtins
from kohakuterrarium.api.routes.catalog import creatures as catalog_creatures
from kohakuterrarium.api.routes.catalog import manifest as catalog_manifest
from kohakuterrarium.api.routes.catalog import modules as catalog_modules
from kohakuterrarium.api.routes.catalog import schema as catalog_schema
from kohakuterrarium.api.routes.catalog import skills as catalog_skills
from kohakuterrarium.api.routes.catalog import templates as catalog_templates
from kohakuterrarium.api.routes.catalog import validate as catalog_validate
from kohakuterrarium.api.routes.catalog import workspace as catalog_workspace
from kohakuterrarium.api.studio.routes import meta, packages


def build_studio_router() -> APIRouter:
    """Build the composite router for studio endpoints.

    Each sub-router is included with its own ``/api/studio/<slug>``
    prefix and a tag that groups routes in the OpenAPI doc.
    """
    r = APIRouter()
    r.include_router(meta.router, prefix="/api/studio/meta", tags=["studio.meta"])
    r.include_router(
        catalog_workspace.router,
        prefix="/api/studio/workspace",
        tags=["studio.workspace"],
    )
    r.include_router(
        catalog_manifest.router,
        prefix="/api/studio/workspace/manifest",
        tags=["studio.manifest"],
    )
    r.include_router(
        catalog_creatures.router,
        prefix="/api/studio/creatures",
        tags=["studio.creatures"],
    )
    r.include_router(
        catalog_modules.router,
        prefix="/api/studio/modules",
        tags=["studio.modules"],
    )
    r.include_router(
        catalog_builtins.router,
        prefix="/api/studio/catalog",
        tags=["studio.catalog"],
    )
    r.include_router(
        packages.router, prefix="/api/studio/packages", tags=["studio.packages"]
    )
    r.include_router(
        catalog_templates.router,
        prefix="/api/studio/templates",
        tags=["studio.templates"],
    )
    r.include_router(
        catalog_validate.router,
        prefix="/api/studio/validate",
        tags=["studio.validate"],
    )
    r.include_router(
        catalog_schema.router,
        prefix="/api/studio/module_schema",
        tags=["studio.schema"],
    )
    r.include_router(
        catalog_skills.router,
        prefix="/api/studio/skills",
        tags=["studio.skills"],
    )
    return r
