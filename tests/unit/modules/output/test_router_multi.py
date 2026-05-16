"""Unit tests for :mod:`kohakuterrarium.modules.output.router_multi`.

Behavior-first: MultiOutputRouter owns an extra named-output map,
write_to targets a specific module, and lifecycle cascades to every
owned module on top of the base router's cascade.
"""

from kohakuterrarium.modules.output.router_multi import MultiOutputRouter
from kohakuterrarium.testing.output import OutputRecorder


class TestWriteTo:
    async def test_write_to_named_module(self):
        a = OutputRecorder()
        b = OutputRecorder()
        router = MultiOutputRouter(OutputRecorder(), outputs={"a": a, "b": b})
        await router.write_to("a", "for a")
        await router.write_to("b", "for b")
        assert a.writes == ["for a"]
        assert b.writes == ["for b"]

    async def test_write_to_unknown_module_is_a_noop(self):
        # Unknown target → logged warning, no crash, nothing written.
        router = MultiOutputRouter(OutputRecorder(), outputs={})
        await router.write_to("ghost", "lost")  # must not raise


class TestLifecycleCascade:
    async def test_start_cascades_to_extra_outputs(self):
        extra = OutputRecorder()
        default = OutputRecorder()
        router = MultiOutputRouter(default, outputs={"x": extra})
        await router.start()
        assert default.is_running is True
        assert extra.is_running is True

    async def test_stop_cascades_to_extra_outputs(self):
        extra = OutputRecorder()
        default = OutputRecorder()
        router = MultiOutputRouter(default, outputs={"x": extra})
        await router.start()
        await router.stop()
        assert default.is_running is False
        assert extra.is_running is False

    async def test_flush_cascades_to_extra_outputs(self):
        extra = OutputRecorder()
        default = OutputRecorder()
        router = MultiOutputRouter(default, outputs={"x": extra})
        await router.flush()
        # Base router flushes default; subclass also flushes the extra map.
        assert default._flushed == 1
        assert extra._flushed == 1

    def test_outputs_default_to_empty_when_omitted(self):
        router = MultiOutputRouter(OutputRecorder())
        assert router.outputs == {}
