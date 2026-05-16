"""Unit tests for :mod:`kohakuterrarium.bootstrap.plugins`."""

import sys
import textwrap

import pytest

from kohakuterrarium.bootstrap import plugins as plug_mod
from kohakuterrarium.bootstrap.plugins import (
    _load_one,
    _merge_default_plugin_specs,
    _resolve_from_catalog,
    _resolve_from_packages,
    init_plugins,
)
from kohakuterrarium.core.loader import ModuleLoader
from kohakuterrarium.modules.plugin.manager import PluginManager


@pytest.fixture(autouse=True)
def _no_packages(monkeypatch):
    """Prevent the test from picking up real installed packages."""
    monkeypatch.setattr(plug_mod, "list_packages", lambda: [])
    monkeypatch.setattr(plug_mod, "ensure_package_importable", lambda n: None)
    # Suppress the builtin catalog so it doesn't auto-register plugins.
    monkeypatch.setattr(plug_mod, "list_catalog_plugins", lambda: [])
    monkeypatch.setattr(plug_mod, "lookup_plugin", lambda name: None)


class TestInitPluginsEmpty:
    def test_no_configs_returns_empty_manager(self):
        mgr = init_plugins([])
        assert isinstance(mgr, PluginManager)
        assert len(mgr) == 0

    def test_none_inputs_safe(self):
        mgr = init_plugins(None)
        assert isinstance(mgr, PluginManager)


class TestInitPluginsCustom:
    def test_loads_custom_plugin(self, tmp_path):
        custom = tmp_path / "custom"
        custom.mkdir()
        (custom / "plug.py").write_text(textwrap.dedent("""
                from kohakuterrarium.modules.plugin.base import BasePlugin


                class MyPlugin(BasePlugin):
                    name = "custom_test_plug"
                """))
        loader = ModuleLoader(agent_path=tmp_path)
        configs = [
            {
                "name": "custom_test_plug",
                "type": "custom",
                "module": "custom/plug.py",
                "class": "MyPlugin",
            }
        ]
        mgr = init_plugins(configs, loader=loader)
        # The config-declared custom plugin loaded AND is enabled (it was
        # listed in config, so it's active, not just discovered).
        assert any(getattr(p, "name", "") == "custom_test_plug" for p in mgr._plugins)
        assert mgr.is_enabled("custom_test_plug") is True

    def test_missing_module_skipped(self, tmp_path):
        loader = ModuleLoader(agent_path=tmp_path)
        configs = [
            {
                "name": "broken",
                "type": "custom",
                "module": "missing.py",
                "class": "X",
            }
        ]
        # Should not raise — bad entries are skipped.
        mgr = init_plugins(configs, loader=loader)
        # No plugins registered.
        assert "broken" not in [getattr(p, "name", "") for p in mgr._plugins]

    def test_no_loader_skips_custom(self):
        configs = [
            {
                "name": "x",
                "type": "custom",
                "module": "m.py",
                "class": "X",
            }
        ]
        mgr = init_plugins(configs, loader=None)
        # No loader → custom plugin not loaded.
        assert all(getattr(p, "name", "") != "x" for p in mgr._plugins)


class TestInitPluginsPackage:
    def test_package_plugin_missing_module(self):
        configs = [
            {
                "name": "x",
                "type": "package",
                "module": "definitely_no_such_module_xyz",
                "class": "C",
            }
        ]
        mgr = init_plugins(configs)
        # Missing module silently skipped.
        assert "x" not in [getattr(p, "name", "") for p in mgr._plugins]


class TestInitPluginsBuiltinCatalog:
    def test_catalog_plugins_discovered_as_disabled(self, monkeypatch, tmp_path):
        """When a plugin is in the builtin catalog but not in config,
        it should be registered as disabled."""
        # Build a real module the loader can import.
        custom = tmp_path / "custom"
        custom.mkdir()
        (custom / "catalog_plug.py").write_text(textwrap.dedent("""
                from kohakuterrarium.modules.plugin.base import BasePlugin

                class _FakeCatalogPlugin(BasePlugin):
                    name = "catalog_fake"
                """))
        spec = {
            "name": "catalog_fake",
            "type": "custom",
            "module": "custom/catalog_plug.py",
            "class": "_FakeCatalogPlugin",
        }
        monkeypatch.setattr(plug_mod, "list_catalog_plugins", lambda: [spec])
        monkeypatch.setattr(plug_mod, "lookup_plugin", lambda name: None)
        loader = ModuleLoader(agent_path=tmp_path)
        mgr = init_plugins([], loader=loader)
        names = [getattr(p, "name", "") for p in mgr._plugins]
        # A catalog plugin absent from config is registered, but DISABLED —
        # discovered for the UI without being active.
        assert "catalog_fake" in names
        assert mgr.is_enabled("catalog_fake") is False


