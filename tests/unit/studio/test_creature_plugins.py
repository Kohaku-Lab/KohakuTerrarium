"""Unit tests for :mod:`kohakuterrarium.studio.sessions.creature_plugins`."""

from types import SimpleNamespace

import pytest

from kohakuterrarium.studio.sessions import creature_plugins as plug_mod


class _FakePlugin:
    @classmethod
    def option_schema(cls):
        return {"k": {"type": "string"}}

    def __init__(self, options=None):
        self._options = options or {"k": "v"}

    def get_options(self):
        return dict(self._options)


class _FakeBadPlugin:
    @classmethod
    def option_schema(cls):
        raise RuntimeError("boom")

    def get_options(self):
        return {}


class _FakePluginManager:
    def __init__(self, plugins=None, enabled_map=None):
        self._plugins = plugins or {}
        self._enabled = enabled_map or {}
        self.disable_calls: list[str] = []
        self.enable_calls: list[str] = []
        self.load_pending_calls = 0

    def __bool__(self):
        return bool(self._plugins)

    def list_plugins(self):
        return [
            {"name": n, "enabled": self._enabled.get(n, True)} for n in self._plugins
        ]

    def list_plugins_with_options(self):
        return [
            {
                "name": n,
                "options": p.get_options() if hasattr(p, "get_options") else {},
            }
            for n, p in self._plugins.items()
        ]

    def get_plugin(self, name):
        return self._plugins.get(name)

    def is_enabled(self, name):
        return self._enabled.get(name, True)

    def disable(self, name):
        self.disable_calls.append(name)
        self._enabled[name] = False

    def enable(self, name):
        self.enable_calls.append(name)
        self._enabled[name] = True

    async def load_pending(self):
        self.load_pending_calls += 1


class _FakeHelper:
    def __init__(self):
        self.set_calls = []

    def set(self, name, values):
        self.set_calls.append((name, values))
        return values


def _agent(*, plugins=None, plugin_options=None):
    return SimpleNamespace(plugins=plugins, plugin_options=plugin_options)


def _creature(agent):
    return SimpleNamespace(agent=agent)


def _install_find(monkeypatch, agent):
    monkeypatch.setattr(
        plug_mod, "find_creature", lambda eng, sid, cid: _creature(agent)
    )


def _engine():
    return SimpleNamespace()


# ── list_plugins / plugin_inventory ───────────────────────────


class TestListPlugins:
    def test_no_plugins(self, monkeypatch):
        agent = _agent(plugins=None)
        _install_find(monkeypatch, agent)
        assert plug_mod.list_plugins(_engine(), "g", "c") == []

    def test_with_plugins(self, monkeypatch):
        mgr = _FakePluginManager({"permgate": _FakePlugin()})
        agent = _agent(plugins=mgr)
        _install_find(monkeypatch, agent)
        out = plug_mod.list_plugins(_engine(), "g", "c")
        assert out[0]["name"] == "permgate"


class TestPluginInventory:
    def test_no_plugins(self, monkeypatch):
        agent = _agent(plugins=None)
        _install_find(monkeypatch, agent)
        assert plug_mod.plugin_inventory(_engine(), "g", "c") == []

    def test_with_plugins(self, monkeypatch):
        mgr = _FakePluginManager({"permgate": _FakePlugin()})
        agent = _agent(plugins=mgr)
        _install_find(monkeypatch, agent)
        out = plug_mod.plugin_inventory(_engine(), "g", "c")
        assert out[0]["name"] == "permgate"


# ── get_plugin_options ────────────────────────────────────────


class TestGetPluginOptions:
    def test_no_plugins(self, monkeypatch):
        agent = _agent(plugins=None)
        _install_find(monkeypatch, agent)
        with pytest.raises(KeyError):
            plug_mod.get_plugin_options(_engine(), "g", "c", "permgate")

    def test_unknown_plugin(self, monkeypatch):
        mgr = _FakePluginManager({"other": _FakePlugin()})
        agent = _agent(plugins=mgr)
        _install_find(monkeypatch, agent)
        with pytest.raises(KeyError):
            plug_mod.get_plugin_options(_engine(), "g", "c", "ghost")

    def test_known(self, monkeypatch):
        mgr = _FakePluginManager({"permgate": _FakePlugin()})
        agent = _agent(plugins=mgr)
        _install_find(monkeypatch, agent)
        out = plug_mod.get_plugin_options(_engine(), "g", "c", "permgate")
        assert out["name"] == "permgate"
        # schema comes from the plugin's option_schema classmethod.
        assert out["schema"] == {"k": {"type": "string"}}
        # options come from the plugin instance's get_options().
        assert out["options"] == {"k": "v"}

    def test_schema_failure_returns_empty(self, monkeypatch):
        mgr = _FakePluginManager({"bad": _FakeBadPlugin()})
        agent = _agent(plugins=mgr)
        _install_find(monkeypatch, agent)
        out = plug_mod.get_plugin_options(_engine(), "g", "c", "bad")
        assert out["schema"] == {}


# ── set_plugin_options ────────────────────────────────────────


class TestSetPluginOptions:
    def test_no_helper(self, monkeypatch):
        agent = _agent()
        _install_find(monkeypatch, agent)
        with pytest.raises(ValueError, match="no plugin_options"):
            plug_mod.set_plugin_options(_engine(), "g", "c", "permgate", {})

    def test_success(self, monkeypatch):
        helper = _FakeHelper()
        agent = _agent(plugin_options=helper)
        _install_find(monkeypatch, agent)
        out = plug_mod.set_plugin_options(_engine(), "g", "c", "permgate", {"k": "v"})
        assert out == {"k": "v"}
        assert helper.set_calls == [("permgate", {"k": "v"})]


# ── toggle_plugin ─────────────────────────────────────────────


class TestTogglePlugin:
    async def test_no_plugins(self, monkeypatch):
        agent = _agent(plugins=None)
        _install_find(monkeypatch, agent)
        with pytest.raises(ValueError, match="No plugins"):
            await plug_mod.toggle_plugin(_engine(), "g", "c", "permgate")

    async def test_enable_path(self, monkeypatch):
        mgr = _FakePluginManager(
            {"permgate": _FakePlugin()}, enabled_map={"permgate": False}
        )
        agent = _agent(plugins=mgr)
        _install_find(monkeypatch, agent)
        out = await plug_mod.toggle_plugin(_engine(), "g", "c", "permgate")
        assert out == {"name": "permgate", "enabled": True}
        assert mgr.enable_calls == ["permgate"]
        assert mgr.load_pending_calls == 1

    async def test_disable_path(self, monkeypatch):
        mgr = _FakePluginManager(
            {"permgate": _FakePlugin()}, enabled_map={"permgate": True}
        )
        agent = _agent(plugins=mgr)
        _install_find(monkeypatch, agent)
        out = await plug_mod.toggle_plugin(_engine(), "g", "c", "permgate")
        assert out == {"name": "permgate", "enabled": False}
        assert mgr.disable_calls == ["permgate"]
