"""Wave B — ``plugin_hook_timing`` additive event.

The plugin manager now emits a timing record after every hook
invocation. The callback plumbs through the agent's output router so
SessionOutput records it as a ``plugin_hook_timing`` event.
"""

import asyncio
from types import SimpleNamespace

import pytest

from kohakuterrarium.modules.plugin.base import BasePlugin, PluginBlockError
from kohakuterrarium.modules.plugin.manager import PluginManager
from kohakuterrarium.session.output import SessionOutput
from kohakuterrarium.session.store import SessionStore


class _NoopPlugin(BasePlugin):
    name = "noop"
    priority = 10

    async def on_event(self, event) -> None:  # pragma: no cover — noop
        await asyncio.sleep(0)


class _BlockingPlugin(BasePlugin):
    name = "blocker"
    priority = 10

    async def pre_llm_call(self, messages, **kwargs):
        raise PluginBlockError("nope")


@pytest.fixture
def store(tmp_path):
    s = SessionStore(tmp_path / "hook.kohakutr")
    s.init_meta(
        session_id="hook",
        config_type="agent",
        config_path="/tmp",
        pwd=str(tmp_path),
        agents=["agent"],
    )
    yield s
    s.close()


class TestHookTimingCallback:
    @pytest.mark.asyncio
    async def test_notify_fires_timing(self, store):
        mgr = PluginManager()
        mgr.register(_NoopPlugin())
        records: list[tuple[str, str, float, bool]] = []

        mgr.set_hook_timing_callback(
            lambda hook, plugin, ms, blocked: records.append(
                (hook, plugin, ms, blocked)
            )
        )
        await mgr.notify("on_event", event=object())

        assert len(records) == 1
        hook, plugin, ms, blocked = records[0]
        assert hook == "on_event"
        assert plugin == "noop"
        assert ms >= 0.0
        assert blocked is False

    @pytest.mark.asyncio
    async def test_pre_hook_block_is_reported(self, store):
        mgr = PluginManager()
        mgr.register(_BlockingPlugin())
        records: list[tuple[str, str, float, bool]] = []
        mgr.set_hook_timing_callback(
            lambda hook, plugin, ms, blocked: records.append(
                (hook, plugin, ms, blocked)
            )
        )

        with pytest.raises(PluginBlockError):
            await mgr.run_pre_hooks("pre_llm_call", [])

        assert records
        assert records[-1][0] == "pre_llm_call"
        assert records[-1][3] is True  # blocked=True


class TestSessionOutputPluginHookTiming:
    def test_activity_to_event_round_trip(self, store):
        agent_stub = SimpleNamespace(controller=None, session=None)
        output = SessionOutput("agent", store, agent_stub)

        output.on_activity_with_metadata(
            "plugin_hook_timing",
            "[noop] on_event 0.23ms",
            {
                "hook": "on_event",
                "plugin": "noop",
                "duration_ms": 0.23,
                "blocked": False,
            },
        )

        evts = [
            e for e in store.get_events("agent") if e["type"] == "plugin_hook_timing"
        ]
        assert len(evts) == 1
        assert evts[0]["hook"] == "on_event"
        assert evts[0]["plugin"] == "noop"
        assert evts[0]["blocked"] is False
        assert evts[0]["duration_ms"] == pytest.approx(0.23)
