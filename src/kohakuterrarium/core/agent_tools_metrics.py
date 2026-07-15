"""Bridge tool and sub-agent completion events to the metrics hook."""

from __future__ import annotations

from kohakuterrarium.core.metrics_hook import metrics


def emit_completion_metrics(
    is_subagent: bool, name: str, status: str, duration_ms: float
) -> None:
    """Record bounded completion metrics for a tool or sub-agent.

    Failures increment both the source error counter and the status-specific
    completion counter because they measure overall health and per-worker
    reliability, respectively.
    """
    if duration_ms < 0:
        duration_ms = 0.0
    if is_subagent:
        metrics.observe_subagent(name, status, duration_ms)
        if status != "ok":
            metrics.observe_error("subagent")
    else:
        metrics.observe_tool(name, status, duration_ms)
        if status != "ok":
            metrics.observe_error("tool")