class TestPackageDiscovery:
    def test_no_packages_no_op(self, monkeypatch):
        # Already patched via fixture: list_packages returns [].
        mgr = init_plugins([])
        # Nothing crashes.
        assert isinstance(mgr, PluginManager)

    def test_package_discovery_with_failures(self, monkeypatch):
        # Return a fake package with a broken plugin manifest entry.
        def fake_list():
            return [
                {
                    "name": "bad-pkg",
                    "plugins": [
                        {"name": "ghost", "module": "no.such.mod", "class": "X"}
                    ],
                }
            ]

        monkeypatch.setattr(plug_mod, "list_packages", fake_list)
        monkeypatch.setattr(plug_mod, "ensure_package_importable", lambda n: None)
        mgr = init_plugins([])
        # The broken plugin is silently skipped.
        names = [getattr(p, "name", "") for p in mgr._plugins]
        assert "ghost" not in names

    def test_package_listing_failure_swallowed(self, monkeypatch):
        # If list_packages itself raises, discovery aborts cleanly.
        def boom():
            raise RuntimeError("disk error")

        monkeypatch.setattr(plug_mod, "list_packages", boom)
        mgr = init_plugins([])
        assert isinstance(mgr, PluginManager)

    def test_package_plugin_discovered_as_disabled(self, monkeypatch, tmp_path):
        # A working plugin shipped by an installed package, not in config,
        # is registered but DISABLED (opt-in via UI).
        mod_dir = tmp_path / "pkgsrc"
        mod_dir.mkdir()
        (mod_dir / "biome_plug.py").write_text(textwrap.dedent("""
                from kohakuterrarium.modules.plugin.base import BasePlugin

                class BiomePlugin(BasePlugin):
                    name = "biome_disc"
                """))
        monkeypatch.syspath_prepend(str(mod_dir))
        monkeypatch.setattr(
            plug_mod,
            "list_packages",
            lambda: [
                {
                    "name": "biome",
                    "plugins": [
                        {
                            "name": "biome_disc",
                            "module": "biome_plug",
                            "class": "BiomePlugin",
                        }
                    ],
                }
            ],
        )
        monkeypatch.setattr(plug_mod, "ensure_package_importable", lambda n: None)
        mgr = init_plugins([])
        sys.modules.pop("biome_plug", None)
        assert "biome_disc" in [getattr(p, "name", "") for p in mgr._plugins]
        assert mgr.is_enabled("biome_disc") is False


