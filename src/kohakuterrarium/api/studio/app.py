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

    Sub-routers are included with a *relative* ``/<slug>`` prefix; the
    caller mounts this composite under ``/api/studio`` (see
    ``create_app``), so the public URLs are ``/api/studio/<slug>/...``.

    The ``/api/studio`` prefix must be applied at the *mount* point, not
    here: several sub-routers expose an index route with an empty path
    (``@router.get("")``), and FastAPI rejects a router that carries an
    empty-path route when it is included without a prefix. Mounting the
    composite under a non-empty prefix keeps those index routes legal.
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
