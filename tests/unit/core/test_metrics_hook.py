"""Unit tests for :mod:`kohakuterrarium.core.metrics_hook`."""

import pytest

from kohakuterrarium.core import metrics_hook as mh
from kohakuterrarium.core.metrics_hook import (
    MetricsHook,
    _set_singleton_for_tests,
)


class _Recorder:
    """Subscriber that captures every observe_* call."""

    def __init__(self):
        self.calls: list[tuple[str, tuple, dict]] = []

    def observe_llm(self, *a, **kw):
        self.calls.append(("observe_llm", a, kw))

    def observe_tokens(self, *a, **kw):
        self.calls.append(("observe_tokens", a, kw))

    def observe_tool(self, *a, **kw):
        self.calls.append(("observe_tool", a, kw))

    def observe_subagent(self, *a, **kw):
        self.calls.append(("observe_subagent", a, kw))

    def observe_error(self, *a, **kw):
        self.calls.append(("observe_error", a, kw))

    def observe_plugin_hook(self, *a, **kw):
        self.calls.append(("observe_plugin_hook", a, kw))


@pytest.fixture
def hook():
    return MetricsHook()


# ── subscribe / unsubscribe / reset ──────────────────────────────


class TestSubscriptionLifecycle:
    def test_subscribe_idempotent(self, hook):
        r = _Recorder()
        hook.subscribe(r)
        hook.subscribe(r)
        hook.observe_error("x")
        # Single delivery because subscribe is idempotent.
        assert len(r.calls) == 1

    def test_unsubscribe(self, hook):
        r = _Recorder()
        hook.subscribe(r)
        hook.unsubscribe(r)
        hook.observe_error("x")
        assert r.calls == []

    def test_unsubscribe_unknown_silent(self, hook):
        hook.unsubscribe(_Recorder())  # must not raise

    def test_reset_clears_all(self, hook):
        for _ in range(3):
            hook.subscribe(_Recorder())
        hook.reset()
        # No subscribers — fan-out silently produces no calls.
        hook.observe_error("x")


# ── fan-out ──────────────────────────────────────────────────────


class TestFanout:
    def test_observe_llm_args(self, hook):
        r = _Recorder()
        hook.subscribe(r)
        hook.observe_llm("openai", "gpt-4", "ok", 100.0, agent="a1")
        assert r.calls[0] == (
            "observe_llm",
            ("openai", "gpt-4", "ok", 100.0),
            {"agent": "a1"},
        )

    def test_observe_tokens_args(self, hook):
        r = _Recorder()
        hook.subscribe(r)
        hook.observe_tokens(
            "openai", "m", prompt=10, completion=20, cache_read=5, cache_write=2
        )
        assert r.calls[0][0] == "observe_tokens"
        # Positional: provider, model, prompt, completion, cache_read, cache_write
        assert r.calls[0][1] == ("openai", "m", 10, 20, 5, 2)

    def test_observe_tool(self, hook):
        r = _Recorder()
        hook.subscribe(r)
        hook.observe_tool("bash", "ok", 5.0)
        assert r.calls[0][1] == ("bash", "ok", 5.0)

    def test_observe_subagent(self, hook):
        r = _Recorder()
        hook.subscribe(r)
        hook.observe_subagent("explore", "ok", 10.0, agent="a")
        assert r.calls[0][0] == "observe_subagent"

    def test_observe_error(self, hook):
        r = _Recorder()
        hook.subscribe(r)
        hook.observe_error("controller", agent="a")
        assert r.calls[0] == ("observe_error", ("controller",), {"agent": "a"})

    def test_observe_plugin_hook(self, hook):
        r = _Recorder()
        hook.subscribe(r)
        hook.observe_plugin_hook("budget", "pre_tool_execute", 1.0)
        assert r.calls[0][0] == "observe_plugin_hook"


class TestPartialSubscriber:
    def test_missing_method_silently_skipped(self, hook):
        class Partial:
            def observe_llm(self, *a, **kw):
                self.seen = True

        p = Partial()
        hook.subscribe(p)
        # observe_tool isn't on the subscriber — fan-out must not crash.
        hook.observe_tool("bash", "ok", 1.0)
        # observe_llm DOES exist — still delivered.
        hook.observe_llm("openai", "m", "ok", 1.0)
        assert getattr(p, "seen", False) is True


class TestMultipleSubscribers:
    def test_all_receive(self, hook):
        r1, r2, r3 = _Recorder(), _Recorder(), _Recorder()
        hook.subscribe(r1)
        hook.subscribe(r2)
        hook.subscribe(r3)
        hook.observe_error("source")
        assert len(r1.calls) == 1
        assert len(r2.calls) == 1
        assert len(r3.calls) == 1


# ── singleton ────────────────────────────────────────────────────


class TestSingletonSwap:
    def test_swap_and_restore(self):
        new = MetricsHook()
        old = _set_singleton_for_tests(new)
        try:
            assert mh.metrics is new
        finally:
            _set_singleton_for_tests(old)
        assert mh.metrics is old