class TestLoadOne:
    def test_string_config_treated_as_name(self, monkeypatch):
        # A bare string entry resolves through the catalog by name.
        monkeypatch.setattr(
            plug_mod,
            "lookup_plugin",
            lambda name: None,
        )
        monkeypatch.setattr(plug_mod, "list_packages", lambda: [])
        # Unknown name → None (no module resolved).
        assert _load_one("ghost_name", loader=None) is None

    def test_missing_module_and_class_returns_none(self):
        # An entry with a name that resolves to nothing, no module.
        # name present but module empty and catalog/packages miss → None.
        result = _load_one({"name": ""}, loader=None)
        assert result is None

    def test_resolved_via_catalog_loads_plugin(self, monkeypatch, tmp_path):
        mod_dir = tmp_path / "src"
        mod_dir.mkdir()
        (mod_dir / "cat_plug.py").write_text(textwrap.dedent("""
                from kohakuterrarium.modules.plugin.base import BasePlugin

                class CatPlugin(BasePlugin):
                    name = "from_catalog"
                """))
        monkeypatch.syspath_prepend(str(mod_dir))
        monkeypatch.setattr(
            plug_mod,
            "lookup_plugin",
            lambda name: {"module": "cat_plug", "class": "CatPlugin"},
        )
        plugin = _load_one({"name": "from_catalog"}, loader=None)
        sys.modules.pop("cat_plug", None)
        assert plugin is not None
        # The config-supplied name is stamped onto the loaded plugin.
        assert plugin.name == "from_catalog"

    def test_not_a_baseplugin_returns_none(self, monkeypatch, tmp_path):
        mod_dir = tmp_path / "src"
        mod_dir.mkdir()
        (mod_dir / "notplug.py").write_text("class NotAPlugin:\n    pass\n")
        monkeypatch.syspath_prepend(str(mod_dir))
        result = _load_one(
            {"name": "x", "module": "notplug", "class": "NotAPlugin"}, loader=None
        )
        sys.modules.pop("notplug", None)
        # Loaded object isn't a BasePlugin → rejected.
        assert result is None

    def test_description_from_config_applied(self, monkeypatch, tmp_path):
        mod_dir = tmp_path / "src"
        mod_dir.mkdir()
        (mod_dir / "descplug.py").write_text(textwrap.dedent("""
                from kohakuterrarium.modules.plugin.base import BasePlugin

                class DescPlugin(BasePlugin):
                    name = "desc_plug"
                """))
        monkeypatch.syspath_prepend(str(mod_dir))
        plugin = _load_one(
            {
                "name": "desc_plug",
                "module": "descplug",
                "class": "DescPlugin",
                "description": "from config",
            },
            loader=None,
        )
        sys.modules.pop("descplug", None)
        assert plugin is not None
        assert plugin.description == "from config"

    def test_import_failure_returns_none(self):
        result = _load_one(
            {"name": "x", "module": "definitely.no.such.module.xyz", "class": "C"},
            loader=None,
        )
        assert result is None

    def test_no_config_name_keeps_plugin_class_name(self, monkeypatch, tmp_path):
        # Config provides module+class but NO name. The plugin's own class
        # attribute supplies the name.
        mod_dir = tmp_path / "src"
        mod_dir.mkdir()
        (mod_dir / "selfnamed.py").write_text(textwrap.dedent("""
                from kohakuterrarium.modules.plugin.base import BasePlugin

                class SelfNamed(BasePlugin):
                    name = "self_named"
                """))
        monkeypatch.syspath_prepend(str(mod_dir))
        plugin = _load_one({"module": "selfnamed", "class": "SelfNamed"}, loader=None)
        sys.modules.pop("selfnamed", None)
        assert plugin is not None
        assert plugin.name == "self_named"


class TestMergeDefaultPluginSpecs:
    def test_defaults_appended_when_not_in_config(self):
        configs = [{"name": "explicit"}]
        merged = _merge_default_plugin_specs(
            configs, [], [{"name": "default_a"}, {"name": "default_b"}]
        )
        names = [c.get("name") for c in merged]
        assert "explicit" in names
        assert "default_a" in names
        assert "default_b" in names

    def test_explicit_config_shadows_default_spec(self):
        # When config explicitly lists a plugin, the default spec for the
        # same name is NOT appended (config wins).
        configs = [{"name": "shared", "module": "config.version"}]
        merged = _merge_default_plugin_specs(
            configs, [], [{"name": "shared", "module": "default.version"}]
        )
        shared_entries = [c for c in merged if c.get("name") == "shared"]
        assert len(shared_entries) == 1
        assert shared_entries[0]["module"] == "config.version"


class TestResolveFromCatalog:
    def test_returns_module_class_tuple(self, monkeypatch):
        monkeypatch.setattr(
            plug_mod,
            "lookup_plugin",
            lambda name: {"module": "m.path", "class": "C"},
        )
        assert _resolve_from_catalog("x") == ("m.path", "C")

    def test_class_name_alias_accepted(self, monkeypatch):
        monkeypatch.setattr(
            plug_mod,
            "lookup_plugin",
            lambda name: {"module": "m.path", "class_name": "C"},
        )
        assert _resolve_from_catalog("x") == ("m.path", "C")

    def test_unknown_returns_none(self, monkeypatch):
        monkeypatch.setattr(plug_mod, "lookup_plugin", lambda name: None)
        assert _resolve_from_catalog("ghost") is None

    def test_incomplete_spec_returns_none(self, monkeypatch):
        monkeypatch.setattr(plug_mod, "lookup_plugin", lambda name: {"module": "m"})
        # No class → not resolvable.
        assert _resolve_from_catalog("x") is None


class TestPreImportPackages:
    def test_pre_import_failure_swallowed(self, monkeypatch):
        # A package config triggers the pre-import walk; if list_packages
        # raises there, init_plugins must still return a manager.
        def boom():
            raise RuntimeError("walk failed")

        monkeypatch.setattr(plug_mod, "list_packages", boom)
        configs = [{"name": "p", "type": "package", "module": "x.y", "class": "C"}]
        mgr = init_plugins(configs)
        assert isinstance(mgr, PluginManager)

    def test_catalog_spec_without_name_skipped(self, monkeypatch):
        # A catalog spec missing a name is skipped by _discover_catalog_plugins.
        monkeypatch.setattr(
            plug_mod, "list_catalog_plugins", lambda: [{"module": "m", "class": "C"}]
        )
        mgr = init_plugins([])
        assert len(mgr) == 0


