"""Catalog and built-in plugin pack expansion."""

from kohakuterrarium.bootstrap.plugins import init_plugins
from kohakuterrarium.builtins.plugin_catalog import lookup_plugin, resolve_plugin_specs


def test_resolve_auto_compact_pack():
    specs = resolve_plugin_specs(["auto-compact"])
    names = [spec["name"] for spec in specs]
    assert names == ["compact.auto"]


def test_resolve_budget_alone_returns_single_plugin():
    specs = resolve_plugin_specs(["budget"])
    names = [spec["name"] for spec in specs]
    assert names == ["budget"]


def test_resolve_unknown_default_plugin_is_dropped():
    assert resolve_plugin_specs(["does.not.exist"]) == []


def test_lookup_plugin_returns_catalog_spec():
    spec = lookup_plugin("budget")
    assert spec is not None
    assert spec["module"] == "kohakuterrarium.builtins.plugins.budget.plugin"
    assert spec["class"] == "BudgetPlugin"


def test_init_plugins_loads_inline_budget_plugin_with_options():
    explicit = [
        {
            "name": "budget",
            "options": {
                "turn_budget": [40, 60],
                "tool_call_budget": [75, 100],
            },
        }
    ]
    manager = init_plugins(explicit)
    plugins_by_name = {p.name: p for p in manager._plugins}
    assert "budget" in plugins_by_name
    plugin = plugins_by_name["budget"]
    assert plugin.budgets is not None
    assert plugin.budgets.turn.hard == 60
    assert plugin.budgets.tool_call.hard == 100


def test_init_plugins_default_pack_does_not_enable_budget():
    """``auto-compact`` pack must not enable the budget plugin.

    Phase B introduced catalog discovery that registers all built-in
    plugins as available-but-disabled so the frontend Plugins tab can
    list them with an "Enable" button. So ``budget`` may appear in
    ``manager._plugins`` — but it must NOT be enabled by default.
    """
    manager = init_plugins([], default_plugins=["auto-compact"])
    assert not manager.is_enabled("budget")
    # ``compact.auto`` from the pack should be enabled.
    assert manager.is_enabled("compact.auto")
