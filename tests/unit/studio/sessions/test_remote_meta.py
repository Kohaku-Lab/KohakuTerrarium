"""Unit tests for :mod:`kohakuterrarium.studio.sessions.remote_meta`."""

from kohakuterrarium.studio.sessions.remote_meta import (
    copy_usage_meta,
    refresh_all_remote_creature_meta,
    refresh_remote_creature_meta,
)
from kohakuterrarium.terrarium.service.dto import CreatureInfo


def _info(**overrides) -> CreatureInfo:
    fields = dict(
        creature_id="w1",
        name="worker",
        graph_id="g1",
        is_running=True,
        is_privileged=False,
        parent_creature_id=None,
        listen_channels=(),
        send_channels=(),
        model="m",
        llm_name="p/m",
        token_usage={"prompt": 800, "completion": 80, "cached": 8, "total": 880},
        context_tokens=300,
    )
    fields.update(overrides)
    return CreatureInfo(**fields)


class _Service:
    def __init__(self, info: CreatureInfo) -> None:
        self._info = info

    async def get_creature_info(self, cid: str) -> CreatureInfo | None:
        return self._info if cid == self._info.creature_id else None

    async def list_creatures(self) -> list[CreatureInfo]:
        return [self._info]


def _registry() -> dict[str, dict]:
    return {"g1": {"on_node": "node-a", "creature_id": "w1", "name": "worker"}}


class TestCopyUsageMeta:
    def test_copies_running_usage(self):
        meta: dict = {}
        copy_usage_meta(meta, _info())
        assert meta["token_usage"]["total"] == 880
        assert meta["context_tokens"] == 300

    def test_empty_usage_keeps_cached_values(self):
        meta = {"token_usage": {"total": 5}, "context_tokens": 5}
        copy_usage_meta(meta, _info(token_usage={}, context_tokens=0))
        assert meta == {"token_usage": {"total": 5}, "context_tokens": 5}


class TestRefreshCopiesUsage:
    async def test_single_session_refresh(self):
        registry = _registry()
        await refresh_remote_creature_meta(registry, _Service(_info()), "g1")
        assert registry["g1"]["token_usage"]["prompt"] == 800
        assert registry["g1"]["context_tokens"] == 300

    async def test_fan_out_refresh(self):
        registry = _registry()
        await refresh_all_remote_creature_meta(registry, _Service(_info()))
        assert registry["g1"]["token_usage"]["completion"] == 80
        assert registry["g1"]["context_tokens"] == 300
