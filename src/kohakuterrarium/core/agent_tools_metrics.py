"""Tool / sub-agent completion → metrics hook bridge.

Split from :mod:`core.agent_tools` to keep that file under the 600-LoC
soft cap. Only the completion + interrupted activity emitters call in
here; everything else stays inside the mixin.
"""

from __future__ import annotations

from kohakuterrarium.core.metrics_hook import metrics


def emit_completion_metrics(
    is_subagent: bool, name: str, status: str, duration_ms: float
) -> None:
    """Forward a tool / subagent terminal event into the metrics hook.

    Status normalisation (``ok`` / ``error`` / ``interrupted`` /
    ``cancelled``) keeps label cardinality bounded; the activity bus
    keeps richer detail for the inspector.

    Errors are double-counted by design: every failing tool / subagent
    bumps ``errors_total{source}`` AND ``tool_calls_total{status}``.
    The two counters answer different questions (is anything broken
    right now? vs which tool fails most often?).
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
