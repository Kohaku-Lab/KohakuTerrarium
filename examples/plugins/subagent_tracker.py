"""Sub-agent Tracker Plugin — monitor sub-agent lifecycle and performance.

Demonstrates:
  - pre_subagent_run: inspect/modify the task before sub-agent starts
  - post_subagent_run: observe results after sub-agent finishes
  - on_task_promoted: detect when a direct task becomes background
  - State persistence: track cumulative sub-agent stats

Usage in config.yaml:
    plugins:
      - name: subagent_tracker
        type: custom
        module: examples.plugins.subagent_tracker
        class: SubagentTrackerPlugin
        options:
          max_concurrent: 5    # Warn when active jobs exceed this threshold.
          log_tasks: true      # Retain a bounded task preview per active job.
"""

import time
from typing import Any

from kohakuterrarium.modules.plugin.base import BasePlugin, PluginContext
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class SubagentTrackerPlugin(BasePlugin):
    """Track active sub-agents, completion totals, and background promotion."""

    name = "subagent_tracker"
    priority = 10

    def __init__(self, options: dict[str, Any] | None = None):
        opts = options or {}
        self._max_concurrent = int(opts.get("max_concurrent", 5))
        self._log_tasks = bool(opts.get("log_tasks", True))
        self._active: dict[str, dict] = {}  # Job IDs correlate pre/post hooks.
        self._completed = 0
        self._total_tokens = 0
        self._ctx: PluginContext | None = None

    async def on_load(self, context: PluginContext) -> None:
        self._ctx = context

    async def pre_subagent_run(self, task: str, **kwargs) -> str | None:
        """Record a sub-agent start and warn on excessive concurrency."""
        name = kwargs.get("name", "unknown")
        job_id = kwargs.get("job_id", "")
        is_bg = kwargs.get("is_background", False)

        self._active[job_id] = {
            "name": name,
            "start": time.monotonic(),
            "task": task[:200] if self._log_tasks else "",
        }

        logger.info(
            "Sub-agent spawned",
            name=name,
            background=is_bg,
            active=len(self._active),
        )

        if len(self._active) > self._max_concurrent:
            logger.warning(
                "High sub-agent concurrency",
                active=len(self._active),
                max=self._max_concurrent,
            )

        return None  # Observation must not alter the delegated task.

    async def post_subagent_run(self, result: Any, **kwargs) -> Any | None:
        """Record sub-agent completion, duration, usage, and persistent totals."""
        job_id = kwargs.get("job_id", "")
        name = kwargs.get("name", "unknown")

        info = self._active.pop(job_id, None)
        elapsed = (time.monotonic() - info["start"]) if info else 0
        self._completed += 1

        tokens = getattr(result, "total_tokens", 0)
        self._total_tokens += tokens

        success = getattr(result, "success", True)
        turns = getattr(result, "turns", 0)

        logger.info(
            "Sub-agent finished",
            name=name,
            success=success,
            turns=turns,
            tokens=tokens,
            elapsed_s=f"{elapsed:.1f}",
        )

        if self._ctx:
            self._ctx.set_state("completed", self._completed)
            self._ctx.set_state("total_tokens", self._total_tokens)

        return None

    async def on_task_promoted(self, job_id: str, tool_name: str) -> None:
        """Log when a blocking task is promoted to background execution."""
        logger.info(
            "Task promoted to background",
            job_id=job_id,
            tool=tool_name,
        )

    async def on_agent_stop(self) -> None:
        """Report unfinished jobs and cumulative sub-agent usage at shutdown."""
        if self._active:
            logger.warning(
                "Sub-agents still active at shutdown",
                names=[v["name"] for v in self._active.values()],
            )
        logger.info(
            "Sub-agent summary",
            total_completed=self._completed,
            total_tokens=self._total_tokens,
        )
