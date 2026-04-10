"""Tool Timer Plugin — measure execution time of every tool call.

Demonstrates:
  - pre_tool_execute: capture start time before tool runs
  - post_tool_execute: compute elapsed time after tool finishes
  - PluginContext.get_state / set_state: persist cumulative stats

Usage in config.yaml:
    plugins:
      - name: tool_timer
        type: custom
        module: examples.plugins.tool_timer
        class: ToolTimerPlugin
"""

import time
from typing import Any

from kohakuterrarium.modules.plugin.base import BasePlugin, PluginContext
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class ToolTimerPlugin(BasePlugin):
    name = "tool_timer"
    priority = 5  # Low priority = runs first in pre, last in post (wraps everything)

    def __init__(self, options: dict[str, Any] | None = None):
        self._pending: dict[str, float] = {}  # job_id -> start time
        self._ctx: PluginContext | None = None

    async def on_load(self, context: PluginContext) -> None:
        self._ctx = context

    async def pre_tool_execute(self, args: dict, **kwargs) -> dict | None:
        """Record the start time before the tool executes.

        kwargs contains: tool_name (str), job_id (str)
        Return None to leave args unmodified.
        """
        job_id = kwargs.get("job_id", "")
        self._pending[job_id] = time.monotonic()
        return None  # Don't modify args

    async def post_tool_execute(self, result: Any, **kwargs) -> Any | None:
        """Log elapsed time after the tool finishes.

        kwargs contains: tool_name (str), job_id (str), args (dict)
        Return None to leave result unmodified.
        """
        job_id = kwargs.get("job_id", "")
        tool_name = kwargs.get("tool_name", "unknown")
        start = self._pending.pop(job_id, None)
        if start is None:
            return None

        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info(
            "Tool execution time",
            tool=tool_name,
            elapsed_ms=f"{elapsed_ms:.1f}",
            job_id=job_id,
        )

        # Persist cumulative stats in session state
        if self._ctx:
            total = float(self._ctx.get_state("total_ms") or 0)
            count = int(self._ctx.get_state("call_count") or 0)
            self._ctx.set_state("total_ms", total + elapsed_ms)
            self._ctx.set_state("call_count", count + 1)

        return None  # Don't modify result

    async def on_agent_stop(self) -> None:
        if not self._ctx:
            return
        total = float(self._ctx.get_state("total_ms") or 0)
        count = int(self._ctx.get_state("call_count") or 0)
        if count:
            logger.info(
                "Tool timing summary",
                total_calls=count,
                total_ms=f"{total:.0f}",
                avg_ms=f"{total / count:.1f}",
            )
