"""Unit tests for the ``ui.apps`` builtin plugin."""

from types import SimpleNamespace

import pytest

from kohakuterrarium.builtins.plugin_catalog import lookup_plugin
from kohakuterrarium.builtins.plugins.apps.plugin import (
    FULL_PAGE_PART,
    WITH_INLINE_LINE,
    AppsPlugin,
)
from kohakuterrarium.builtins.tools.show_app import ShowAppTool
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.modules.plugin.base import PluginContext, ToolVisibility
from tests.unit.test_tool_doc_shape import DISCOURAGING


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


def _context(renderers, visible):
    registry = Registry()
    if visible:
        registry.register_tool(ShowAppTool())
    router = SimpleNamespace(ui_renderers=lambda: dict(renderers))
    agent = SimpleNamespace(
        output_router=router,
        registry=registry,
        _visible_catalog=lambda: ToolVisibility(
            allowed_tools=frozenset({"show_app"} if visible else set())
        ),
    )
    return PluginContext(_host_agent=agent)


def test_presentation_part_follows_show_app_visibility():
    plugin = AppsPlugin()
    assert plugin.presentation_contribution(_context({}, visible=False)) is None
    assert plugin.presentation_contribution(PluginContext()) is None
    part = plugin.presentation_contribution(_context({"kt.app": 1}, visible=True))
    assert part == FULL_PAGE_PART
    assert "prefer this to a long Markdown document" in part
    with_inline = plugin.presentation_contribution(
        _context({"kt.app": 1, "kt.ui": 1}, visible=True)
    )
    assert with_inline == f"{FULL_PAGE_PART} {WITH_INLINE_LINE}"
    assert plugin.prompt_renderers == frozenset({"kt.ui"})
    for phrase in DISCOURAGING:
        assert phrase not in with_inline.lower()
