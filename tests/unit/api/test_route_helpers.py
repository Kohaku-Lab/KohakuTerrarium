"""Unit tests for :mod:`kohakuterrarium.api.routes.sessions_v2._helpers`."""

import pytest
from fastapi import HTTPException

from kohakuterrarium.api.routes.sessions_v2._helpers import resolve_creature_id
from kohakuterrarium.terrarium.service import CreatureInfo


class _FakeService:
    def __init__(self, creatures=None, raise_exc=None, cluster_links=None):
        self._creatures = creatures or []
        self._raise = raise_exc
        # ``cluster_groups`` reads this to fold worker-local graphs into
        # one cluster; absent/None means standalone (single-host).
        if cluster_links is not None:
            self._cluster_links = cluster_links

    async def list_creatures(self):
        if self._raise is not None:
            raise self._raise
        return self._creatures


def _info(
    creature_id="cid", name="alice", graph_id="g", config_name=""
) -> CreatureInfo:
    return CreatureInfo(
        creature_id=creature_id,
        name=name,
        graph_id=graph_id,
        is_running=True,
        is_privileged=False,
        parent_creature_id=None,
        listen_channels=(),
        send_channels=(),
        config_name=config_name,
    )


class TestResolveConnectTargetId:
    async def test_exact_runtime_id_may_cross_graph_but_name_may_not(self):
        from kohakuterrarium.api.routes.sessions_v2._helpers import (
            resolve_connect_target_id,
        )

        svc = _FakeService(
            creatures=[
                _info("source-id", "source", graph_id="g1"),
                _info("target-id", "worker", graph_id="g2"),
            ]
        )
        assert await resolve_connect_target_id(svc, "target-id", "g1") == "target-id"
        with pytest.raises(HTTPException) as exc:
            await resolve_connect_target_id(svc, "worker", "g1")
        assert exc.value.status_code == 404


class TestResolveCreatureId:
    async def test_exact_id_match(self):
        svc = _FakeService([_info("cid-1", "alice")])
        out = await resolve_creature_id(svc, "cid-1")
        assert out == "cid-1"

    async def test_name_fallback(self):
        svc = _FakeService([_info("cid-1", "alice")])
        out = await resolve_creature_id(svc, "alice")
        assert out == "cid-1"

    async def test_config_name_fallback(self):
        svc = _FakeService([_info("cid-1", "display", config_name="configured")])
        out = await resolve_creature_id(svc, "configured")
        assert out == "cid-1"

    async def test_id_wins_over_name(self):
        svc = _FakeService(
            [
                _info("first-id", "second-id"),
                _info("second-id", "alice"),
            ]
        )
        # ``second-id`` matches the second entry's creature_id directly.
        out = await resolve_creature_id(svc, "second-id")
        assert out == "second-id"

    async def test_not_found_404(self):
        svc = _FakeService([_info("cid-1", "alice")])
        with pytest.raises(HTTPException) as exc:
            await resolve_creature_id(svc, "nope")
        assert exc.value.status_code == 404

    async def test_service_error_503(self):
        svc = _FakeService(raise_exc=RuntimeError("link dead"))
        with pytest.raises(HTTPException) as exc:
            await resolve_creature_id(svc, "alice")
        assert exc.value.status_code == 503

    async def test_session_scoped_name_picks_matching_graph(self):
        # Regression: two running sessions of the SAME creature config
        # share the creature display ``name``.  Without a session_id
        # filter the name-fallback returns the FIRST creature globally
        # — which meant the second session's history endpoint
        # returned the first session's transcript.  With session_id
        # scoping, the lookup honours the URL session.
        svc = _FakeService(
            [
                _info("cid-old", "creative-art", graph_id="graph_d3575"),
                _info("cid-new", "creative-art", graph_id="graph_316cda"),
            ]
        )
        # Looking up by name in session_d3575 yields the older creature.
        assert (
            await resolve_creature_id(svc, "creative-art", "graph_d3575") == "cid-old"
        )
        # Looking up by name in session_316cda yields the newer creature.
        assert (
            await resolve_creature_id(svc, "creative-art", "graph_316cda") == "cid-new"
        )

    async def test_session_scoped_id_match_filters_by_graph(self):
        # A cross-session creature_id in the URL (stale handle, or URL
        # tampering) MUST 404 instead of returning the wrong-session's
        # creature.
        svc = _FakeService(
            [
                _info("cid-a", "alice", graph_id="graph_aaa"),
                _info("cid-b", "alice", graph_id="graph_bbb"),
            ]
        )
        # ``cid-a`` exists but not in ``graph_bbb`` — 404.
        with pytest.raises(HTTPException) as exc:
            await resolve_creature_id(svc, "cid-a", "graph_bbb")
        assert exc.value.status_code == 404
        # Same id WITH the right session resolves fine.
        assert await resolve_creature_id(svc, "cid-a", "graph_aaa") == "cid-a"

    async def test_global_duplicate_name_without_session_is_ambiguous(self):
        # Legacy callers without a session may still use exact IDs, but duplicate
        # names fail closed instead of selecting the first graph globally.
        svc = _FakeService(
            [
                _info("cid-a", "alice", graph_id="graph_aaa"),
                _info("cid-b", "alice", graph_id="graph_bbb"),
            ]
        )
        with pytest.raises(HTTPException) as exc:
            await resolve_creature_id(svc, "alice")
        assert exc.value.status_code == 409


