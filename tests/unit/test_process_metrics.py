"""Unit tests for ``serving.process_metrics`` + ``core.metrics_hook``.

Pin the contracts that matter at the integration layer:

- counters increment monotonically and partition by label tuple
- sliding histograms expose p50/p95/p99 over the configured windows
- subscribers never break the hot path: a buggy subscriber's
  exception must NOT propagate up through ``observe_*``
- snapshot dict keys/shape stay forward-compatible
"""

from __future__ import annotations


import pytest

from kohakuterrarium.core.metrics_hook import MetricsHook
from kohakuterrarium.serving.process_metrics import (
    ProcessMetrics,
    reset_aggregator_for_tests,
)


@pytest.fixture(autouse=True)
def _isolate_aggregator():
    """Drop the module-level singleton before AND after each test so
    the canonical aggregator never leaks state across cases."""
    reset_aggregator_for_tests()
    yield
    reset_aggregator_for_tests()


def test_counter_increments_and_partitions_by_label():
    pm = ProcessMetrics()
    pm.observe_tool("bash", "ok", 12.0)
    pm.observe_tool("bash", "ok", 8.0)
    pm.observe_tool("bash", "error", 4.0)
    pm.observe_tool("read", "ok", 1.0)

    snap = pm.snapshot()
    counters = snap["counters"]["tool_calls_total"]
    assert counters["bash|ok"] == 2
    assert counters["bash|error"] == 1
    assert counters["read|ok"] == 1


def test_histogram_percentiles_over_5min_window():
    pm = ProcessMetrics()
    for v in (10, 20, 30, 40, 50, 60, 70, 80, 90, 100):
        pm.observe_tool("bash", "ok", float(v))

    snap = pm.snapshot()
    bash = snap["histograms"]["tool_exec_ms"]["bash"]["5m"]
    assert bash["n"] == 10
    # Linear interpolation: p50 of 1..10*10 = 55, p95 ≈ 95.5
    assert 50 <= bash["p50_ms"] <= 60
    assert 90 <= bash["p95_ms"] <= 100
    assert 90 <= bash["p99_ms"] <= 100


def test_token_observe_skips_zero_kinds():
    pm = ProcessMetrics()
    pm.observe_tokens("openai", "gpt-5", prompt=100, completion=50)
    snap = pm.snapshot()
    counters = snap["counters"]["tokens_total"]
    assert counters["openai|gpt-5|prompt"] == 100
    assert counters["openai|gpt-5|completion"] == 50
    # cache_read / cache_write were 0 so no entry created
    assert "openai|gpt-5|cache_read" not in counters
    assert "openai|gpt-5|cache_write" not in counters


def test_llm_observe_pushes_rate_bucket():
    pm = ProcessMetrics()
    pm.observe_llm("openai", "gpt-5", "ok", 200.0)
    pm.observe_llm("openai", "gpt-5", "ok", 250.0)
    snap = pm.snapshot()
    rates = snap["rates"]["llm"]
    assert sum(rates) == 2


def test_error_observe_increments_per_source():
    pm = ProcessMetrics()
    pm.observe_error("controller")
    pm.observe_error("tool")
    pm.observe_error("controller")

    snap = pm.snapshot()
    errors = snap["counters"]["errors_total"]
    assert errors["controller"] == 2
    assert errors["tool"] == 1


def test_subscriber_exception_does_not_break_hot_path():
    """If a buggy subscriber raises, ``observe_*`` must still complete
    and the next subscriber must still get the event."""
    hook = MetricsHook()
    pm_good = ProcessMetrics()

    class _Buggy:
        def observe_tool(self, *a, **kw):
            raise RuntimeError("boom")

    hook.subscribe(_Buggy())
    hook.subscribe(pm_good)
    # No exception raised:
    hook.observe_tool("bash", "ok", 1.0)
    snap = pm_good.snapshot()
    assert snap["counters"]["tool_calls_total"]["bash|ok"] == 1


def test_snapshot_includes_uptime_field():
    pm = ProcessMetrics()
    snap = pm.snapshot()
    assert "uptime_s" in snap
    assert snap["uptime_s"] >= 0


def test_snapshot_shape_is_forward_compatible():
    """Frontend reads `counters`, `histograms`, `rates`, `gauges`,
    `uptime_s`. Pin the top-level keys so a refactor doesn't silently
    break the StatsTab."""
    pm = ProcessMetrics()
    pm.observe_llm("openai", "gpt-5", "ok", 100.0)
    snap = pm.snapshot()
    assert set(snap.keys()) >= {"counters", "histograms", "rates", "uptime_s"}
    # The route handler injects ``gauges`` from engine state; the
    # aggregator alone doesn't.
    # Histogram entries must expose 5m + 1h windows for every series.
    series = snap["histograms"]["llm_response_ms"]["openai|gpt-5"]
    assert "5m" in series and "1h" in series
    assert {"n", "p50_ms", "p95_ms", "p99_ms", "avg_ms"} <= set(series["5m"].keys())


def test_plugin_hook_observe_records_histogram():
    pm = ProcessMetrics()
    pm.observe_plugin_hook("budget", "pre_tool_execute", 0.4)
    pm.observe_plugin_hook("budget", "pre_tool_execute", 0.6)
    snap = pm.snapshot()
    series = snap["histograms"]["plugin_hook_ms"]["budget|pre_tool_execute"]["5m"]
    assert series["n"] == 2
    assert 0.4 <= series["p50_ms"] <= 0.6
