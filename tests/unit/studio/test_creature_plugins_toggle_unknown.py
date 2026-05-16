"""Unit: ``creature_plugins.toggle_plugin`` against a *real* plugin manager.

Pins bug B-iw-1 at the unit tier: the studio per-creature plugin
toggle silently fabricates ``{"enabled": True}`` for a plugin name the
creature does not have. Driven with a real
:class:`kohakuterrarium.modules.plugin.manager.PluginManager` holding a
real :class:`BasePlugin` — NOT a hand-rolled fake whose ``enable`` /
``disable`` ignore the documented bool return (the same masking
anti-pattern that hid B-e2e-2).

The sibling terrarium path (``terrarium.creature_ops.agent_toggle_
plugin``) was fixed under B-e2e-2 to raise ``KeyError`` for an
unregistered plugin; this studio path was not, and still silently
no-ops.
"""

from types import SimpleNamespace

import pytest

from kohakuterrarium.modules.plugin.base import BasePlugin
from kohakuterrarium.modules.plugin.manager import PluginManager
from kohakuterrarium.studio.sessions import creature_plugins as plug_mod


def _agent_with_real_manager() -> SimpleNamespace:
    """An agent stand-in carrying a *real* PluginManager + real plugin."""
    plugin = BasePlugin()
    plugin.name = "realplugin"
    mgr = PluginManager()
    mgr.register(plugin)
    return SimpleNamespace(plugins=mgr)


def _install_find(monkeypatch, agent) -> None:
    monkeypatch.setattr(
        plug_mod, "find_creature", lambda eng, sid, cid: SimpleNamespace(agent=agent)
    )


class TestToggleKnownPlugin:
    async def test_toggle_flips_a_real_plugin_off_then_on(self, monkeypatch):
        """Contract: toggling a plugin the creature *does* have flips its
        real ``enabled`` flag — off, then back on — observed through the
        same manager."""
        agent = _agent_with_real_manager()
        _install_find(monkeypatch, agent)
        mgr = agent.plugins

        # Starts enabled → first toggle disables.
        first = await plug_mod.toggle_plugin(object(), "g", "c", "realplugin")
        assert first == {"name": "realplugin", "enabled": False}
        assert mgr.is_enabled("realplugin") is False

        # Second toggle re-enables.
        second = await plug_mod.toggle_plugin(object(), "g", "c", "realplugin")
        assert second == {"name": "realplugin", "enabled": True}
        assert mgr.is_enabled("realplugin") is True


class TestToggleUnknownPlugin:
    async def test_toggle_unknown_plugin_raises(self, monkeypatch):
        """Regression guard for B-iw-1 (FIXED): toggling a plugin name
        the creature does not have raises ``KeyError`` — the same
        loud-failure contract the B-e2e-2 fix gave the sibling terrarium
        path. Before the fix, ``creature_plugins.toggle_plugin`` ignored
        ``mgr.enable()``'s ``False`` return and fabricated a success dict.
        Fixed by delegating to ``terrarium.creature_ops.agent_toggle_
        plugin``, which raises ``KeyError`` for an unregistered name."""
        agent = _agent_with_real_manager()
        _install_find(monkeypatch, agent)
        with pytest.raises(KeyError):
            await plug_mod.toggle_plugin(object(), "g", "c", "no-such-plugin")
