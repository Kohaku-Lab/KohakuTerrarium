"""Composite router for the studio backend.

Catalog routes are also mounted under ``/api/studio/*`` to preserve the
frontend contract even though their implementation lives under
``api/routes/catalog/``. Studio-specific ``meta`` and ``packages`` routes remain
in this package.
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
    """Build the Studio router with relative prefixes for external mounting.

    The caller must mount the composite at ``/api/studio``. Keeping that prefix
    outside this router allows sub-routers with empty index paths to remain valid
    under FastAPI's inclusion rules.
    """
    r = APIRouter()
    r.include_router(meta.router, prefix="/meta", tags=["studio.meta"])
    r.include_router(
        catalog_workspace.router,
        prefix="/workspace",
        tags=["studio.workspace"],
    )
    r.include_router(
        catalog_manifest.router,
        prefix="/workspace/manifest",
        tags=["studio.manifest"],
    )
    r.include_router(
        catalog_creatures.router,
        prefix="/creatures",
        tags=["studio.creatures"],
    )
    r.include_router(
        catalog_modules.router,
        prefix="/modules",
        tags=["studio.modules"],
    )
    r.include_router(
        catalog_builtins.router,
        prefix="/catalog",
        tags=["studio.catalog"],
    )
    r.include_router(packages.router, prefix="/packages", tags=["studio.packages"])
    r.include_router(
        catalog_templates.router,
        prefix="/templates",
        tags=["studio.templates"],
    )
    r.include_router(
        catalog_validate.router,
        prefix="/validate",
        tags=["studio.validate"],
    )
    r.include_router(
        catalog_schema.router,
        prefix="/module_schema",
        tags=["studio.schema"],
    )
    r.include_router(
        catalog_skills.router,
        prefix="/skills",
        tags=["studio.skills"],
    )
    return r