class TestLoadOneKohakuModule:
    def test_kohakuterrarium_module_forced_to_package_type(self, monkeypatch, tmp_path):
        # A module path under ``kohakuterrarium.`` is always loaded as a
        # package-type plugin even if the config says ``custom``.
        loaded = {}

        class _FakeLoader:
            def load_instance(self, module, class_name, module_type, options):
                loaded["module_type"] = module_type
                from kohakuterrarium.modules.plugin.base import BasePlugin

                p = BasePlugin()
                p.name = "kt_plug"
                return p

        plugin = _load_one(
            {
                "name": "kt_plug",
                "type": "custom",
                "module": "kohakuterrarium.builtins.plugins.something",
                "class": "C",
            },
            loader=_FakeLoader(),
        )
        assert plugin is not None
        # ``custom`` was overridden to ``package`` for a kohakuterrarium module.
        assert loaded["module_type"] == "package"


class TestResolveFromPackages:
    def test_finds_plugin_in_package(self, monkeypatch):
        monkeypatch.setattr(
            plug_mod,
            "list_packages",
            lambda: [
                {
                    "name": "pkg",
                    "plugins": [
                        {"name": "p1", "module": "pkg.plugins.p1", "class": "P1"}
                    ],
                }
            ],
        )
        monkeypatch.setattr(plug_mod, "ensure_package_importable", lambda n: None)
        assert _resolve_from_packages("p1") == ("pkg.plugins.p1", "P1")

    def test_unknown_plugin_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            plug_mod, "list_packages", lambda: [{"name": "pkg", "plugins": []}]
        )
        assert _resolve_from_packages("ghost") is None

    def test_listing_failure_returns_none(self, monkeypatch):
        def boom():
            raise RuntimeError("fail")

        monkeypatch.setattr(plug_mod, "list_packages", boom)
        assert _resolve_from_packages("x") is None


class TestDiscoverPackagePluginsEdgeCases:
    def test_package_without_plugins_key_skipped(self, monkeypatch):
        monkeypatch.setattr(
            plug_mod, "list_packages", lambda: [{"name": "pkg-no-plugins"}]
        )
        monkeypatch.setattr(plug_mod, "ensure_package_importable", lambda n: None)
        mgr = init_plugins([])
        assert isinstance(mgr, PluginManager)

    def test_non_dict_plugin_def_skipped(self, monkeypatch):
        monkeypatch.setattr(
            plug_mod,
            "list_packages",
            lambda: [{"name": "pkg", "plugins": ["junk-string", 42]}],
        )
        monkeypatch.setattr(plug_mod, "ensure_package_importable", lambda n: None)
        # Non-dict plugin entries are skipped without crashing.
        mgr = init_plugins([])
        assert isinstance(mgr, PluginManager)

    def test_already_loaded_package_plugin_not_redisovered(self, monkeypatch, tmp_path):
        # A plugin loaded via config (Phase 1) must not be re-registered
        # by package discovery (Phase 2).
        mod_dir = tmp_path / "src"
        mod_dir.mkdir()
        (mod_dir / "shared_plug.py").write_text(textwrap.dedent("""
                from kohakuterrarium.modules.plugin.base import BasePlugin

                class SharedPlugin(BasePlugin):
                    name = "shared_one"
                """))
        monkeypatch.syspath_prepend(str(mod_dir))
        monkeypatch.setattr(
            plug_mod,
            "list_packages",
            lambda: [
                {
                    "name": "pkg",
                    "plugins": [
                        {
                            "name": "shared_one",
                            "module": "shared_plug",
                            "class": "SharedPlugin",
                        }
                    ],
                }
            ],
        )
        monkeypatch.setattr(plug_mod, "ensure_package_importable", lambda n: None)
        configs = [
            {
                "name": "shared_one",
                "type": "package",
                "module": "shared_plug",
                "class": "SharedPlugin",
            }
        ]
        mgr = init_plugins(configs)
        sys.modules.pop("shared_plug", None)
        # Exactly one registration, and it's ENABLED (config wins over
        # the disabled package-discovery path).
        matches = [p for p in mgr._plugins if getattr(p, "name", "") == "shared_one"]
        assert len(matches) == 1
        assert mgr.is_enabled("shared_one") is True
