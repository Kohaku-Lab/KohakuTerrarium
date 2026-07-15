"""Budget Enforcer Plugin — hard-stop when token budget is exhausted.

Demonstrates:
  - post_llm_call: track cumulative usage after each call
  - pre_llm_call: block the next call if budget exceeded (return empty)
  - State persistence: survive session resume via get_state/set_state
  - Options: configurable budget, warn threshold, per-model pricing

This is similar to kt-biome' cost_tracker but focuses on the
*enforcement* pattern — actually blocking calls, not just observing.

Usage in config.yaml:
    plugins:
      - name: budget_enforcer
        type: custom
        module: examples.plugins.budget_enforcer
        class: BudgetEnforcerPlugin
        options:
          max_tokens: 500000      # Count prompt and completion tokens together.
          warn_at_pct: 80         # Emit one warning before hard exhaustion.
"""

from typing import Any

from kohakuterrarium.modules.plugin.base import BasePlugin, PluginContext
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class BudgetEnforcerPlugin(BasePlugin):
    """Persist token usage and replace future calls after budget exhaustion."""

    name = "budget_enforcer"
    priority = 2  # Budget gating must precede other LLM-call transforms.

    def __init__(self, options: dict[str, Any] | None = None):
        opts = options or {}
        self._max_tokens = int(opts.get("max_tokens", 500_000))
        self._warn_pct = float(opts.get("warn_at_pct", 80)) / 100
        self._total_input = 0
        self._total_output = 0
        self._warned = False
        self._exhausted = False
        self._ctx: PluginContext | None = None

    @property
    def _total(self) -> int:
        return self._total_input + self._total_output

    async def on_load(self, context: PluginContext) -> None:
        self._ctx = context
        # Session state keeps cumulative usage intact after resume.
        saved_input = context.get_state("total_input")
        if saved_input is not None:
            self._total_input = int(saved_input)
            self._total_output = int(context.get_state("total_output") or 0)
        logger.info(
            "Budget enforcer loaded",
            budget=self._max_tokens,
            used=self._total,
        )

    async def pre_llm_call(self, messages: list[dict], **kwargs) -> list[dict] | None:
        """Block the LLM call if budget is exhausted.

        Instead of raising an error (which would crash the agent), we
        inject a system message telling the model its budget is gone.
        The model can then gracefully inform the user and stop.
        """
        if not self._exhausted:
            return None  # None preserves the original call.

        # Replacing messages permits a graceful user-facing stop instead of failure.
        logger.warning(
            "LLM call blocked — budget exhausted",
            total=self._total,
            max=self._max_tokens,
        )
        return [
            {"role": "system", "content": "Token budget exhausted."},
            {
                "role": "user",
                "content": (
                    "Your token budget has been exhausted. "
                    "Please inform the user and stop working."
                ),
            },
        ]

    async def post_llm_call(
        self, messages: list[dict], response: str, usage: dict, **kwargs
    ) -> None:
        """Accumulate usage, persist it, and update warning or stop state."""
        self._total_input += usage.get("prompt_tokens", 0)
        self._total_output += usage.get("completion_tokens", 0)

        if self._ctx:
            self._ctx.set_state("total_input", self._total_input)
            self._ctx.set_state("total_output", self._total_output)

        pct = self._total / self._max_tokens if self._max_tokens else 0

        # Warn once so long sessions do not repeat the same threshold message.
        if pct >= self._warn_pct and not self._warned:
            self._warned = True
            logger.warning(
                "Token budget warning",
                used=self._total,
                budget=self._max_tokens,
                pct=f"{pct:.0%}",
            )

        # Exhaustion affects subsequent calls because this call already completed.
        if pct >= 1.0 and not self._exhausted:
            self._exhausted = True
            logger.warning(
                "Token budget EXHAUSTED — future LLM calls will be blocked",
                used=self._total,
                budget=self._max_tokens,
            )

    async def on_agent_stop(self) -> None:
        logger.info(
            "Budget summary",
            input_tokens=self._total_input,
            output_tokens=self._total_output,
            total=self._total,
            budget=self._max_tokens,
            pct=f"{self._total / self._max_tokens:.0%}" if self._max_tokens else "N/A",
        )
