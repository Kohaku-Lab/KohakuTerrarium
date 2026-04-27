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


class TestAgentMRO:
    """Regression for the silent MRO collision that caused
    ``compact_manager._llm = None`` and the ``"No LLM available for
    compaction"`` user report.

    A sibling mixin (``AgentModelMixin``) once declared a
    ``def _build_compact_llm(self, compact_cfg) -> Any: ...`` stub
    "for type checkers". Because ``...`` is the ``Ellipsis`` literal
    and *not* a NotImplementedError, the function silently returned
    ``None``. ``AgentModelMixin`` sits before ``AgentCompactMixin``
    in ``Agent.__mro__``, so its stub shadowed the real implementation
    and every standard agent ended up with no compact LLM.

    These tests pin two invariants:

    1. ``Agent._build_compact_llm`` resolves to ``AgentCompactMixin``
       (not any other mixin).
    2. A freshly constructed ``Agent`` ends up with a non-None
       ``compact_manager._llm`` after ``_init_compact_manager``.
    """

    def test_method_resolves_to_compact_mixin(self):
        from kohakuterrarium.core.agent import Agent

        method = Agent._build_compact_llm
        assert method.__qualname__ == "AgentCompactMixin._build_compact_llm", (
            f"Agent._build_compact_llm resolved to {method.__qualname__!r}; "
            "expected AgentCompactMixin._build_compact_llm. A sibling mixin "
            "is shadowing the real implementation via MRO."
        )

    def test_init_compact_manager_binds_real_llm(self, monkeypatch):
        """End-to-end: building an Agent and running
        ``_init_compact_manager`` must populate ``_llm`` with the
        agent's active provider, never ``None``."""
        # Stub out the api-key check so we can construct the provider
        # without a real OPENAI_API_KEY in the environment.
        monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")

        from kohakuterrarium.core.agent import Agent
        from kohakuterrarium.core.config import AgentConfig

        config = AgentConfig(name="test", model="gpt-4o", provider="openai")
        agent = Agent(config=config)
        assert agent.llm is not None, "agent.llm not initialized"

        # ``_init_compact_manager`` is normally called inside ``start()``
        # but is itself a synchronous, idempotent helper.
        agent._init_compact_manager()

        assert agent.compact_manager is not None
        assert agent.compact_manager._llm is not None, (
            "compact_manager._llm is None after init — MRO shadow bug. "
            "The compact path will report 'No LLM available for compaction' "
            "when the user clicks the compact button."
        )
