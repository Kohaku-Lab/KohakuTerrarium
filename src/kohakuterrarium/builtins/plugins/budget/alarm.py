"""Budget alarm plugin and prompt contribution."""

from typing import Any

from kohakuterrarium.core.budget import AlarmState, BudgetAxis, BudgetSet
from kohakuterrarium.modules.plugin.base import BasePlugin, PluginContext


class BudgetAlarmPlugin(BasePlugin):
    """Inject budget operating constraints and runtime alarm messages."""

    name = "budget.alarm"
    priority = 20

    def __init__(self) -> None:
        super().__init__()
        self._budgets: BudgetSet | None = None
        self._pending: list[tuple[str, AlarmState]] = []

    async def on_load(self, context: PluginContext) -> None:
        self._budgets = context.budgets

    def get_prompt_content(self, context: PluginContext) -> str | None:
        budgets = context.budgets
        if budgets is None:
            return None
        bullets = [
            _format_axis_bullet(axis)
            for axis in (budgets.turn, budgets.walltime, budgets.tool_call)
            if axis is not None and axis.hard > 0
        ]
        if not bullets:
            return None
        lines = [
            "## Operating Constraints",
            "",
            "You are running with a budget across these axes. The runtime tracks usage and injects alarms when you approach a limit.",
            "",
            *bullets,
            "",
            "How to behave inside a budget:",
            "- Plan so you can wrap up by the soft wall.",
            "- Prefer fewer, more-targeted tool calls.",
            "- Soft-wall alarm: stop exploring and prepare the final report.",
            "- Hard-wall alarm: tools will fail; reply with text only.",
            "- Output is consumed by another agent — be terse and structured.",
        ]
        return "\n".join(lines)

    async def pre_llm_call(
        self, messages: list[dict], **kwargs: Any
    ) -> list[dict] | None:
        if not self._pending:
            return None
        injected = [
            {
                "role": "user",
                "content": _format_alarm(axis_name, state, self._budgets),
            }
            for axis_name, state in self._pending
        ]
        self._pending.clear()
        return injected + list(messages)

    async def post_llm_call(
        self,
        messages: list[dict],
        response: str,
        usage: dict,
        **kwargs: Any,
    ) -> None:
        if self._budgets is not None:
            self._pending.extend(self._budgets.drain_alarms())
        return None


def _format_axis_bullet(axis: BudgetAxis) -> str:
    soft = f"soft {axis.soft:g}" if axis.soft > 0 else "no soft wall"
    return f"- `{axis.name}`: {soft}; hard {axis.hard:g}; crash {axis.hard * 1.5:g}."


def _format_alarm(axis_name: str, state: AlarmState, budgets: BudgetSet | None) -> str:
    snapshot = budgets.snapshot() if budgets is not None else {}
    axis = snapshot.get(axis_name, {})
    used = axis.get("used", "?")
    hard = axis.get("hard", "?")
    match state:
        case AlarmState.SOFT:
            instruction = "Soft wall reached. Stop exploration and start wrapping up."
        case AlarmState.HARD:
            instruction = (
                "Hard wall reached. Do not call tools; return final text only."
            )
        case AlarmState.CRASH:
            instruction = (
                "Crash limit reached. Terminate with the best concise answer now."
            )
        case _:
            instruction = "Budget state changed."
    return (
        f"[budget {state.value}] Axis `{axis_name}` is at {used}/{hard}. "
        f"{instruction}"
    )
