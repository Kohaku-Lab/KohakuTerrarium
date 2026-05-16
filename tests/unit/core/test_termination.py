"""Unit tests for :mod:`kohakuterrarium.core.termination`.

The :class:`TerminationChecker` drives the agent's stop conditions
— max turns, max duration, idle timeout, keyword match, and
plugin-contributed checkers. The tests below use a frozen monotonic
clock so duration / idle assertions are deterministic.
"""

import pytest

from kohakuterrarium.core.termination import (
    TerminationChecker,
    TerminationConfig,
    TerminationContext,
    TerminationDecision,
)

# ── helpers ─────────────────────────────────────────────────────────


class _Clock:
    """Deterministic monotonic-clock stand-in."""

    def __init__(self, t0: float = 1000.0):
        self.t = t0

    def __call__(self) -> float:
        return self.t

    def tick(self, dt: float) -> None:
        self.t += dt


@pytest.fixture
def clock(monkeypatch):
    from kohakuterrarium.core import termination as t

    c = _Clock()
    monkeypatch.setattr(t.time, "monotonic", c)
    return c


class _StubManager:
    """Mimics :class:`PluginManager.collect_termination_checkers`."""

    def __init__(self, checkers=None, raises=False):
        self._checkers = checkers or []
        self._raises = raises

    def collect_termination_checkers(self):
        if self._raises:
            raise RuntimeError("boom")
        return list(self._checkers)


# ── TerminationDecision / Context ──────────────────────────────────


class TestTerminationDecision:
    def test_is_frozen(self):
        d = TerminationDecision(should_stop=True, reason="x")
        with pytest.raises(Exception):
            d.should_stop = False  # type: ignore[misc]

    def test_default_reason_is_empty(self):
        d = TerminationDecision(should_stop=False)
        assert d.reason == ""


class TestTerminationContext:
    def test_default_recent_tool_results_independent(self):
        a = TerminationContext(turn_count=0, elapsed=0.0, idle_time=0.0, last_output="")
        b = TerminationContext(turn_count=0, elapsed=0.0, idle_time=0.0, last_output="")
        a.recent_tool_results.append("x")
        # Per-instance default_factory list, not a shared list.
        assert b.recent_tool_results == []


# ── built-in conditions ────────────────────────────────────────────


class TestMaxTurns:
    def test_zero_disables(self, clock):
        c = TerminationChecker(TerminationConfig(max_turns=0))
        c.start()
        for _ in range(1000):
            c.record_turn()
        assert c.should_terminate() is False

    def test_fires_at_exact_count(self, clock):
        c = TerminationChecker(TerminationConfig(max_turns=3))
        c.start()
        c.record_turn()
        c.record_turn()
        assert c.should_terminate() is False
        c.record_turn()
        assert c.should_terminate() is True
        assert "Max turns reached (3)" in c.reason

    def test_terminated_is_sticky(self, clock):
        c = TerminationChecker(TerminationConfig(max_turns=1))
        c.start()
        c.record_turn()
        assert c.should_terminate() is True
        # ``_terminated`` short-circuits — same answer next call.
        assert c.should_terminate() is True


class TestMaxDuration:
    def test_zero_disables(self, clock):
        c = TerminationChecker(TerminationConfig(max_duration=0))
        c.start()
        clock.tick(86400)
        assert c.should_terminate() is False

    def test_fires_when_elapsed(self, clock):
        c = TerminationChecker(TerminationConfig(max_duration=5.0))
        c.start()
        clock.tick(4.0)
        assert c.should_terminate() is False
        clock.tick(1.0)  # elapsed = 5.0
        assert c.should_terminate() is True
        assert "Max duration reached" in c.reason


class TestIdleTimeout:
    def test_zero_disables(self, clock):
        c = TerminationChecker(TerminationConfig(idle_timeout=0))
        c.start()
        clock.tick(86400)
        assert c.should_terminate() is False

    def test_fires_after_quiet(self, clock):
        c = TerminationChecker(TerminationConfig(idle_timeout=3.0))
        c.start()
        clock.tick(2.0)
        assert c.should_terminate() is False
        clock.tick(1.0)  # idle = 3.0
        assert c.should_terminate() is True
        assert "Idle timeout" in c.reason

    def test_record_activity_resets(self, clock):
        c = TerminationChecker(TerminationConfig(idle_timeout=3.0))
        c.start()
        clock.tick(2.5)
        c.record_activity()
        clock.tick(2.5)  # idle = 2.5, not 5.0
        assert c.should_terminate() is False

    def test_record_turn_resets_idle(self, clock):
        c = TerminationChecker(TerminationConfig(idle_timeout=3.0))
        c.start()
        clock.tick(2.5)
        c.record_turn()
        clock.tick(2.5)
        assert c.should_terminate() is False


