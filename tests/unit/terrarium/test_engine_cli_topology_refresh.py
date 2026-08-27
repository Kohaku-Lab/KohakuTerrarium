"""Regression tests for dynamic TUI topology refresh."""

from types import SimpleNamespace

from kohakuterrarium.terrarium import engine_cli_tabs


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
        self.models = []
        self.identities = []

    def set_terrarium_tabs(self, tabs):
        self.tabs = tabs

    def update_target_model(self, *args):
        self.models.append(args)

    def update_target_identity(self, *args):
        self.identities.append(args)


async def test_seed_tab_models_includes_config_identity():
    agent = SimpleNamespace(
        llm_identifier=lambda: "provider/model",
        compact_manager=None,
    )
    creature = SimpleNamespace(
        creature_id="worker",
        name="warm-ember",
        config_name="swe",
        config_ref="@kt-biome/creatures/swe",
        agent=agent,
    )
    tui = _TUI()

    engine_cli_tabs._seed_tab_models(tui, [creature])

    assert tui.models == [("worker", "provider/model", 0, 0)]
    assert tui.identities == [
        (
            "worker",
            "",
            "warm-ember",
            "swe",
            "@kt-biome/creatures/swe",
        )
    ]

    creature.agent.llm_identifier = lambda: ""
    tui.models.clear()
    tui.identities.clear()
    engine_cli_tabs._seed_tab_models(tui, [creature])
    assert tui.models == []
    assert tui.identities[0][2:] == (
        "warm-ember",
        "swe",
        "@kt-biome/creatures/swe",
    )


async def test_refresh_follows_focus_creature_after_graph_merge(monkeypatch):
    engine = _Engine()
    tui = _TUI()
    seen = []
    monkeypatch.setattr(engine_cli_tabs, "_seed_tab_models", lambda *_args: None)
    monkeypatch.setattr(engine_cli_tabs, "_wire_new_channels", lambda *_args: None)
    monkeypatch.setattr(
        engine_cli_tabs,
        "_update_terrarium_panel",
        lambda _tui, _creatures, env, focus: seen.append((env, focus)),
    )

    await engine_cli_tabs._refresh_tui_on_topology_change(
        engine,
        tui,
        "focus",
        set(),
        {"focus"},
    )

    assert tui.tabs == ["focus"]
    assert seen == [(engine._environments["merged"], "focus")]
