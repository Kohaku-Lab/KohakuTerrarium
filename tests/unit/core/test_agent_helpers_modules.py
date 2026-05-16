"""Unit tests for small ``agent_*`` helper modules:
:mod:`agent_helpers`, :mod:`agent_observability`, :mod:`agent_tools_metrics`.
"""

import types


from kohakuterrarium.core import agent_observability as obs
from kohakuterrarium.core import agent_tools_metrics as tm
from kohakuterrarium.core.agent_helpers import attach_session_helpers
from kohakuterrarium.core.agent_native_tools import NativeToolOptions
from kohakuterrarium.core.agent_observability import (
    _TOKEN_KEYS,
    build_session_info,
    init_branch_state,
    wire_plugin_hook_timing,
    wire_scratchpad_observer,
)
from kohakuterrarium.core.agent_plugin_options import PluginOptions
from kohakuterrarium.core.agent_tools_metrics import emit_completion_metrics
from kohakuterrarium.core.agent_workspace import WorkspaceController


def _fake_agent(**kw):
    """Tiny duck-typed agent stand-in."""
    return types.SimpleNamespace(**kw)


# ── agent_helpers.attach_session_helpers ─────────────────────────


class TestAttachSessionHelpers:
    def test_attaches_three_helpers(self):
        agent = _fake_agent(config=types.SimpleNamespace(name="a"))
        attach_session_helpers(agent)
        assert isinstance(agent.native_tool_options, NativeToolOptions)
        assert isinstance(agent.plugin_options, PluginOptions)
        assert isinstance(agent.workspace, WorkspaceController)


# ── agent_observability.init_branch_state ────────────────────────


class TestInitBranchState:
    def test_sets_all_fields(self):
        a = _fake_agent()
        init_branch_state(a)
        assert a._wiring_resolver is None
        assert a._turn_index == 0
        assert a._branch_id == 0
        assert a._parent_branch_path == []
        assert a._last_turn_text == []
        assert a._turn_usage_accum == {k: 0 for k in _TOKEN_KEYS}

    def test_accum_keys_independent_per_call(self):
        a = _fake_agent()
        b = _fake_agent()
        init_branch_state(a)
        init_branch_state(b)
        a._turn_usage_accum["prompt_tokens"] = 42
        assert b._turn_usage_accum["prompt_tokens"] == 0


# ── wire_scratchpad_observer ─────────────────────────────────────


class _Pad:
    def __init__(self):
        self.observer = None

    def set_write_observer(self, fn):
        self.observer = fn


class _Router:
    def __init__(self):
        self.calls: list[tuple] = []

    def notify_activity(self, kind, msg, metadata=None):
        self.calls.append((kind, msg, metadata))


class TestWireScratchpadObserver:
    def test_no_session_no_op(self):
        a = _fake_agent(session=None)
        wire_scratchpad_observer(a)  # no error, no observer set

    def test_no_scratchpad_no_op(self):
        a = _fake_agent(session=types.SimpleNamespace())
        wire_scratchpad_observer(a)

    def test_no_router_no_op(self):
        pad = _Pad()
        a = _fake_agent(
            session=types.SimpleNamespace(scratchpad=pad),
            output_router=None,
            config=types.SimpleNamespace(name="x"),
        )
        wire_scratchpad_observer(a)
        assert pad.observer is None

    def test_observer_emits_activity(self):
        pad = _Pad()
        router = _Router()
        a = _fake_agent(
            session=types.SimpleNamespace(scratchpad=pad),
            output_router=router,
            config=types.SimpleNamespace(name="alice"),
        )
        wire_scratchpad_observer(a)
        assert pad.observer is not None
        pad.observer("foo", "set", 123)
        kind, msg, meta = router.calls[0]
        assert kind == "scratchpad_write"
        assert "alice" in msg
        assert meta["agent"] == "alice"
        assert meta["key"] == "foo"
        assert meta["size_bytes"] == 123


# ── wire_plugin_hook_timing ──────────────────────────────────────


class _Plugins:
    def __init__(self):
        self.cb = None

    def set_hook_timing_callback(self, fn):
        self.cb = fn


