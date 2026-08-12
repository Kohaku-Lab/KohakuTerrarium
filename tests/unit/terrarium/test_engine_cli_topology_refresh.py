"""Regression tests for dynamic TUI topology refresh."""

from types import SimpleNamespace

from kohakuterrarium.terrarium import engine_cli


class _Engine:
    def __init__(self):
        graph = SimpleNamespace(creature_ids={"focus"}, channels={})
        self._topology = SimpleNamespace(
            creature_to_graph={"focus": "merged"},
            graphs={"merged": graph},
        )
        self._environments = {"merged": object()}
        self.creature = SimpleNamespace(creature_id="focus")

    async def subscribe(self, _filter):
        yield object()

    def get_creature(self, creature_id):
        assert creature_id == "focus"
        return self.creature


class _TUI:
    def __init__(self):
        self.tabs = []

    def set_terrarium_tabs(self, tabs):
        self.tabs = tabs


async def test_refresh_follows_focus_creature_after_graph_merge(monkeypatch):
    engine = _Engine()
    tui = _TUI()
    seen = []
    monkeypatch.setattr(engine_cli, "_seed_tab_models", lambda *_args: None)
    monkeypatch.setattr(engine_cli, "_wire_new_channels", lambda *_args: None)
    monkeypatch.setattr(
        engine_cli,
        "_update_terrarium_panel",
        lambda _tui, _creatures, env, focus: seen.append((env, focus)),
    )

    await engine_cli._refresh_tui_on_topology_change(
        engine,
        tui,
        "focus",
        set(),
        {"focus"},
    )

    assert tui.tabs == ["focus"]
    assert seen == [(engine._environments["merged"], "focus")]
