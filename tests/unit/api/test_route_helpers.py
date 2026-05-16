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


def _info(creature_id="cid", name="alice") -> CreatureInfo:
    return CreatureInfo(
        creature_id=creature_id,
        name=name,
        graph_id="g",
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
