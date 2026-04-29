"""Tests for built-in runtime plugin catalog and default-pack merging."""

from kohakuterrarium.bootstrap.plugins import init_plugins
from kohakuterrarium.builtins.plugin_catalog import resolve_plugin_specs


def test_resolve_default_runtime_pack_expands_in_order():
    specs = resolve_plugin_specs(["default-runtime"])
    assert [spec["name"] for spec in specs] == [
        "budget.ticker",
        "budget.alarm",
        "budget.gate",
        "compact.auto",
    ]


def test_resolve_plugin_specs_deduplicates_pack_collisions():
    specs = resolve_plugin_specs(["budget", "budget.ticker", "auto-compact"])
    assert [spec["name"] for spec in specs] == [
        "budget.ticker",
        "budget.alarm",
        "budget.gate",
        "compact.auto",
    ]


def test_resolve_unknown_default_plugin_is_dropped():
    assert resolve_plugin_specs(["does.not.exist"]) == []


def test_init_plugins_loads_default_pack_plugins():
    manager = init_plugins([], default_plugins=["budget"])
    names = [item["name"] for item in manager.list_plugins() if item["enabled"]]
    assert names[:3] == ["budget.gate", "budget.ticker", "budget.alarm"]


def test_explicit_plugin_overrides_default_by_name():
    explicit = [
        {
            "name": "budget.ticker",
            "module": "kohakuterrarium.builtins.plugins.budget.alarm",
            "class": "BudgetAlarmPlugin",
        }
    ]
    manager = init_plugins(explicit, default_plugins=["budget"])
    enabled = {item["name"] for item in manager.list_plugins() if item["enabled"]}
    plugins_by_name = {
        plugin.name: plugin for plugin in manager._plugins if plugin.name in enabled
    }

    assert {"budget.ticker", "budget.alarm", "budget.gate"}.issubset(enabled)
    assert type(plugins_by_name["budget.ticker"]).__name__ == "BudgetAlarmPlugin"
    assert type(plugins_by_name["budget.alarm"]).__name__ == "BudgetAlarmPlugin"
    assert type(plugins_by_name["budget.gate"]).__name__ == "BudgetGatePlugin"
    assert [item["name"] for item in manager.list_plugins() if item["enabled"]].count(
        "budget.ticker"
    ) == 1
