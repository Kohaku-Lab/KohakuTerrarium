"""Unit tests for :mod:`kohakuterrarium.builtins.plugins.budget.plugin`."""

from kohakuterrarium.builtins.plugins.budget.plugin import BudgetPlugin
from kohakuterrarium.core.budget import AlarmState


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
