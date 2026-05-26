"""Unit tests for :mod:`kohakuterrarium.api.routes.sessions_v2._helpers`."""

import pytest
from fastapi import HTTPException

from kohakuterrarium.api.routes.sessions_v2._helpers import resolve_creature_id
from kohakuterrarium.terrarium.service import CreatureInfo


class _FakeService:
    def __init__(self, creatures=None, raise_exc=None):
        self._creatures = creatures or []
        self._raise = raise_exc

    async def list_creatures(self):
        if self._raise is not None:
            raise self._raise
        return self._creatures


def _info(creature_id="cid", name="alice", graph_id="g") -> CreatureInfo:
    return CreatureInfo(
        creature_id=creature_id,
        name=name,
        graph_id=graph_id,
        is_running=True,
        is_privileged=False,
        parent_creature_id=None,
        listen_channels=(),
        send_channels=(),
    )


class TestResolveCreatureId:
    async def test_exact_id_match(self):
        svc = _FakeService([_info("cid-1", "alice")])
        out = await resolve_creature_id(svc, "cid-1")
        assert out == "cid-1"

    async def test_name_fallback(self):
        svc = _FakeService([_info("cid-1", "alice")])
        out = await resolve_creature_id(svc, "alice")
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

    async def test_global_search_when_session_id_omitted(self):
        # Back-compat: callers that pre-date the v2 session-scoped
        # routes (tests + a few internal callers) pass ``session_id=None``
        # and get the historical global-name-fallback behaviour.
        svc = _FakeService(
            [
                _info("cid-a", "alice", graph_id="graph_aaa"),
                _info("cid-b", "alice", graph_id="graph_bbb"),
            ]
        )
        out = await resolve_creature_id(svc, "alice")
        # First match wins under global search (historical semantics).
        assert out == "cid-a"
