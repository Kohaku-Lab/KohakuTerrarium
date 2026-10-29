"""Unit tests for the ``ui.apps`` builtin plugin."""

import pytest

from kohakuterrarium.builtins.plugin_catalog import lookup_plugin
from kohakuterrarium.builtins.plugins.apps.plugin import AppsPlugin
from kohakuterrarium.builtins.tools.show_app import ShowAppTool


def test_catalog_resolves_ui_apps():
    spec = lookup_plugin("ui.apps")
    assert spec == {
        "module": "kohakuterrarium.builtins.plugins.apps.plugin",
        "class": "AppsPlugin",
    }


def test_contributes_one_configured_show_app_tool():
    plugin = AppsPlugin(allowed_hosts=["tailwind"], default_surface="chat")
    (tool,) = plugin.contribute_tools()
    assert isinstance(tool, ShowAppTool) and tool is plugin.contribute_tools()[0]
    assert tool.allowed_hosts == ("cdn.tailwindcss.com",)
    assert tool.default_surface == "chat"
    assert plugin.get_options() == {
        "allowed_hosts": ["cdn.tailwindcss.com"],
        "default_surface": "chat",
        "max_document_kb": 256,
    }


def test_options_dict_and_defaults():
    plugin = AppsPlugin(options={"allowed_hosts": [], "max_document_kb": 64})
    assert plugin.tool.allowed_hosts == ()
    assert plugin.tool.max_document_bytes == 64 * 1024
    assert "esm.sh" in AppsPlugin().tool.allowed_hosts


def test_bad_hosts_fail_the_load():
    with pytest.raises(ValueError):
        AppsPlugin(allowed_hosts=["http://evil.example"])
