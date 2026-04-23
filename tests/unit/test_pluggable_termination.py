"""Tests for pluggable termination (cluster C.2).

Covers the new TerminationDecision vote shape, any-can-stop semantics,
and interaction with built-in conditions.
"""

from kohakuterrarium.core.termination import (
    TerminationChecker,
    TerminationConfig,
    TerminationContext,
    TerminationDecision,
)
from kohakuterrarium.modules.plugin.base import BasePlugin, PluginContext
from kohakuterrarium.modules.plugin.manager import PluginManager


class _TerminateWhenFlag(BasePlugin):
    name = "flag_term"

    def __init__(self, flag_name: str, reason: str = "flag set"):
        super().__init__()
        self._flag = flag_name
        self._reason = reason
        self._should_stop = False

    def set_flag(self, value: bool) -> None:
        self._should_stop = value

    def contribute_termination_check(self):
        def _check(ctx: TerminationContext):
            if self._should_stop:
                return TerminationDecision(True, self._reason)
            return None

        return _check


class _NeverTerminate(BasePlugin):
    name = "never"

    def contribute_termination_check(self):
        def _check(ctx: TerminationContext):
            return None

        return _check


def _make_checker_with_plugin(plugins: list[BasePlugin]) -> TerminationChecker:
    checker = TerminationChecker(TerminationConfig())
    manager = PluginManager()
    for p in plugins:
        manager.register(p)
    manager._load_context = PluginContext(agent_name="swe", model="m")
    checker.attach_plugins(manager)
    checker.start()
    return checker


# ── Tests ────────────────────────────────────────────────────────────


def test_plugin_vote_stops_run_with_reason():
    plug = _TerminateWhenFlag("goal_achieved", "goal achieved")
    plug.set_flag(True)
    checker = _make_checker_with_plugin([plug])
    assert checker.should_terminate() is True
    assert "goal achieved" in checker.reason


def test_plugin_vote_not_triggered_when_flag_off():
    plug = _TerminateWhenFlag("goal_achieved")
    # Flag defaults to False.
    checker = _make_checker_with_plugin([plug])
    assert checker.should_terminate() is False
    assert checker.reason == ""


def test_first_true_vote_wins_among_multiple():
    a = _TerminateWhenFlag("a", reason="plugin-a")
    b = _TerminateWhenFlag("b", reason="plugin-b")
    a.set_flag(True)
    b.set_flag(True)
    # Set their priorities so `a` is consulted first.
    a.priority = 10
    b.priority = 20
    checker = _make_checker_with_plugin([a, b])
    assert checker.should_terminate() is True
    # Either reason is acceptable as "first wins", but priority=10 runs
    # first in the PluginManager's sort.
    assert checker.reason == "plugin-a"


def test_no_plugin_votes_stop_keeps_run_alive():
    checker = _make_checker_with_plugin([_NeverTerminate()])
    assert checker.should_terminate() is False


def test_builtin_max_turns_still_fires():
    """When no plugin votes, built-in limits must still apply."""
    config = TerminationConfig(max_turns=1)
    checker = TerminationChecker(config)
    manager = PluginManager()
    manager.register(_NeverTerminate())
    manager._load_context = PluginContext(agent_name="a", model="m")
    checker.attach_plugins(manager)
    checker.start()
    checker.record_turn()  # counter hits 1
    assert checker.should_terminate() is True
    assert "Max turns" in checker.reason


def test_builtin_fires_before_plugin_vote():
    """If a built-in fires, plugin checkers are not even consulted."""
    called = []

    class Voter(BasePlugin):
        name = "voter"

        def contribute_termination_check(self):
            def _check(ctx):
                called.append(1)
                return TerminationDecision(True, "plugin vote")

            return _check

    config = TerminationConfig(max_turns=1)
    checker = TerminationChecker(config)
    manager = PluginManager()
    manager.register(Voter())
    manager._load_context = PluginContext(agent_name="a", model="m")
    checker.attach_plugins(manager)
    checker.start()
    checker.record_turn()
    assert checker.should_terminate() is True
    assert "Max turns" in checker.reason
    # Plugin checker was not consulted.
    assert called == []


def test_plugin_checker_receives_context_fields():
    """TerminationContext carries turn count and last_output."""
    captured: dict = {}

    class Inspector(BasePlugin):
        name = "inspector"

        def contribute_termination_check(self):
            def _check(ctx: TerminationContext):
                captured["turn_count"] = ctx.turn_count
                captured["last_output"] = ctx.last_output
                captured["elapsed_is_number"] = isinstance(ctx.elapsed, float)
                return None

            return _check

    checker = _make_checker_with_plugin([Inspector()])
    checker.record_turn()
    checker.record_turn()
    checker.should_terminate(last_output="hi there")
    assert captured["turn_count"] == 2
    assert captured["last_output"] == "hi there"
    assert captured["elapsed_is_number"] is True


def test_plugin_exception_does_not_crash():
    class Buggy(BasePlugin):
        name = "buggy"

        def contribute_termination_check(self):
            def _check(ctx):
                raise RuntimeError("oops")

            return _check

    checker = _make_checker_with_plugin([Buggy()])
    # Should not raise; should just not vote.
    assert checker.should_terminate() is False


def test_non_termination_decision_return_ignored():
    class BadReturn(BasePlugin):
        name = "bad"

        def contribute_termination_check(self):
            def _check(ctx):
                return "not a decision"

            return _check

    checker = _make_checker_with_plugin([BadReturn()])
    assert checker.should_terminate() is False
