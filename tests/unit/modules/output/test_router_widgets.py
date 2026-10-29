"""Unit tests for :mod:`kohakuterrarium.modules.output.router_widgets`.

The renderer union over attached outputs, listeners that fire only on a real
change, and widget delivery with ``exclude`` and the fallback activity.
"""

from kohakuterrarium.modules.output.event import OutputEvent
from kohakuterrarium.modules.output.router import OutputRouter
from kohakuterrarium.modules.output.router_widgets import output_renderers
from kohakuterrarium.testing.output import OutputRecorder


class RendererOutput(OutputRecorder):
    def __init__(self, renderers):
        super().__init__()
        self.renderers = renderers
        self.widget_events = []

    def ui_renderers(self):
        return self.renderers

    async def on_widget(self, event):
        self.widget_events.append(event)


class TestRendererUnion:
    def test_union_takes_max_version_and_ignores_bad_entries(self):
        router = OutputRouter(RendererOutput({"kt.ui": 1, "kt.app": 1}))
        router.add_secondary(RendererOutput({"kt.ui": 2, 7: 1, "bad": "x"}))
        router.add_secondary(OutputRecorder())
        assert router.ui_renderers() == {"kt.ui": 2, "kt.app": 1}

    def test_outputs_without_the_method_report_nothing(self):
        assert output_renderers(OutputRecorder()) == {}
        assert output_renderers(RendererOutput(["not", "a", "map"])) == {}
        assert OutputRouter(OutputRecorder()).ui_renderers() == {}


class TestRenderersListener:
    def test_fires_on_add_remove_and_refresh_only_when_changed(self):
        router = OutputRouter(OutputRecorder())
        calls = []
        router.add_renderers_listener(lambda old, new: calls.append((old, new)))
        router.add_renderers_listener(calls.append)
        router.remove_renderers_listener(calls.append)

        web = RendererOutput({"kt.app": 1})
        router.add_secondary(web)
        assert calls == [({}, {"kt.app": 1})]

        router.add_secondary(RendererOutput({"kt.app": 1}))
        assert len(calls) == 1, "a second identical browser changes nothing"

        web.renderers = {"kt.app": 1, "kt.ui": 1}
        router.refresh_renderers()
        router.refresh_renderers()
        assert calls[-1] == ({"kt.app": 1}, {"kt.app": 1, "kt.ui": 1})
        assert len(calls) == 2

        router.remove_secondary(web)
        assert calls[-1] == ({"kt.app": 1, "kt.ui": 1}, {"kt.app": 1})

    def test_replacing_the_default_output_links_it_and_refreshes(self):
        router = OutputRouter(OutputRecorder())
        calls = []
        router.add_renderers_listener(lambda old, new: calls.append(new))
        terminal = RendererOutput({"kt.ui": 1})
        router.default_output = terminal
        assert terminal._router is router
        assert calls == [{"kt.ui": 1}]
        router.default_output = OutputRecorder()
        assert calls[-1] == {}

    def test_a_raising_listener_does_not_stop_the_others(self):
        router = OutputRouter(OutputRecorder())
        seen = []

        def boom(old, new):
            raise RuntimeError("listener bug")

        router.add_renderers_listener(boom)
        router.add_renderers_listener(lambda old, new: seen.append(new))
        router.add_secondary(RendererOutput({"kt.ui": 1}))
        assert seen == [{"kt.ui": 1}]


class TestEmitWidget:
    async def test_delivers_to_widget_outputs_except_excluded(self):
        a, b = RendererOutput({}), RendererOutput({})
        router = OutputRouter(a)
        router.add_secondary(b)
        event = OutputEvent(type="widget_state", id="w1", update_target="w1")
        await router.emit_widget(event, exclude=b)
        assert a.widget_events == [event] and b.widget_events == []

    async def test_router_emit_routes_widget_types(self):
        out = RendererOutput({})
        router = OutputRouter(out)
        for kind in ("widget", "widget_close", "widget_state", "widget_message"):
            await router.emit(OutputEvent(type=kind, id="w1", update_target="w1"))
        assert [e.type for e in out.widget_events] == [
            "widget",
            "widget_close",
            "widget_state",
            "widget_message",
        ]
        assert out.events == [], "widget events bypass the legacy emit path"

    async def test_plain_outputs_get_fallback_only_for_new_widgets(self):
        plain = OutputRecorder()
        router = OutputRouter(plain)
        await router.emit_widget(
            OutputEvent(type="widget", id="w1", payload={"fallback": "Chart"})
        )
        await router.emit_widget(
            OutputEvent(type="widget", id="w2", payload={"fallback": ""})
        )
        await router.emit_widget(
            OutputEvent(
                type="widget", id="w1", update_target="w1", payload={"fallback": "x"}
            )
        )
        await router.emit_widget(OutputEvent(type="widget_close", id="w1"))
        assert [(a.activity_type, a.detail) for a in plain.activities] == [
            ("widget", "Chart")
        ]
