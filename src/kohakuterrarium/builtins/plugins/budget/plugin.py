"""Unified runtime budget plugin.

Combines ticker (consume axes), alarm (prompt + post-turn alarm
injection), and gate (block tools/sub-agents after a hard wall) into a
single plugin so the budget system is fully self-contained — no agent
or sub-agent core fields, no shared :class:`BudgetSet` on the host.

The plugin reads its axes from its own ``options``::

    plugins:
      - name: budget
        options:
          turn_budget: [40, 60]            # [soft, hard]
          walltime_budget: {soft: 0, hard: 600}
          tool_call_budget: [75, 100]

Any axis whose option is omitted is disabled.
"""

import time
from typing import Any

from kohakuterrarium.core.budget import AlarmState, BudgetAxis, BudgetSet
from kohakuterrarium.modules.plugin.base import (
    BasePlugin,
    PluginBlockError,
    PluginContext,
)


class BudgetPlugin(BasePlugin):
    """Self-contained multi-axis runtime budget enforcement."""

    name = "budget"
    # ``priority=5`` keeps the gate (pre_tool_execute / pre_subagent_run)
    # ahead of most user plugins so a hard-wall block fires before
    # downstream side effects.
    priority = 5

    @classmethod
    def option_schema(cls) -> dict[str, dict[str, Any]]:
        return {
            "turn_budget": {
                "type": "dict",
                "default": None,
                "doc": (
                    "Turn budget as ``{soft: N, hard: M}`` (hard > 0 to "
                    "enable). YAML may also use the list shape "
                    "``[soft, hard]``; the UI uses the object form."
                ),
            },
            "walltime_budget": {
                "type": "dict",
                "default": None,
                "doc": (
                    "Wall-clock budget in seconds — ``{soft, hard}`` or "
                    "null to disable."
                ),
            },
            "tool_call_budget": {
                "type": "dict",
                "default": None,
                "doc": (
                    "Cumulative tool-call budget — ``{soft, hard}`` or "
                    "null to disable."
                ),
            },
        }

    def __init__(
        self,
        *,
        turn_budget: Any = None,
        walltime_budget: Any = None,
        tool_call_budget: Any = None,
        options: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        # Accept either flattened kwargs (loader path: ``cls(**options)``)
        # or a nested ``options`` dict (fallback path used by tests /
        # direct construction).
        opts: dict[str, Any] = dict(options or {})
        if turn_budget is not None and "turn_budget" not in opts:
            opts["turn_budget"] = turn_budget
        if walltime_budget is not None and "walltime_budget" not in opts:
            opts["walltime_budget"] = walltime_budget
        if tool_call_budget is not None and "tool_call_budget" not in opts:
            opts["tool_call_budget"] = tool_call_budget
        self.options = {
            "turn_budget": opts.get("turn_budget"),
            "walltime_budget": opts.get("walltime_budget"),
            "tool_call_budget": opts.get("tool_call_budget"),
        }
        self._turn_started_at: float | None = None
        self._pending: list[tuple[str, AlarmState]] = []
        self.refresh_options()

    # ── Options ──

    def refresh_options(self) -> None:
        """Rebuild :attr:`_budgets` from :attr:`options`."""
        self._budgets = _build_budget_set(self.options)

    # ── Public accessor (other plugins / tests can introspect) ──

    @property
    def budgets(self) -> BudgetSet | None:
        """Internal :class:`BudgetSet`; ``None`` when no axis is configured."""
        return self._budgets

    # ── Lifecycle ──

    async def on_load(self, context: PluginContext) -> None:  # noqa: ARG002
        return None

    # ── Prompt contribution (alarm) ──

    def get_prompt_content(self, context: PluginContext) -> str | None:  # noqa: ARG002
        budgets = self._budgets
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
            "You are running with a budget across these axes. The runtime "
            "tracks usage and injects alarms when you approach a limit.",
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

    # ── LLM hooks (ticker + alarm) ──

    async def pre_llm_call(
        self, messages: list[dict], **kwargs: Any
    ) -> list[dict] | None:
        self._turn_started_at = time.monotonic()
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
        if self._budgets is None:
            return None
        started = self._turn_started_at or time.monotonic()
        self._budgets.tick(turns=1, seconds=max(time.monotonic() - started, 0.0))
        self._pending.extend(self._budgets.drain_alarms())
        return None

    # ── Tool hooks (ticker + gate) ──

    async def pre_tool_execute(self, args: dict, **kwargs: Any) -> None:
        if self._budgets is not None and self._budgets.is_hard_walled():
            axis = self._budgets.exhausted_axis() or "unknown"
            raise PluginBlockError(
                f"Budget exhausted ({axis}). Tools are no longer available; "
                "return your final text answer."
            )
        return None

    async def post_tool_execute(self, result: Any, **kwargs: Any) -> None:
        if self._budgets is not None:
            self._budgets.tick(tool_calls=1)
        return None

    # ── Sub-agent hooks (gate) ──

    async def pre_subagent_run(self, task: str, **kwargs: Any) -> str | None:
        if self._budgets is not None and self._budgets.is_hard_walled():
            raise PluginBlockError(
                "Budget exhausted; sub-agent dispatch disabled. "
                "Return your final text answer."
            )
        return task


def _build_budget_set(opts: dict[str, Any]) -> BudgetSet | None:
    turn = _axis_from_option("turn", opts.get("turn_budget"))
    walltime = _axis_from_option("walltime", opts.get("walltime_budget"))
    tool_call = _axis_from_option("tool_call", opts.get("tool_call_budget"))
    if turn is None and walltime is None and tool_call is None:
        return None
    return BudgetSet(turn=turn, walltime=walltime, tool_call=tool_call)


def _axis_from_option(name: str, value: Any) -> BudgetAxis | None:
    if value is None:
        return None
    soft: float = 0.0
    hard: float = 0.0
    if isinstance(value, dict):
        hard = float(value.get("hard", value.get("limit", 0)) or 0)
        soft = float(value.get("soft", 0) or 0)
    elif isinstance(value, (list, tuple)) and len(value) >= 2:
        soft = float(value[0])
        hard = float(value[1])
    else:
        return None
    if hard <= 0:
        return None
    return BudgetAxis(name=name, soft=soft, hard=hard)


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
