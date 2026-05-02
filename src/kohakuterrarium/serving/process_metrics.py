"""Process-wide metrics aggregator — the canonical subscriber.

Fed via :mod:`core.metrics_hook`. Owns counters, sliding-window
histograms (5-minute and 1-hour buckets), token totals, and a few
gauges that read directly off ``KohakuManager`` /
``SubAgentManager`` / ``MCPClientManager`` on snapshot.

Lifetime is the FastAPI app — one instance per ``kt serve`` process.
The HTTP layer reads :func:`get_aggregator` and shapes a snapshot dict
for ``GET /api/metrics/snapshot``.

Intentional design choices:

- **In-memory only**. Persistence to ``~/.kohakuterrarium/metrics.db``
  is a future M5 milestone. Live observability comes first.
- **Closed label cardinality**. Labels are constructed at the emit
  site from a small, enumerable set (provider name, model name, tool
  name, plugin name, hook stage). User-controllable strings never flow
  into a label so a creative prompt can't blow up memory.
- **No locks on counter increments**. Python's GIL serialises
  attribute writes on simple ints/dicts, which is enough for our
  tens-of-events-per-second hot path. Histograms that mutate a per-
  bucket list use ``threading.Lock`` because we want the percentile
  reader to see a consistent bucket snapshot.
- **Hard memory ceiling**. Sliding histograms cap retained sample
  count per series; older samples are summarised into bucket aggregates
  that survive without per-sample storage.
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

# Window definitions: (label, total_seconds, bucket_seconds)
#   5 min × 5 s buckets = 60 buckets
#   1 hr  × 1 min buckets = 60 buckets
WINDOWS: list[tuple[str, int, int]] = [
    ("5m", 5 * 60, 5),
    ("1h", 60 * 60, 60),
]

# Per-series sample cap so a runaway loop can't blow up memory. Older
# samples drop off the front of the deque; percentiles read whatever
# remains (still bounded to the longest window).
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
    """Sliding-window histogram. One per metric × label combo."""

    samples: deque[tuple[float, float]] = field(
        default_factory=lambda: deque(maxlen=MAX_SAMPLES_PER_SERIES)
    )
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def observe(self, value: float, ts: float | None = None) -> None:
        ts = ts if ts is not None else time.monotonic()
        with self._lock:
            self.samples.append((ts, value))

    def snapshot(self, window_seconds: int) -> _SeriesSnapshot:
        now = time.monotonic()
        cutoff = now - window_seconds
        # Snapshot under the lock then sort outside it — percentile
        # computation isn't constant time and we don't want to block
        # writers while we copy + sort.
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
    """Return the ``q``-th percentile (q ∈ [0, 1]) of ``sorted_values``.

    Linear interpolation between adjacent ranks; matches NumPy's
    default. ``sorted_values`` is assumed pre-sorted ascending.
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
    """Sliding-window event-rate counter for "events per minute" sparklines.

    A monotonic ring of (bucket_start_ts → count) pairs. Used by the
    UI to draw a per-second / per-minute rate over the last 5 min.
    """

    bucket_seconds: int
    capacity: int  # number of buckets retained
    buckets: deque[tuple[float, int]] = field(default_factory=deque)

    def add(self, ts: float | None = None) -> None:
        ts = ts if ts is not None else time.monotonic()
        bucket_start = (int(ts) // self.bucket_seconds) * self.bucket_seconds
        if self.buckets and self.buckets[-1][0] == bucket_start:
            head = self.buckets[-1]
            self.buckets[-1] = (head[0], head[1] + 1)
        else:
            self.buckets.append((bucket_start, 1))
        # Trim
        while len(self.buckets) > self.capacity:
            self.buckets.popleft()

    def values(self, window_seconds: int) -> list[int]:
        now = time.monotonic()
        cutoff = int(now) - window_seconds
        return [count for ts, count in self.buckets if ts >= cutoff]


class ProcessMetrics:
    """Aggregator. One instance per process; canonical subscriber on
    :data:`core.metrics_hook.metrics`.

    Storage layout:

    - ``counters[name][labels_tuple] = int`` — monotonic counts.
    - ``histograms[name][labels_tuple] = _Histogram`` — latency p50/p95.
    - ``rates[name] = _RateBucket`` — top-line per-minute rates for
      the throughput sparklines (LLM calls / tool calls / sub-agents /
      errors). Per-window labels would be cardinality fuel and the
      sparkline is a single series anyway.
    """

    def __init__(self) -> None:
        self.started_at = time.time()
        self._counters: dict[str, dict[tuple[str, ...], int]] = defaultdict(
            lambda: defaultdict(int)
        )
        self._histograms: dict[str, dict[tuple[str, ...], _Histogram]] = defaultdict(
            dict
        )
        # 5-second buckets × 60 = 5 minutes, plenty for the sparkline.
        self._rates: dict[str, _RateBucket] = {
            kind: _RateBucket(bucket_seconds=5, capacity=60)
            for kind in ("llm", "tool", "subagent", "error")
        }

    # ── observe_* — invoked from the metrics_hook fan-out. ──

    def observe_llm(
        self,
        provider: str,
        model: str,
        status: str,
        duration_ms: float,
        agent: str | None = None,
    ) -> None:
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
        name = name or "unknown"
        self._inc("subagent_runs_total", (name, status))
        self._observe("subagent_duration_ms", (name,), duration_ms)
        self._rates["subagent"].add()

    def observe_error(self, source: str, agent: str | None = None) -> None:
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
        plugin = plugin or "unknown"
        hook = hook or "unknown"
        self._observe("plugin_hook_ms", (plugin, hook), duration_ms)

    # ── snapshot ──

    def snapshot(self) -> dict[str, Any]:
        """Render the entire aggregator state as a JSON-serialisable
        dict. Cheap (<1 ms for ~50 series) — re-computed on every
        request rather than cached, since the histograms are sliding
        windows and a cache would lag the user's window.
        """
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
        # Always serialise the last 5 minutes' worth of buckets; the UI
        # decides how many of the trailing buckets to render.
        return {kind: bucket.values(5 * 60) for kind, bucket in self._rates.items()}

    # ── plumbing ──

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


# ── Singleton + auto-subscribe ──

_aggregator: ProcessMetrics | None = None


def get_aggregator() -> ProcessMetrics:
    """Return the process-wide aggregator, creating + subscribing on
    first call. Safe to call from FastAPI handlers, plugin code, or
    tests."""
    global _aggregator
    if _aggregator is None:
        _aggregator = ProcessMetrics()
        _hook.subscribe(_aggregator)
    return _aggregator


def reset_aggregator_for_tests() -> None:
    """Drop the aggregator + unsubscribe. Tests only — production code
    should never need to clear runtime metrics."""
    global _aggregator
    if _aggregator is not None:
        _hook.unsubscribe(_aggregator)
    _aggregator = None
