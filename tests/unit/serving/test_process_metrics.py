"""Unit tests for :mod:`kohakuterrarium.serving.process_metrics`."""

import pytest

from kohakuterrarium.serving.process_metrics import (
    MAX_SAMPLES_PER_SERIES,
    WINDOWS,
    ProcessMetrics,
    _Histogram,
    _RateBucket,
    _SeriesSnapshot,
    _percentile,
    _series_to_dict,
    get_aggregator,
    reset_aggregator_for_tests,
)


@pytest.fixture(autouse=True)
def _reset():
    """Reset the singleton between tests so state is isolated."""
    reset_aggregator_for_tests()
    yield
    reset_aggregator_for_tests()


# ── _percentile ─────────────────────────────────────────────────


class TestPercentile:
    def test_empty(self):
        assert _percentile([], 0.5) == 0.0

    def test_single_value(self):
        assert _percentile([5.0], 0.5) == 5.0

    def test_median_of_sorted_list(self):
        # [1,2,3,4,5] - p50 = 3.
        assert _percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.5) == 3.0

    def test_p95_interpolation(self):
        out = _percentile([0.0, 10.0, 20.0, 30.0, 40.0], 0.95)
        # pos = 0.95 * 4 = 3.8 → between 30 and 40.
        assert out == pytest.approx(38.0)


# ── _Histogram ──────────────────────────────────────────────────


class TestHistogram:
    def test_empty_snapshot(self):
        h = _Histogram()
        snap = h.snapshot(300)
        assert snap.n == 0
        assert snap.p50 == 0.0
        assert snap.avg == 0.0

    def test_observations_within_window(self):
        h = _Histogram()
        for v in (10.0, 20.0, 30.0):
            h.observe(v)
        snap = h.snapshot(300)
        assert snap.n == 3
        assert snap.avg == pytest.approx(20.0)

    def test_old_samples_filtered_by_window(self):
        h = _Histogram()
        # Observe with very old timestamps.
        h.observe(100.0, ts=0.0)
        h.observe(200.0, ts=0.0)
        # Window of 5 seconds — none of those should be visible "now".
        snap = h.snapshot(5)
        assert snap.n == 0


# ── _RateBucket ─────────────────────────────────────────────────


class TestRateBucket:
    def test_add_creates_bucket(self):
        rb = _RateBucket(bucket_seconds=10, capacity=5)
        rb.add(ts=100.0)
        assert len(rb.buckets) == 1

    def test_add_same_bucket_increments(self):
        rb = _RateBucket(bucket_seconds=10, capacity=5)
        rb.add(ts=100.0)
        rb.add(ts=105.0)  # same 10s bucket
        # One bucket entry with count=2.
        assert rb.buckets[-1][1] == 2

    def test_trims_to_capacity(self):
        rb = _RateBucket(bucket_seconds=10, capacity=2)
        for ts in (0.0, 20.0, 40.0, 60.0):
            rb.add(ts=ts)
        # Only last 2 buckets retained.
        assert len(rb.buckets) == 2


# ── ProcessMetrics ──────────────────────────────────────────────


class TestProcessMetricsObserveLLM:
    def test_records_counter_and_histogram(self):
        m = ProcessMetrics()
        m.observe_llm("openai", "gpt-4", "ok", 100.0)
        snap = m.snapshot()
        # The call is counted under a provider|model|status label.
        assert snap["counters"]["llm_calls_total"] == {"openai|gpt-4|ok": 1}
        # The 100ms latency lands in the provider|model histogram series.
        series = snap["histograms"]["llm_response_ms"]["openai|gpt-4"]["5m"]
        assert series["n"] == 1
        assert series["avg_ms"] == 100.0
        assert series["p50_ms"] == 100.0

    def test_unknown_labels_when_empty(self):
        m = ProcessMetrics()
        m.observe_llm("", "", "ok", 1.0)
        snap = m.snapshot()
        # Empty provider/model labels are normalised to "unknown"; the
        # status label is kept as-is.
        assert snap["counters"]["llm_calls_total"] == {"unknown|unknown|ok": 1}


