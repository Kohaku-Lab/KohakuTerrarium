"""Unit tests for :mod:`kohakuterrarium.builtins.plugin_catalog`.

Pins the built-in plugin catalog contents (the seam ``bootstrap.plugins``
iterates to register each built-in plugin as available-but-disabled in the
plugin panel) and the pack/alias expansion rules.
"""

from kohakuterrarium.builtins import plugin_catalog as pc

_BUILTIN_PLUGIN_NAMES = {"budget", "compact.auto", "goal", "permgate", "sandbox"}


class TestLookupPlugin:
    def test_goal_resolves_to_builtin_module(self):
        spec = pc.lookup_plugin("goal")
        assert spec == {
            "module": "kohakuterrarium.builtins.plugins.goal.plugin",
            "class": "GoalPlugin",
        }

    def test_unknown_returns_none(self):
        assert pc.lookup_plugin("nope") is None


class TestListCatalogPlugins:
    def test_lists_every_builtin_including_goal(self):
        entries = pc.list_catalog_plugins()
        by_name = {e["name"]: e for e in entries}
        assert _BUILTIN_PLUGIN_NAMES <= set(by_name)
        goal = by_name["goal"]
        # goal appears identically to the other built-ins: a package-type
        # load spec the plugin panel registers disabled-by-default.
        assert goal["type"] == "package"
        assert goal["module"] == "kohakuterrarium.builtins.plugins.goal.plugin"
        assert goal["class"] == "GoalPlugin"

    def test_every_entry_has_load_spec_shape(self):
        for entry in pc.list_catalog_plugins():
            assert entry["name"]
            assert entry["module"] and entry["class"]
            assert entry["type"] == "package"


class TestResolvePluginSpecs:
    def test_goal_is_not_bundled_in_any_pack(self):
        # Enabling goal is an explicit per-agent decision — no pack pulls it in.
        specs = pc.resolve_plugin_specs(["auto-compact"])
        assert all(s["name"] != "goal" for s in specs)

    def test_explicit_goal_name_resolves(self):
        specs = pc.resolve_plugin_specs(["goal"])
        assert [s["name"] for s in specs] == ["goal"]
        assert specs[0]["module"] == "kohakuterrarium.builtins.plugins.goal.plugin"
