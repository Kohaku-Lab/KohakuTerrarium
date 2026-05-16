"""Unit tests for :mod:`kohakuterrarium.terrarium.creature` (CreatureHandle)."""

from pathlib import Path

from kohakuterrarium.terrarium.config import CreatureConfig
from kohakuterrarium.terrarium.creature import CreatureHandle
from kohakuterrarium.terrarium.output_log import OutputLogCapture


class _FakeAgent:
    def __init__(self, running=False):
        self.is_running = running


class _FakeOutput:
    async def start(self):
        pass

    async def stop(self):
        pass

    async def write(self, _):
        pass

    async def write_stream(self, _):
        pass

    async def flush(self):
        pass

    async def on_processing_start(self):
        pass

    async def on_processing_end(self):
        pass

    def on_activity(self, *_):
        pass


def _make_handle(*, with_log=True) -> CreatureHandle:
    cfg = CreatureConfig(name="alice", config_data={}, base_dir=Path("."))
    log = OutputLogCapture(_FakeOutput()) if with_log else None
    return CreatureHandle(
        name="alice",
        agent=_FakeAgent(),
        config=cfg,
        listen_channels=["a"],
        send_channels=["b"],
        output_log=log,
    )


class TestCreatureHandle:
    def test_basic_fields(self):
        h = _make_handle()
        assert h.name == "alice"
        assert h.listen_channels == ["a"]
        assert h.send_channels == ["b"]

    def test_is_running_from_agent(self):
        h = _make_handle()
        h.agent.is_running = False
        assert h.is_running is False
        h.agent.is_running = True
        assert h.is_running is True

    def test_get_log_entries_with_log(self):
        h = _make_handle(with_log=True)
        h.output_log.on_activity("test", "x")
        out = h.get_log_entries()
        # The recorded activity is surfaced through the handle.
        assert len(out) == 1
        assert out[0].entry_type == "activity"
        assert out[0].content == "x"
        assert out[0].metadata["activity_type"] == "test"

    def test_get_log_entries_without_log(self):
        h = _make_handle(with_log=False)
        assert h.get_log_entries() == []

    def test_get_log_text_with_log(self):
        h = _make_handle(with_log=True)
        import asyncio

        asyncio.run(h.output_log.write("hi"))
        assert "hi" in h.get_log_text()

    def test_get_log_text_without_log(self):
        h = _make_handle(with_log=False)
        assert h.get_log_text() == ""
