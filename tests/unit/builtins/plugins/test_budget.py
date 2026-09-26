"""Unit tests for :mod:`kohakuterrarium.builtins.plugins.budget.plugin`."""

import pytest

from kohakuterrarium.builtins.plugins.budget.plugin import BudgetPlugin
from kohakuterrarium.core.budget import AlarmState
from kohakuterrarium.modules.plugin.base import PluginBlockError
from kohakuterrarium.modules.plugin.option_validation import PluginOptionError


class TestBudgetAlarmInjection:
    async def test_inserts_alarms_after_all_leading_system_messages(self):
        plugin = BudgetPlugin()
        plugin._pending = [("turn", AlarmState.SOFT)]
        messages = [
            {"role": "system", "content": "framework"},
            {"role": "system", "content": "creature"},
            {"role": "user", "content": "continue"},
        ]

        result = await plugin.pre_llm_call(messages)

        assert [message["role"] for message in result] == [
            "system",
            "system",
            "user",
            "user",
        ]
        assert result[:2] == messages[:2]
        assert result[2]["content"].startswith("[budget soft]")
        assert result[3:] == messages[2:]
        assert plugin._pending == []


class TestBudgetReconfigure:
    async def test_updates_preserve_usage_and_recompute_gate(self):
        plugin = BudgetPlugin(tool_call_budget={"soft": 2, "hard": 5})
        for _ in range(4):
            await plugin.post_tool_execute(None)
        plugin.set_options({"tool_call_budget": {"soft": 3, "hard": 6}})
        assert plugin.budgets.tool_call.used == 4
        await plugin.pre_tool_execute({})
        plugin.set_options({"tool_call_budget": {"soft": 1, "hard": 3}})
        with pytest.raises(PluginBlockError):
            await plugin.pre_tool_execute({})
        plugin.set_options({"tool_call_budget": {"soft": 8, "hard": 10}})
        assert plugin.budgets.tool_call.used == 4
        assert plugin.budgets.tool_call.last_alarm is AlarmState.OK
        await plugin.pre_tool_execute({})
        assert await plugin.pre_llm_call([]) is None

    async def test_unchanged_axes_keep_both_pending_alarm_locations(self):
        plugin = BudgetPlugin(
            turn_budget={"soft": 1, "hard": 9}, tool_call_budget={"soft": 1, "hard": 9}
        )
        await plugin.post_llm_call([], "", {})
        await plugin.post_tool_execute(None)
        plugin.set_options({"walltime_budget": {"hard": 20}})
        assert plugin.budgets.turn.used == 1
        assert plugin.budgets.tool_call.used == 1
        assert plugin._pending == [("turn", AlarmState.SOFT)]
        assert plugin.budgets.tool_call.pending_transitions == [AlarmState.SOFT]
        plugin.set_options({})
        assert plugin._pending == [("turn", AlarmState.SOFT)]
        assert plugin.budgets.tool_call.pending_transitions == [AlarmState.SOFT]

    async def test_changed_axis_replaces_stale_alarm_once(self):
        plugin = BudgetPlugin(turn_budget={"soft": 1, "hard": 2})
        await plugin.post_llm_call([], "", {})
        plugin.set_options({"turn_budget": {"hard": 1}})
        messages = await plugin.pre_llm_call([])
        assert len(messages) == 1
        assert "[budget hard]" in messages[0]["content"]
        assert plugin.budgets.turn.pending_transitions == []
        assert await plugin.pre_llm_call([]) is None

    @pytest.mark.parametrize("disabled", [None, {"hard": 0}])
    async def test_disable_reenable_preserves_accounted_usage(self, disabled):
        plugin = BudgetPlugin(tool_call_budget={"hard": 3})
        for _ in range(3):
            await plugin.post_tool_execute(None)
        plugin.set_options({"tool_call_budget": disabled})
        assert plugin.budgets is None
        await plugin.pre_tool_execute({})
        await plugin.post_tool_execute(None)
        plugin.set_options({"tool_call_budget": {"hard": 3}})
        assert plugin.budgets.tool_call.used == 3
        with pytest.raises(PluginBlockError):
            await plugin.pre_tool_execute({})

    @pytest.mark.parametrize(
        "changes",
        [
            {"tool_call_budget": {"hard": "bad"}},
            {"tool_call_budget": {"hard": float("nan")}},
            {"tool_call_budget": {"hard": ["bad"]}, "turn_budget": {"hard": 7}},
            {"unknown": 3},
        ],
    )
    def test_invalid_update_is_atomic(self, changes):
        plugin = BudgetPlugin(tool_call_budget={"hard": 3})
        plugin.budgets.tick(tool_calls=2)
        before = plugin.budgets.snapshot()
        options = plugin.get_options()
        with pytest.raises(PluginOptionError):
            plugin.set_options(changes)
        assert plugin.get_options() == options
        assert plugin.budgets.snapshot() == before
