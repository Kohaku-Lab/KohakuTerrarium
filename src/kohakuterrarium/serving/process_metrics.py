"""Aggregate process-wide runtime metrics for serving snapshots.

The metrics hook feeds in-memory counters, bounded sliding histograms, token
totals, and throughput buckets for one serving process. Label values are kept
to controlled runtime dimensions, and histogram access is locked so percentile
snapshots remain consistent while writers continue recording samples.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from kohakuterrarium.core.metrics_hook import metrics as _hook
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Each window defines its label, duration, and bucket width.
WINDOWS: list[tuple[str, int, int]] = [
    ("5m", 5 * 60, 5),
    ("1h", 60 * 60, 60),
]

# Bounded deques prevent high-volume series from growing without limit.
MAX_SAMPLES_PER_SERIES = 4096


@dataclass
class _SeriesSnapshot:
    n: int
    p50: float
    p95: float
    p99: float
    avg: float


@dataclass
class _Histogram:
    """Store bounded timestamped samples for one metric-label series."""

    samples: deque[tuple[float, float]] = field(
        default_factory=lambda: deque(maxlen=MAX_SAMPLES_PER_SERIES)
    )
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def observe(self, value: float, ts: float | None = None) -> None:
        """Append a sample using the supplied or current monotonic timestamp."""
        ts = ts if ts is not None else time.monotonic()
        with self._lock:
            self.samples.append((ts, value))

    def snapshot(self, window_seconds: int) -> _SeriesSnapshot:
        """Summarize samples within the trailing window."""
        now = time.monotonic()
        cutoff = now - window_seconds
        # Copy under the lock, then sort outside it to minimize writer blocking.
        with self._lock:
            window_values = [v for ts, v in self.samples if ts >= cutoff]
        n = len(window_values)
        if n == 0:
            return _SeriesSnapshot(n=0, p50=0.0, p95=0.0, p99=0.0, avg=0.0)
        window_values.sort()
        return _SeriesSnapshot(
            n=n,
            p50=_percentile(window_values, 0.50),
            p95=_percentile(window_values, 0.95),
            p99=_percentile(window_values, 0.99),
            avg=sum(window_values) / n,
        )


def _percentile(sorted_values: list[float], q: float) -> float:
    """Return an interpolated percentile from ascending values.

    ``q`` must be between zero and one. Empty input returns zero.
    """
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    pos = q * (len(sorted_values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return float(sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac)


@dataclass
class _RateBucket:
    """Store bounded monotonic event counts for throughput sparklines."""

    bucket_seconds: int
    capacity: int  # Maximum number of time buckets retained.
    buckets: deque[tuple[float, int]] = field(default_factory=deque)

    def add(self, ts: float | None = None) -> None:
        """Increment the bucket containing the supplied or current timestamp."""
        ts = ts if ts is not None else time.monotonic()
        bucket_start = (int(ts) // self.bucket_seconds) * self.bucket_seconds
        if self.buckets and self.buckets[-1][0] == bucket_start:
            head = self.buckets[-1]
            self.buckets[-1] = (head[0], head[1] + 1)
        else:
            self.buckets.append((bucket_start, 1))
        while len(self.buckets) > self.capacity:
            self.buckets.popleft()

    def values(self, window_seconds: int) -> list[int]:
        """Return retained bucket counts within the trailing window."""
        now = time.monotonic()
        cutoff = int(now) - window_seconds
        return [count for ts, count in self.buckets if ts >= cutoff]


class ProcessMetrics:
    """Collect counters, latency distributions, and throughput rates.

    Metrics are keyed by controlled label tuples. Histograms retain bounded
    timestamped samples, while top-level event kinds use one rate series each.
    """

    def __init__(self) -> None:
        self.started_at = time.time()
        self._counters: dict[str, dict[tuple[str, ...], int]] = defaultdict(
            lambda: defaultdict(int)
        )
        self._histograms: dict[str, dict[tuple[str, ...], _Histogram]] = defaultdict(
            dict
        )
        # Five-second buckets retain five minutes of sparkline history.
        self._rates: dict[str, _RateBucket] = {
            kind: _RateBucket(bucket_seconds=5, capacity=60)
            for kind in ("llm", "tool", "subagent", "error")
        }

    def observe_llm(
        self,
        provider: str,
        model: str,
        status: str,
        duration_ms: float,
        agent: str | None = None,
    ) -> None:
        """Record one LLM call and its response duration."""
        provider = provider or "unknown"
        model = model or "unknown"
        self._inc("llm_calls_total", (provider, model, status))
        self._observe(
            "llm_response_ms",
            (provider, model),
            duration_ms,
        )
        self._rates["llm"].add()

    def observe_tokens(
        self,
        provider: str,
        model: str,
        prompt: int = 0,
        completion: int = 0,
        cache_read: int = 0,
        cache_write: int = 0,
        agent: str | None = None,
    ) -> None:
        """Add nonzero token usage totals by provider, model, and token kind."""
        provider = provider or "unknown"
        model = model or "unknown"
        if prompt:
            self._add("tokens_total", (provider, model, "prompt"), int(prompt))
        if completion:
            self._add("tokens_total", (provider, model, "completion"), int(completion))
        if cache_read:
            self._add("tokens_total", (provider, model, "cache_read"), int(cache_read))
        if cache_write:
            self._add(
                "tokens_total", (provider, model, "cache_write"), int(cache_write)
            )

    def observe_tool(
        self,
        tool: str,
        status: str,
        duration_ms: float,
        agent: str | None = None,
    ) -> None:
        """Record one tool call and its execution duration."""
        tool = tool or "unknown"
        self._inc("tool_calls_total", (tool, status))
        self._observe("tool_exec_ms", (tool,), duration_ms)
        self._rates["tool"].add()

    def observe_subagent(
        self,
        name: str,
        status: str,
        duration_ms: float,
        agent: str | None = None,
    ) -> None:
        """Record one sub-agent run and its duration."""
        name = name or "unknown"
        self._inc("subagent_runs_total", (name, status))
        self._observe("subagent_duration_ms", (name,), duration_ms)
        self._rates["subagent"].add()

    def observe_error(self, source: str, agent: str | None = None) -> None:
        """Record one runtime error by source."""
        source = source or "unknown"
        self._inc("errors_total", (source,))
        self._rates["error"].add()

    def observe_plugin_hook(
        self,
        plugin: str,
        hook: str,
        duration_ms: float,
        agent: str | None = None,
    ) -> None:
        """Record a plugin hook duration by plugin and hook name."""
        plugin = plugin or "unknown"
        hook = hook or "unknown"
        self._observe("plugin_hook_ms", (plugin, hook), duration_ms)

    def snapshot(self) -> dict[str, Any]:
        """Return a current JSON-serializable snapshot of all metric series."""
        return {
            "uptime_s": int(time.time() - self.started_at),
            "counters": self._snapshot_counters(),
            "histograms": self._snapshot_histograms(),
            "rates": self._snapshot_rates(),
        }

    def _snapshot_counters(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for name, label_map in self._counters.items():
            out[name] = {"|".join(labels): count for labels, count in label_map.items()}
        return out

    def _snapshot_histograms(self) -> dict[str, dict[str, dict[str, Any]]]:
        out: dict[str, dict[str, dict[str, Any]]] = {}
        for name, label_map in self._histograms.items():
            inner: dict[str, dict[str, Any]] = {}
            for labels, hist in label_map.items():
                key = "|".join(labels)
                inner[key] = {
                    win_label: _series_to_dict(hist.snapshot(win_seconds))
                    for win_label, win_seconds, _ in WINDOWS
                }
            out[name] = inner
        return out

    def _snapshot_rates(self) -> dict[str, list[int]]:
        # The UI may render any trailing subset of the retained five minutes.
        return {kind: bucket.values(5 * 60) for kind, bucket in self._rates.items()}

    def _inc(self, name: str, labels: tuple[str, ...]) -> None:
        self._counters[name][labels] = self._counters[name].get(labels, 0) + 1

    def _add(self, name: str, labels: tuple[str, ...], value: int) -> None:
        self._counters[name][labels] = self._counters[name].get(labels, 0) + value

    def _observe(self, name: str, labels: tuple[str, ...], duration_ms: float) -> None:
        bucket = self._histograms[name].get(labels)
        if bucket is None:
            bucket = _Histogram()
            self._histograms[name][labels] = bucket
        bucket.observe(duration_ms)


def _series_to_dict(s: _SeriesSnapshot) -> dict[str, Any]:
    return {
        "n": s.n,
        "p50_ms": round(s.p50, 2),
        "p95_ms": round(s.p95, 2),
        "p99_ms": round(s.p99, 2),
        "avg_ms": round(s.avg, 2),
    }


_aggregator: ProcessMetrics | None = None


def get_aggregator() -> ProcessMetrics:
    """Return the process aggregator, creating and subscribing it once."""
    global _aggregator
    if _aggregator is None:
        _aggregator = ProcessMetrics()
        _hook.subscribe(_aggregator)
    return _aggregator


def reset_aggregator_for_tests() -> None:
    """Unsubscribe and clear the process aggregator for test isolation."""
    global _aggregator
    if _aggregator is not None:
        _hook.unsubscribe(_aggregator)
    _aggregator = None
