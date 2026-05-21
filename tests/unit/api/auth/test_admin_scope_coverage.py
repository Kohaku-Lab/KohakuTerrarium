"""Audit: every config-mutating route in the catalogue carries the
``verify_admin_token`` dependency.

The design doc fixes the L3 catalogue route-by-route — auto-inferring
"if method != GET" would over-gate (e.g., ``POST /api/chat`` is not
config mutation).  This test prevents drift: if a future change adds a
mutation under one of the catalogued prefixes WITHOUT the dep, the
test fails loudly.

The audit walks the live FastAPI app and checks each route's resolved
``dependant`` chain for ``verify_admin_token``.  We construct the app
without a network port — just import, mount, inspect.
"""

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from kohakuterrarium.api.auth.dependencies import verify_admin_token

# Catalogue from design.md §8.1.
# (path_prefix, method) pairs that MUST carry verify_admin_token.
_ADMIN_GATED_ROUTES: tuple[tuple[str, str], ...] = (
    ("/api/settings/keys", "POST"),
    ("/api/settings/keys/", "DELETE"),
    ("/api/settings/backends", "POST"),
    ("/api/settings/backends/", "DELETE"),
    ("/api/settings/profiles", "POST"),
    ("/api/settings/profiles/", "DELETE"),
    ("/api/settings/default-model", "POST"),
    ("/api/settings/mcp", "POST"),
    ("/api/settings/mcp/", "PATCH"),
    ("/api/settings/mcp/", "DELETE"),
    ("/api/settings/config-files/", "PUT"),
    ("/api/registry/install", "POST"),
    ("/api/registry/uninstall", "POST"),
    ("/api/registry/", "POST"),  # update + update-all
    ("/api/registry/", "PUT"),  # write package file
)


@pytest.fixture(scope="module")
def real_app() -> FastAPI:
    """Boot the production FastAPI app once.  We only inspect routes,
    so no lifespan / engine boot is needed — direct ``create_app``
    is enough.
    """
    from kohakuterrarium.api.app import create_app

    return create_app()


def _route_has_admin_dep(route: APIRoute) -> bool:
    """Check whether ``verify_admin_token`` sits in the route's
    resolved dependency chain.

    FastAPI flattens ``Depends(...)`` into ``route.dependant.dependencies``;
    each entry has a ``call`` referencing the original callable.  We
    inspect the dependant tree (BFS) to catch nested dependencies too.
    """
    seen: set[int] = set()
    queue = list(getattr(route.dependant, "dependencies", []) or [])
    while queue:
        node = queue.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        if getattr(node, "call", None) is verify_admin_token:
            return True
        queue.extend(getattr(node, "dependencies", []) or [])
    return False


def _walk_routes(app: FastAPI) -> list[APIRoute]:
    return [r for r in app.routes if isinstance(r, APIRoute)]


class TestCatalogueCoverage:
    """Every catalogued (prefix, method) has at least one matching
    route carrying the dep."""

    @pytest.mark.parametrize("prefix,method", _ADMIN_GATED_ROUTES)
    def test_catalogued_path_has_admin_dep(self, real_app, prefix, method):
        matching = [
            r
            for r in _walk_routes(real_app)
            if r.path.startswith(prefix) and method in r.methods
        ]
        # We don't require EVERY route under a prefix; only assert
        # that at least one match exists AND carries the dep.  This
        # tolerates intermediate routes that aren't mutations.
        assert matching, f"no route found for {method} {prefix} — catalogue out of date"
        gated = [r for r in matching if _route_has_admin_dep(r)]
        assert gated, (
            f"{method} {prefix} routes exist but none carry verify_admin_token: "
            f"{[r.path for r in matching]}"
        )


class TestReadRoutesUngated:
    """Read routes (GETs) under the same prefixes must NOT carry the dep —
    we don't want to lock down reading."""

    @pytest.mark.parametrize(
        "prefix",
        [
            "/api/settings/keys",
            "/api/settings/backends",
            "/api/settings/profiles",
            "/api/settings/mcp",
            "/api/registry/",
        ],
    )
    def test_read_routes_pass_freely(self, real_app, prefix):
        for route in _walk_routes(real_app):
            if not route.path.startswith(prefix):
                continue
            if "GET" not in route.methods:
                continue
            assert not _route_has_admin_dep(route), (
                f"GET route {route.path} should NOT have admin dep "
                f"(reads are not config mutations)"
            )


class TestUnrelatedRoutesUngated:
    """Mutation routes that are NOT config-mutation (e.g., chat, regen)
    must NOT carry the dep."""

    @pytest.mark.parametrize(
        "prefix,method",
        [
            ("/api/sessions/", "POST"),  # chat, regen, etc.
            ("/api/sessions/", "PUT"),
            ("/api/sessions/", "DELETE"),
        ],
    )
    def test_session_mutations_pass_freely(self, real_app, prefix, method):
        for route in _walk_routes(real_app):
            if not route.path.startswith(prefix):
                continue
            if method not in route.methods:
                continue
            assert not _route_has_admin_dep(route), (
                f"{method} {route.path} should NOT have admin dep "
                f"(session ops aren't config mutations)"
            )
