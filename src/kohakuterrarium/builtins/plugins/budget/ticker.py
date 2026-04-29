"""Budget ticker plugin."""

import time
from typing import Any

from kohakuterrarium.core.budget import BudgetSet
from kohakuterrarium.modules.plugin.base import BasePlugin, PluginContext


class BudgetTickerPlugin(BasePlugin):
    """Tick budget axes from LLM and tool activity."""

    name = "budget.ticker"
    priority = 10

    def __init__(self) -> None:
        super().__init__()
        self._budgets: BudgetSet | None = None
        self._turn_started_at: float | None = None

    async def on_load(self, context: PluginContext) -> None:
        self._budgets = context.budgets

    async def pre_llm_call(self, messages: list[dict], **kwargs: Any) -> None:
        self._turn_started_at = time.monotonic()
        return None

    async def post_llm_call(
        self,
        messages: list[dict],
        response: str,
        usage: dict,
        **kwargs: Any,
    ) -> None:
        if self._budgets is None:
            return None
        started = self._turn_started_at or time.monotonic()
        self._budgets.tick(turns=1, seconds=max(time.monotonic() - started, 0.0))
        return None

    async def post_tool_execute(self, result: Any, **kwargs: Any) -> None:
        if self._budgets is not None:
            self._budgets.tick(tool_calls=1)
        return None
