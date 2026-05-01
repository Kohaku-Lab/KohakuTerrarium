"""Runtime mutation of plugin options via :class:`BasePlugin.set_options`.

Covers:
- BasePlugin's set_options + refresh_options + get_options
- PluginManager.list_plugins_with_options (schema + values)
- PluginManager.set_plugin_options dispatch
- Permgate's options round-trip (schema, mutation, refresh)
- Budget's options round-trip
"""

from typing import Any

import pytest

from kohakuterrarium.builtins.plugins.budget.plugin import BudgetPlugin
from kohakuterrarium.builtins.plugins.permgate.plugin import PermGatePlugin
from kohakuterrarium.modules.plugin.base import BasePlugin
from kohakuterrarium.modules.plugin.manager import PluginManager


class _DemoPlugin(BasePlugin):
    name = "demo"
    description = "Test fixture"
    priority = 50

    @classmethod
    def option_schema(cls) -> dict[str, dict[str, Any]]:
        return {
            "n": {"type": "int", "default": 0, "min": 0, "max": 100},
            "tag": {
                "type": "enum",
                "values": ["a", "b"],
                "default": "a",
            },
        }

    def __init__(self, n: int = 0, tag: str = "a") -> None:
        super().__init__()
        self.options = {"n": n, "tag": tag}
        self.refresh_options()

    def refresh_options(self) -> None:
        self._n = int(self.options.get("n") or 0)
        self._tag = str(self.options.get("tag") or "a")


class TestBasePluginOptions:
    def test_no_options_default(self):
        # Plain BasePlugin subclass with no schema — set_options rejects.
        class P(BasePlugin):
            name = "p"

        plugin = P()
        assert plugin.get_options() == {}
        with pytest.raises(ValueError, match="declares no options"):
            plugin.set_options({"x": 1})

    def test_set_options_validates_and_applies(self):
        plugin = _DemoPlugin()
        applied = plugin.set_options({"n": 42, "tag": "b"})
        assert applied == {"n": 42, "tag": "b"}
        assert plugin._n == 42
        assert plugin._tag == "b"

    def test_set_options_partial_merge(self):
        plugin = _DemoPlugin(n=5, tag="a")
        plugin.set_options({"n": 10})
        # tag should remain from initial init
        assert plugin.options["tag"] == "a"
        assert plugin.options["n"] == 10
        assert plugin._tag == "a"
        assert plugin._n == 10

    def test_unknown_key_raises(self):
        plugin = _DemoPlugin()
        with pytest.raises(ValueError, match="Unknown option"):
            plugin.set_options({"nope": 1})

    def test_invalid_value_raises(self):
        plugin = _DemoPlugin()
        with pytest.raises(ValueError, match=">="):
            plugin.set_options({"n": -5})


class TestPluginManagerOptions:
    def test_list_with_options_includes_schema(self):
        mgr = PluginManager()
        mgr.register(_DemoPlugin(n=7, tag="a"))
        rows = mgr.list_plugins_with_options()
        assert len(rows) == 1
        row = rows[0]
        assert row["name"] == "demo"
        assert row["enabled"] is True
        assert "n" in row["schema"]
        assert "tag" in row["schema"]
        assert row["options"] == {"n": 7, "tag": "a"}

    def test_set_plugin_options_dispatches(self):
        mgr = PluginManager()
        plugin = _DemoPlugin()
        mgr.register(plugin)
        applied = mgr.set_plugin_options("demo", {"n": 25})
        assert applied["n"] == 25
        assert plugin._n == 25

    def test_set_plugin_options_unknown_raises_keyerror(self):
        mgr = PluginManager()
        with pytest.raises(KeyError):
            mgr.set_plugin_options("ghost", {"x": 1})

    def test_get_plugin_returns_instance(self):
        mgr = PluginManager()
        plugin = _DemoPlugin()
        mgr.register(plugin)
        assert mgr.get_plugin("demo") is plugin
        assert mgr.get_plugin("ghost") is None


class TestPermGateOptions:
    def test_schema_declared(self):
        schema = PermGatePlugin.option_schema()
        assert "gated_tools" in schema
        assert "allowlist" in schema
        assert "timeout_s" in schema
        assert "surface" in schema
        assert schema["surface"]["type"] == "enum"

    def test_init_populates_options(self):
        p = PermGatePlugin(
            gated_tools=["bash"], allowlist=["read"], timeout_s=30, surface="chat"
        )
        assert p.get_options() == {
            "gated_tools": ["bash"],
            "allowlist": ["read"],
            "timeout_s": 30.0,
            "surface": "chat",
        }
        assert p._gated == ["bash"]
        assert p._allowlist == {"read"}
        assert p._timeout_s == 30.0
        assert p._surface == "chat"

    def test_set_options_refreshes_derived(self):
        p = PermGatePlugin()
        p.set_options({"gated_tools": ["write", "edit"], "surface": "modal"})
        assert p._gated == ["write", "edit"]
        assert p._surface == "modal"

    def test_invalid_surface_rejected(self):
        p = PermGatePlugin()
        with pytest.raises(ValueError, match="must be one of"):
            p.set_options({"surface": "popup"})


class TestBudgetOptions:
    def test_schema_declared(self):
        schema = BudgetPlugin.option_schema()
        assert "turn_budget" in schema
        assert "walltime_budget" in schema
        assert "tool_call_budget" in schema

    def test_init_via_dict_kwargs(self):
        p = BudgetPlugin(turn_budget={"soft": 30, "hard": 60})
        assert p.options["turn_budget"] == {"soft": 30, "hard": 60}
        assert p._budgets is not None
        assert p._budgets.turn is not None
        assert p._budgets.turn.hard == 60.0

    def test_set_options_rebuilds_budgets(self):
        p = BudgetPlugin()
        p.set_options({"turn_budget": {"soft": 10, "hard": 20}})
        assert p._budgets is not None
        assert p._budgets.turn is not None
        assert p._budgets.turn.hard == 20.0

    def test_set_options_to_null_disables_axis(self):
        p = BudgetPlugin(turn_budget={"soft": 10, "hard": 20})
        assert p._budgets is not None
        p.set_options({"turn_budget": None})
        # All axes None → no budget set
        assert p._budgets is None