class TestKeywords:
    def test_no_match_no_stop(self, clock):
        c = TerminationChecker(TerminationConfig(keywords=["DONE"]))
        c.start()
        assert c.should_terminate(last_output="working") is False

    def test_substring_match(self, clock):
        c = TerminationChecker(TerminationConfig(keywords=["DONE"]))
        c.start()
        assert c.should_terminate(last_output="task DONE here") is True
        assert "Keyword detected: DONE" in c.reason

    def test_first_match_wins(self, clock):
        c = TerminationChecker(TerminationConfig(keywords=["A", "B"]))
        c.start()
        assert c.should_terminate(last_output="A and B") is True
        assert "Keyword detected: A" in c.reason

    def test_empty_output_no_match(self, clock):
        c = TerminationChecker(TerminationConfig(keywords=["DONE"]))
        c.start()
        assert c.should_terminate(last_output="") is False


# ── plugin-supplied checkers ───────────────────────────────────────


class TestPluginCheckers:
    def test_no_manager_no_effect(self, clock):
        c = TerminationChecker(TerminationConfig())
        c.start()
        assert c.should_terminate() is False

    def test_positive_vote_terminates(self, clock):
        c = TerminationChecker(TerminationConfig())

        def vote(_ctx):
            return TerminationDecision(should_stop=True, reason="cluster says stop")

        c.attach_plugins(_StubManager(checkers=[("plug", vote)]))
        c.start()
        assert c.should_terminate(last_output="anything") is True
        assert "cluster says stop" in c.reason

    def test_default_reason_when_plugin_omits(self, clock):
        c = TerminationChecker(TerminationConfig())

        def vote(_ctx):
            return TerminationDecision(should_stop=True)

        c.attach_plugins(_StubManager(checkers=[("plug", vote)]))
        c.start()
        assert c.should_terminate() is True
        assert c.reason == "Plugin vetoed continuation"

    def test_negative_vote_does_not_stop(self, clock):
        c = TerminationChecker(TerminationConfig())

        def vote(_ctx):
            return TerminationDecision(should_stop=False, reason="ok")

        c.attach_plugins(_StubManager(checkers=[("plug", vote)]))
        c.start()
        assert c.should_terminate() is False

    def test_none_decision_skipped(self, clock):
        c = TerminationChecker(TerminationConfig())

        def vote(_ctx):
            return None

        c.attach_plugins(_StubManager(checkers=[("plug", vote)]))
        c.start()
        assert c.should_terminate() is False

    def test_non_decision_return_logged_and_skipped(self, clock, caplog):
        c = TerminationChecker(TerminationConfig())

        def vote(_ctx):
            return "stop"  # bogus type

        c.attach_plugins(_StubManager(checkers=[("plug", vote)]))
        c.start()
        assert c.should_terminate() is False
        # _terminated remains False — bogus types do NOT cause stop.

    def test_exception_in_checker_skipped(self, clock):
        c = TerminationChecker(TerminationConfig())

        def boom(_ctx):
            raise RuntimeError("kaboom")

        c.attach_plugins(_StubManager(checkers=[("p", boom)]))
        c.start()
        # Exception swallowed — run continues.
        assert c.should_terminate() is False

    def test_first_positive_short_circuits(self, clock):
        c = TerminationChecker(TerminationConfig())
        calls = []

        def first(_ctx):
            calls.append("first")
            return TerminationDecision(should_stop=True, reason="r1")

        def second(_ctx):
            calls.append("second")
            return TerminationDecision(should_stop=True, reason="r2")

        c.attach_plugins(_StubManager(checkers=[("a", first), ("b", second)]))
        c.start()
        assert c.should_terminate() is True
        assert "r1" in c.reason
        # Second checker never queried.
        assert calls == ["first"]

    def test_ctx_carries_state(self, clock):
        c = TerminationChecker(TerminationConfig())
        seen = []

        def capture(ctx):
            seen.append(ctx)
            return None

        c.attach_plugins(_StubManager(checkers=[("p", capture)]))
        c.attach_scratchpad("SCR")  # stored verbatim
        c.record_tool_result({"tool": "bash"})
        c.start()
        clock.tick(1.5)
        c.record_turn()
        c.record_turn()
        clock.tick(0.5)
        # NB: ``start()`` resets recent_tool_results — record after start.
        c.record_tool_result({"tool": "read"})
        c.should_terminate(last_output="hi")

        ctx = seen[-1]
        assert ctx.turn_count == 2
        assert ctx.elapsed >= 2.0
        assert ctx.idle_time >= 0.0
        assert ctx.last_output == "hi"
        assert ctx.scratchpad == "SCR"
        # ``recent_tool_results`` is a copy, not the live list.
        assert ctx.recent_tool_results == [{"tool": "read"}]

    def test_recent_tool_results_kept_short(self, clock):
        c = TerminationChecker(TerminationConfig())
        seen = []

        def capture(ctx):
            seen.append(list(ctx.recent_tool_results))
            return None

        c.attach_plugins(_StubManager(checkers=[("p", capture)]))
        c.start()
        for i in range(50):
            c.record_tool_result(i)
        c.should_terminate()
        # Cap is 16 — keep the tail.
        assert len(seen[-1]) == 16
        assert seen[-1][0] == 34
        assert seen[-1][-1] == 49

    def test_collect_failure_is_defensive(self, clock):
        c = TerminationChecker(TerminationConfig())
        c.attach_plugins(_StubManager(raises=True))
        c.start()
        # collect_termination_checkers blew up; treated as "no checkers".
        assert c.should_terminate() is False


