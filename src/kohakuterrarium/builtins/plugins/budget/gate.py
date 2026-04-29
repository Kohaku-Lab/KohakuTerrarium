"""Budget hard-wall gate plugin."""

from typing import Any

from kohakuterrarium.core.budget import BudgetSet
from kohakuterrarium.modules.plugin.base import (
    BasePlugin,
    PluginBlockError,
    PluginContext,
)


class BudgetGatePlugin(BasePlugin):
    """Disable tools and sub-agent dispatch after a hard wall."""

    name = "budget.gate"
    priority = 5

    def __init__(self) -> None:
        super().__init__()
        self._budgets: BudgetSet | None = None

    async def on_load(self, context: PluginContext) -> None:
        self._budgets = context.budgets

    async def pre_tool_execute(self, args: dict, **kwargs: Any) -> None:
        if self._budgets is not None and self._budgets.is_hard_walled():
            axis = self._budgets.exhausted_axis() or "unknown"
            raise PluginBlockError(
                f"Budget exhausted ({axis}). Tools are no longer available; "
                "return your final text answer."
            )
        return None

    async def pre_subagent_run(self, task: str, **kwargs: Any) -> str | None:
        if self._budgets is not None and self._budgets.is_hard_walled():
            raise PluginBlockError(
                "Budget exhausted; sub-agent dispatch disabled. "
                "Return your final text answer."
            )
        return task