class TestWirePluginHookTiming:
    def test_no_plugins_no_op(self):
        a = _fake_agent(plugins=None)
        wire_plugin_hook_timing(a)

    def test_no_router_no_op(self):
        a = _fake_agent(plugins=_Plugins(), output_router=None)
        wire_plugin_hook_timing(a)
        assert a.plugins.cb is None

    def test_callback_wires_router_and_metrics(self, monkeypatch):
        plugins = _Plugins()
        router = _Router()
        a = _fake_agent(plugins=plugins, output_router=router)

        captured = []
        monkeypatch.setattr(
            obs.metrics,
            "observe_plugin_hook",
            lambda *args, **kw: captured.append(("hook", args)),
        )
        monkeypatch.setattr(
            obs.metrics,
            "observe_error",
            lambda src, **kw: captured.append(("error", src)),
        )

        wire_plugin_hook_timing(a)
        assert plugins.cb is not None
        plugins.cb("pre_tool_execute", "budget", 5.0, False)
        assert router.calls[0][0] == "plugin_hook_timing"
        assert ("hook", ("budget", "pre_tool_execute", 5.0)) in captured
        # Not blocked → no error counter bump.
        assert ("error", "plugin") not in captured

    def test_blocked_increments_error(self, monkeypatch):
        plugins = _Plugins()
        router = _Router()
        a = _fake_agent(plugins=plugins, output_router=router)
        bumps = []
        monkeypatch.setattr(obs.metrics, "observe_plugin_hook", lambda *a, **k: None)
        monkeypatch.setattr(
            obs.metrics, "observe_error", lambda src, **k: bumps.append(src)
        )

        wire_plugin_hook_timing(a)
        plugins.cb("pre_tool_execute", "permgate", 0.0, True)
        assert bumps == ["plugin"]


# ── build_session_info ───────────────────────────────────────────


class TestBuildSessionInfo:
    def test_no_store_default_view(self):
        a = _fake_agent(
            config=types.SimpleNamespace(name="alice"),
            session_store=None,
        )
        info = build_session_info(a, tokens_view="own")
        assert info == {"agent": "alice", "tokens": {}}

    def test_no_store_all_loops_view(self):
        a = _fake_agent(
            config=types.SimpleNamespace(name="alice"),
            session_store=None,
        )
        info = build_session_info(a, tokens_view="all_loops")
        assert info["tokens"] == []

    def test_store_own_view(self):
        class _Store:
            def token_usage(self, name):
                return {"prompt_tokens": 10, "name": name}

            def token_usage_all_loops(self):
                return [{"prompt_tokens": 99}]

        a = _fake_agent(
            config=types.SimpleNamespace(name="alice"),
            session_store=_Store(),
        )
        info = build_session_info(a, tokens_view="own")
        assert info["tokens"] == {"prompt_tokens": 10, "name": "alice"}

    def test_store_all_loops_view(self):
        class _Store:
            def token_usage(self, name):
                return {}

            def token_usage_all_loops(self):
                return [{"prompt_tokens": 99}]

        a = _fake_agent(
            config=types.SimpleNamespace(name="alice"),
            session_store=_Store(),
        )
        info = build_session_info(a, tokens_view="all_loops")
        assert info["tokens"] == [{"prompt_tokens": 99}]


# ── agent_tools_metrics.emit_completion_metrics ──────────────────


class TestEmitCompletionMetrics:
    def test_tool_ok_no_error_bump(self, monkeypatch):
        seen = []
        monkeypatch.setattr(
            tm.metrics, "observe_tool", lambda *a, **k: seen.append(("tool", a))
        )
        monkeypatch.setattr(
            tm.metrics, "observe_error", lambda src, **k: seen.append(("err", src))
        )
        monkeypatch.setattr(
            tm.metrics, "observe_subagent", lambda *a, **k: seen.append(("sa", a))
        )
        emit_completion_metrics(False, "bash", "ok", 5.0)
        assert seen == [("tool", ("bash", "ok", 5.0))]

    def test_tool_error_bumps(self, monkeypatch):
        seen = []
        monkeypatch.setattr(tm.metrics, "observe_tool", lambda *a, **k: None)
        monkeypatch.setattr(
            tm.metrics, "observe_error", lambda src, **k: seen.append(src)
        )
        emit_completion_metrics(False, "bash", "error", 5.0)
        assert seen == ["tool"]

    def test_subagent_ok(self, monkeypatch):
        seen = []
        monkeypatch.setattr(
            tm.metrics, "observe_subagent", lambda *a, **k: seen.append(a)
        )
        monkeypatch.setattr(
            tm.metrics, "observe_error", lambda src, **k: seen.append(src)
        )
        emit_completion_metrics(True, "explore", "ok", 5.0)
        assert seen == [("explore", "ok", 5.0)]

    def test_subagent_error(self, monkeypatch):
        seen = []
        monkeypatch.setattr(tm.metrics, "observe_subagent", lambda *a, **k: None)
        monkeypatch.setattr(
            tm.metrics, "observe_error", lambda src, **k: seen.append(src)
        )
        emit_completion_metrics(True, "explore", "error", 5.0)
        assert seen == ["subagent"]

    def test_negative_duration_clamped_to_zero(self, monkeypatch):
        seen = []
        monkeypatch.setattr(tm.metrics, "observe_tool", lambda *a, **k: seen.append(a))
        monkeypatch.setattr(tm.metrics, "observe_error", lambda *a, **k: None)
        emit_completion_metrics(False, "t", "ok", -5.0)
        assert seen == [("t", "ok", 0.0)]