# ── builtins fire BEFORE plugins ───────────────────────────────────


class TestBuiltinsFirst:
    def test_max_turns_skips_plugin_check(self, clock):
        c = TerminationChecker(TerminationConfig(max_turns=1))
        plugin_called = []

        def vote(_ctx):
            plugin_called.append(True)
            return TerminationDecision(should_stop=True, reason="plugin")

        c.attach_plugins(_StubManager(checkers=[("p", vote)]))
        c.start()
        c.record_turn()
        assert c.should_terminate() is True
        assert "Max turns" in c.reason  # not "plugin"
        assert plugin_called == []


# ── force_terminate ────────────────────────────────────────────────


class TestForceTerminate:
    def test_force_skips_checks(self, clock):
        c = TerminationChecker(TerminationConfig())
        c.start()
        c.force_terminate("budget exhausted")
        assert c.should_terminate() is True
        assert c.reason == "budget exhausted"


# ── elapsed / turn_count / is_active properties ────────────────────


class TestProperties:
    def test_elapsed_before_start_zero(self):
        c = TerminationChecker(TerminationConfig())
        assert c.elapsed == 0.0

    def test_elapsed_grows(self, clock):
        c = TerminationChecker(TerminationConfig())
        c.start()
        clock.tick(2.5)
        assert c.elapsed == pytest.approx(2.5)

    def test_turn_count_property(self, clock):
        c = TerminationChecker(TerminationConfig())
        c.start()
        c.record_turn()
        c.record_turn()
        assert c.turn_count == 2

    def test_is_active_no_config_no_plugin(self):
        c = TerminationChecker(TerminationConfig())
        assert c.is_active is False

    def test_is_active_with_config(self):
        c = TerminationChecker(TerminationConfig(max_turns=1))
        assert c.is_active is True

    def test_is_active_with_plugin_checkers(self):
        c = TerminationChecker(TerminationConfig())
        c.attach_plugins(_StubManager(checkers=[("p", lambda _c: None)]))
        assert c.is_active is True

    def test_is_active_with_plugin_but_no_checkers(self):
        c = TerminationChecker(TerminationConfig())
        c.attach_plugins(_StubManager(checkers=[]))
        assert c.is_active is False

    def test_reason_empty_before_termination(self, clock):
        c = TerminationChecker(TerminationConfig(max_turns=10))
        c.start()
        assert c.reason == ""


# ── start() resets everything ──────────────────────────────────────


class TestStartResets(object):
    def test_start_clears_prior_state(self, clock):
        c = TerminationChecker(TerminationConfig(max_turns=2))
        c.start()
        c.record_turn()
        c.record_turn()
        assert c.should_terminate() is True
        # Re-start — fresh run.
        clock.tick(100)
        c.start()
        assert c.should_terminate() is False
        assert c.reason == ""
        assert c.turn_count == 0


class TestRunPluginCheckersDirect:
    def test_none_manager_returns_none(self):
        c = TerminationChecker(TerminationConfig())
        c._plugin_manager = None
        # Calling directly with no manager — early-return None branch.
        assert c._run_plugin_checkers("", 0.0) is None
