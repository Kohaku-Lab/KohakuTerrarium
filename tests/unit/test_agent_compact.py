from kohakuterrarium.core.agent_compact import AgentCompactMixin


class _DummyAgent(AgentCompactMixin):
    def __init__(self):
        self.config = type(
            "Config",
            (),
            {
                "name": "dummy",
                "llm_profile": "",
                "model": "gpt-5.4",
                "provider": "openai",
                "variation_selections": {},
            },
        )()
        self._llm_override = None
        self.llm = object()


class TestBuildCompactLlm:
    def test_resolves_inline_model_to_dedicated_profile(self, monkeypatch):
        agent = _DummyAgent()
        captured = {}

        class _Profile:
            provider = "openai"
            name = "gpt-5.4"
            selected_variations = {}

        def fake_resolve(controller_data, llm_override=None):
            captured["controller_data"] = controller_data
            captured["llm_override"] = llm_override
            return _Profile()

        built = object()

        def fake_create(name):
            captured["profile_name"] = name
            return built

        monkeypatch.setattr(
            "kohakuterrarium.core.agent_compact.resolve_controller_llm",
            fake_resolve,
        )
        monkeypatch.setattr(
            "kohakuterrarium.core.agent_compact.create_llm_from_profile_name",
            fake_create,
        )

        compact_llm = agent._build_compact_llm(
            type("Cfg", (), {"compact_model": None})()
        )

        assert compact_llm is built
        assert captured["controller_data"] == {
            "model": "gpt-5.4",
            "provider": "openai",
        }
        assert captured["llm_override"] is None
        assert captured["profile_name"] == "openai/gpt-5.4"

    def test_falls_back_to_active_llm_when_resolution_fails(self, monkeypatch):
        agent = _DummyAgent()

        monkeypatch.setattr(
            "kohakuterrarium.core.agent_compact.resolve_controller_llm",
            lambda controller_data, llm_override=None: None,
        )

        compact_llm = agent._build_compact_llm(
            type("Cfg", (), {"compact_model": None})()
        )

        assert compact_llm is agent.llm