class TestProcessMetricsObserveTokens:
    def test_prompt_only(self):
        m = ProcessMetrics()
        m.observe_tokens("o", "m", prompt=10)
        snap = m.snapshot()
        # Only the prompt counter is recorded, with the exact token count.
        assert snap["counters"]["tokens_total"] == {"o|m|prompt": 10}

    def test_all_token_types(self):
        m = ProcessMetrics()
        m.observe_tokens(
            "o", "m", prompt=10, completion=20, cache_read=5, cache_write=3
        )
        snap = m.snapshot()
        # Every token type gets its own provider|model|type counter with
        # the exact count.
        assert snap["counters"]["tokens_total"] == {
            "o|m|prompt": 10,
            "o|m|completion": 20,
            "o|m|cache_read": 5,
            "o|m|cache_write": 3,
        }

    def test_zero_values_skipped(self):
        m = ProcessMetrics()
        m.observe_tokens("o", "m", prompt=0, completion=0)
        snap = m.snapshot()
        # No entries because all zeros.
        assert snap["counters"].get("tokens_total", {}) == {}


class TestProcessMetricsObserveTool:
    def test_tool_call_records(self):
        m = ProcessMetrics()
        m.observe_tool("bash", "ok", 50.0)
        snap = m.snapshot()
        # Counted under a tool|status label; latency in the tool series.
        assert snap["counters"]["tool_calls_total"] == {"bash|ok": 1}
        series = snap["histograms"]["tool_exec_ms"]["bash"]["5m"]
        assert series["n"] == 1
        assert series["avg_ms"] == 50.0


class TestProcessMetricsObserveSubagent:
    def test_subagent_records(self):
        m = ProcessMetrics()
        m.observe_subagent("explore", "ok", 200.0)
        snap = m.snapshot()
        assert snap["counters"]["subagent_runs_total"] == {"explore|ok": 1}
        series = snap["histograms"]["subagent_duration_ms"]["explore"]["5m"]
        assert series["n"] == 1
        assert series["avg_ms"] == 200.0


class TestProcessMetricsObserveError:
    def test_error_records(self):
        m = ProcessMetrics()
        m.observe_error("controller")
        snap = m.snapshot()
        # The error is counted under its source label.
        assert snap["counters"]["errors_total"] == {"controller": 1}


class TestProcessMetricsObservePluginHook:
    def test_plugin_hook_records(self):
        m = ProcessMetrics()
        m.observe_plugin_hook("budget", "pre_tool_execute", 1.5)
        snap = m.snapshot()
        # Latency recorded under a plugin|hook series.
        series = snap["histograms"]["plugin_hook_ms"]["budget|pre_tool_execute"]["5m"]
        assert series["n"] == 1
        assert series["avg_ms"] == 1.5


class TestSnapshot:
    def test_includes_uptime(self):
        m = ProcessMetrics()
        snap = m.snapshot()
        assert "uptime_s" in snap
        assert snap["uptime_s"] >= 0

    def test_includes_rates(self):
        m = ProcessMetrics()
        m.observe_llm("o", "m", "ok", 1.0)
        snap = m.snapshot()
        rates = snap["rates"]
        # All four rate series are present; the one LLM call shows up in
        # the llm bucket series while the others stay empty.
        assert set(rates) == {"llm", "tool", "subagent", "error"}
        assert sum(rates["llm"]) == 1
        assert rates["tool"] == []
        assert rates["subagent"] == []
        assert rates["error"] == []


# ── _series_to_dict ─────────────────────────────────────────────


class TestSeriesToDict:
    def test_shape(self):
        s = _SeriesSnapshot(n=10, p50=1.234, p95=2.5, p99=3.1, avg=1.7)
        d = _series_to_dict(s)
        assert d == {
            "n": 10,
            "p50_ms": 1.23,
            "p95_ms": 2.5,
            "p99_ms": 3.1,
            "avg_ms": 1.7,
        }


# ── get_aggregator / reset ──────────────────────────────────────


class TestGetAggregator:
    def test_singleton(self):
        a = get_aggregator()
        b = get_aggregator()
        assert a is b

    def test_reset_drops_instance(self):
        a = get_aggregator()
        reset_aggregator_for_tests()
        b = get_aggregator()
        assert a is not b


# ── Module-level constants ──────────────────────────────────────


class TestConstants:
    def test_windows_shape(self):
        # Each entry: (label, total_seconds, bucket_seconds)
        for label, total, bucket in WINDOWS:
            assert isinstance(label, str)
            assert total > 0
            assert bucket > 0

    def test_max_samples_per_series(self):
        assert MAX_SAMPLES_PER_SERIES > 0
