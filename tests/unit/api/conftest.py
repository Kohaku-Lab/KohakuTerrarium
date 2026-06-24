"""Shared route-introspection helper for the API test suite.

FastAPI >= 0.116 changed ``include_router`` to attach lazy
``_IncludedRouter`` wrappers to ``app.routes`` instead of copying the
sub-routes in flat. Route-introspection tests therefore can no longer
iterate ``app.routes`` for ``APIRoute`` objects directly — the real
routes live inside the wrappers. This helper flattens BOTH shapes (the
old flat include and the new lazy wrapper) into ``(effective_path,
APIRoute)`` pairs so the audits keep working across FastAPI versions.

The app itself is unaffected: lazy includes still route requests and
still enforce each route's ``Depends(...)`` at request time — only
static introspection of ``app.routes`` needed updating.
"""

import pytest
from fastapi.routing import APIRoute

try:  # FastAPI >= 0.116 — lazy include wrappers
    from fastapi.routing import _IncludedRouter
except ImportError:  # older FastAPI — routes are already flat
    _IncludedRouter = None


def _iter_api_routes(app):
    """Return ``[(effective_path, APIRoute), ...]`` for every API route.

    Recurses through lazy ``_IncludedRouter`` wrappers, accumulating the
    include prefix so each ``APIRoute`` is paired with its full served
    path (e.g. ``/api/settings/keys``), exactly as a flat include used to
    expose it.
    """
    out: list[tuple[str, APIRoute]] = []

    def walk(routes, prefix: str) -> None:
        for r in routes:
            if _IncludedRouter is not None and isinstance(r, _IncludedRouter):
                ctx = r.include_context
                sub = getattr(r, "original_router", None) or ctx.included_router
                walk(sub.routes, prefix + (getattr(ctx, "prefix", "") or ""))
            elif isinstance(r, APIRoute):
                out.append((prefix + r.path, r))

    walk(app.routes, "")
    return out


@pytest.fixture
def iter_api_routes():
    """Callable fixture: ``iter_api_routes(app) -> [(path, APIRoute), ...]``."""
    return _iter_api_routes