class TestResolveCreatureIdClusterScope:
    """Bug #145: in a multi-node cluster the UI addresses a member
    creature via the cluster PRIMARY sid, but the creature physically
    lives on a peer worker under that worker's LOCAL graph_id.  The
    resolver must widen the session filter to the whole cluster so the
    member resolves; it must NOT leak creatures outside the cluster.
    """

    # Two worker graphs linked into one cluster; primary = lex-smallest.
    _LINKS = {frozenset({("w1", "graph_a"), ("w2", "graph_b")})}

    def _clustered_service(self, extra=None):
        creatures = [
            _info("cid-a", "alpha", graph_id="graph_a"),
            _info("cid-b", "bravo", graph_id="graph_b"),
        ]
        if extra:
            creatures.extend(extra)
        return _FakeService(creatures, cluster_links=self._LINKS)

    async def test_member_id_resolves_via_cluster_primary(self):
        # THE bug: bravo (graph_b, w2) addressed via the cluster primary
        # graph_a. Pre-fix this 404'd because graph_b != graph_a.
        svc = self._clustered_service()
        assert await resolve_creature_id(svc, "cid-b", "graph_a") == "cid-b"

    async def test_member_name_resolves_via_cluster_primary(self):
        svc = self._clustered_service()
        assert await resolve_creature_id(svc, "bravo", "graph_a") == "cid-b"

    async def test_member_still_resolves_via_own_graph(self):
        # The creature's own worker-local sid must keep working too.
        svc = self._clustered_service()
        assert await resolve_creature_id(svc, "cid-b", "graph_b") == "cid-b"

    async def test_primary_own_creature_still_resolves(self):
        svc = self._clustered_service()
        assert await resolve_creature_id(svc, "cid-a", "graph_a") == "cid-a"

    async def test_non_cluster_creature_stays_404_via_primary(self):
        # A creature on an UNLINKED graph must NOT leak into the cluster
        # scope — addressing it via the cluster primary still 404s.
        svc = self._clustered_service(
            extra=[_info("cid-c", "charlie", graph_id="graph_c")]
        )
        with pytest.raises(HTTPException) as exc:
            await resolve_creature_id(svc, "cid-c", "graph_a")
        assert exc.value.status_code == 404

    async def test_cross_cluster_member_stays_404(self):
        # CF-7: a member of a DIFFERENT cluster must not resolve through
        # this cluster's primary. graph_x/graph_y form a second cluster;
        # its member is invisible from graph_a's scope.
        links = {
            frozenset({("w1", "graph_a"), ("w2", "graph_b")}),
            frozenset({("w3", "graph_x"), ("w4", "graph_y")}),
        }
        svc = _FakeService(
            [
                _info("cid-a", "alpha", graph_id="graph_a"),
                _info("cid-b", "bravo", graph_id="graph_b"),
                _info("cid-y", "yankee", graph_id="graph_y"),
            ],
            cluster_links=links,
        )
        with pytest.raises(HTTPException) as exc:
            await resolve_creature_id(svc, "cid-y", "graph_a")
        assert exc.value.status_code == 404
        # But yankee resolves through its OWN cluster's primary graph_x.
        assert await resolve_creature_id(svc, "cid-y", "graph_x") == "cid-y"
