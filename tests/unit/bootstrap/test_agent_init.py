"""Unit tests for :mod:`kohakuterrarium.bootstrap.agent_init`.

``AgentInitMixin`` is a bag of ``_init_*`` / ``_create_*`` methods that
read ``self.<attr>`` and mutate ``self.<other>``. Each test builds a
minimal stand-in ``self`` (a ``SimpleNamespace`` carrying just the
attributes the method under test touches), invokes the unbound mixin
method against it, and asserts the resulting state mutation — the
documented contract of that method.
"""

from pathlib import Path
from types import SimpleNamespace


from kohakuterrarium.bootstrap.agent_init import AgentInitMixin
from kohakuterrarium.builtins.tool_catalog import get_builtin_tool
from kohakuterrarium.core.config_types import AgentConfig
from kohakuterrarium.core.executor import Executor
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.core.session import Session
from kohakuterrarium.modules.output.router import OutputRouter
from kohakuterrarium.parsing.format import BRACKET_FORMAT, XML_FORMAT
from kohakuterrarium.testing.output import OutputRecorder


class _FakeLLM:
    """Minimal LLM stand-in carrying the provider-identity attributes
    ``_drop_unsupported_provider_native_tools`` / ``_auto_inject`` read."""

    def __init__(self, provider_name="", native_tools=frozenset()):
        self.provider_name = provider_name
        self.provider_native_tools = native_tools
        self.model = "fake-model"


class _FakeAgent(AgentInitMixin):
    """Real ``AgentInitMixin`` subclass so cross-method calls resolve.

    Tests assign just the attributes the method under test reads; the
    mixin methods themselves are exercised unchanged.
    """

    def __init__(self, **attrs):
        for key, value in attrs.items():
            setattr(self, key, value)


# ── _init_llm ───────────────────────────────────────────────────


class TestInitLLM:
    def test_delegates_to_create_llm_provider(self, monkeypatch):
        from kohakuterrarium.bootstrap import agent_init as ai_mod

        built = object()
        captured = {}

        def fake_create(config, llm_override=None):
            captured["config"] = config
            captured["override"] = llm_override
            return built

        monkeypatch.setattr(ai_mod, "create_llm_provider", fake_create)
        cfg = AgentConfig(name="a")
        fake = SimpleNamespace(config=cfg, _llm_override="prof-x")
        AgentInitMixin._init_llm(fake)
        # The created provider is stored as self.llm and the override flows through.
        assert fake.llm is built
        assert captured["override"] == "prof-x"


# ── _drop_unsupported_provider_native_tools ─────────────────────


class TestDropUnsupportedProviderNativeTools:
    def test_drops_native_tool_unsupported_by_active_provider(self):
        registry = Registry()
        # image_gen is provider-native, supported only by 'codex'.
        registry.register_tool(get_builtin_tool("image_gen"))
        registry.register_tool(get_builtin_tool("bash"))
        fake = SimpleNamespace(registry=registry, llm=_FakeLLM(provider_name="openai"))
        AgentInitMixin._drop_unsupported_provider_native_tools(fake)
        # image_gen dropped (openai not in its provider_support); bash kept.
        assert "image_gen" not in registry.list_tools()
        assert "bash" in registry.list_tools()

    def test_keeps_native_tool_when_provider_supported(self):
        registry = Registry()
        registry.register_tool(get_builtin_tool("image_gen"))
        fake = SimpleNamespace(registry=registry, llm=_FakeLLM(provider_name="codex"))
        AgentInitMixin._drop_unsupported_provider_native_tools(fake)
        # codex IS in image_gen.provider_support → tool stays.
        assert "image_gen" in registry.list_tools()

    def test_non_native_tools_untouched_when_no_provider(self):
        registry = Registry()
        registry.register_tool(get_builtin_tool("bash"))
        fake = SimpleNamespace(registry=registry, llm=None)
        AgentInitMixin._drop_unsupported_provider_native_tools(fake)
        assert "bash" in registry.list_tools()


# ── _auto_inject_provider_native_tools ──────────────────────────


class TestAutoInjectProviderNativeTools:
    def test_injects_advertised_native_tool(self):
        registry = Registry()
        fake = SimpleNamespace(
            registry=registry,
            llm=_FakeLLM(provider_name="codex", native_tools=frozenset(["image_gen"])),
            config=AgentConfig(name="a"),
        )
        AgentInitMixin._auto_inject_provider_native_tools(fake)
        # The provider advertises image_gen → it's auto-registered.
        assert "image_gen" in registry.list_tools()

    def test_no_native_tools_is_noop(self):
        registry = Registry()
        fake = SimpleNamespace(
            registry=registry,
            llm=_FakeLLM(native_tools=frozenset()),
            config=AgentConfig(name="a"),
        )
        AgentInitMixin._auto_inject_provider_native_tools(fake)
        assert registry.list_tools() == []

    def test_disabled_tool_not_injected(self):
        registry = Registry()
        fake = SimpleNamespace(
            registry=registry,
            llm=_FakeLLM(native_tools=frozenset(["image_gen"])),
            config=AgentConfig(name="a", disable_provider_tools=["image_gen"]),
        )
        AgentInitMixin._auto_inject_provider_native_tools(fake)
        # Explicitly opted out → not registered.
        assert "image_gen" not in registry.list_tools()

    def test_already_registered_tool_left_as_is(self):
        registry = Registry()
        user_wired = get_builtin_tool("image_gen")
        registry.register_tool(user_wired)
        fake = SimpleNamespace(
            registry=registry,
            llm=_FakeLLM(native_tools=frozenset(["image_gen"])),
            config=AgentConfig(name="a"),
        )
        AgentInitMixin._auto_inject_provider_native_tools(fake)
        # User's own wiring is respected — same instance still there.
        assert registry.get_tool("image_gen") is user_wired

    def test_unknown_native_tool_skipped(self):
        registry = Registry()
        fake = SimpleNamespace(
            registry=registry,
            llm=_FakeLLM(native_tools=frozenset(["no_such_native_tool"])),
            config=AgentConfig(name="a"),
        )
        # A native tool name not in the catalog is skipped, not fatal.
        AgentInitMixin._auto_inject_provider_native_tools(fake)
        assert "no_such_native_tool" not in registry.list_tools()


# ── _init_registry ──────────────────────────────────────────────


class TestInitRegistry:
    def test_builds_registry_and_runs_native_tool_steps(self, monkeypatch):
        from kohakuterrarium.bootstrap import agent_init as ai_mod

        calls = []

        def fake_init_tools(cfg, reg, loader):
            calls.append("init_tools")
            # Wire a provider-native tool the active provider can't serve.
            reg.register_tool(get_builtin_tool("image_gen"))

        monkeypatch.setattr(ai_mod, "init_tools", fake_init_tools)
        fake = _FakeAgent(
            config=AgentConfig(name="a"),
            _loader=None,
            llm=_FakeLLM(provider_name="openai"),
        )
        AgentInitMixin._init_registry(fake)
        # A fresh Registry is created, init_tools wired config entries, and
        # the unsupported-provider-native drop step ran (image_gen gone).
        assert isinstance(fake.registry, Registry)
        assert calls == ["init_tools"]
        assert "image_gen" not in fake.registry.list_tools()


# ── _resolve_tool_format ────────────────────────────────────────


class TestResolveToolFormat:
    def test_bracket_string(self):
        fake = SimpleNamespace(config=AgentConfig(name="a", tool_format="bracket"))
        assert AgentInitMixin._resolve_tool_format(fake) is BRACKET_FORMAT

    def test_xml_string(self):
        fake = SimpleNamespace(config=AgentConfig(name="a", tool_format="xml"))
        assert AgentInitMixin._resolve_tool_format(fake) is XML_FORMAT

    def test_native_returns_none(self):
        fake = SimpleNamespace(config=AgentConfig(name="a", tool_format="native"))
        # Native mode bypasses the parser entirely.
        assert AgentInitMixin._resolve_tool_format(fake) is None

    def test_unknown_string_falls_back_to_bracket(self):
        fake = SimpleNamespace(config=AgentConfig(name="a", tool_format="bogus"))
        assert AgentInitMixin._resolve_tool_format(fake) is BRACKET_FORMAT

    def test_dict_builds_custom_format(self):
        cfg = AgentConfig(
            name="a",
            tool_format={"start_char": "<", "end_char": ">"},
        )
        fake = SimpleNamespace(config=cfg)
        fmt = AgentInitMixin._resolve_tool_format(fake)
        # A dict config produces a custom ToolCallFormat carrying its markers.
        assert fmt.start_char == "<"
        assert fmt.end_char == ">"

    def test_non_str_non_dict_falls_back_to_bracket(self):
        # A tool_format that is neither str nor dict (e.g. None) is
        # defensively coerced to the bracket default.
        cfg = AgentConfig(name="a")
        cfg.tool_format = None
        fake = SimpleNamespace(config=cfg)
        assert AgentInitMixin._resolve_tool_format(fake) is BRACKET_FORMAT


# ── _init_executor ──────────────────────────────────────────────


class TestInitExecutor:
    def test_wires_executor_session_and_working_dir(self):
        registry = Registry()
        registry.register_tool(get_builtin_tool("bash"))
        cfg = AgentConfig(name="exec-agent")
        fake = SimpleNamespace(
            registry=registry,
            config=cfg,
            _explicit_session=None,
            _explicit_pwd=None,
        )
        AgentInitMixin._init_executor(fake)
        # Executor created, tool mirrored, session derived from config name.
        assert isinstance(fake.executor, Executor)
        assert fake.executor.get_tool("bash") is not None
        assert fake.session.key == "exec-agent"
        assert fake.executor._agent_name == "exec-agent"
        # Working dir defaults to process cwd when no explicit pwd given.
        assert fake.executor._working_dir == Path.cwd()
        # Backward-compat accessors point at the session's state.
        assert fake.channel_registry is fake.session.channels
        assert fake.scratchpad is fake.session.scratchpad

    def test_explicit_pwd_resolved_onto_executor(self, tmp_path):
        registry = Registry()
        cfg = AgentConfig(name="a")
        fake = SimpleNamespace(
            registry=registry,
            config=cfg,
            _explicit_session=None,
            _explicit_pwd=str(tmp_path),
        )
        AgentInitMixin._init_executor(fake)
        # Explicit pwd from API/config wins over process cwd.
        assert fake.executor._working_dir == tmp_path.resolve()

    def test_explicit_session_used_directly(self):
        registry = Registry()
        cfg = AgentConfig(name="a")
        explicit = Session(key="my-explicit-session")
        fake = SimpleNamespace(
            registry=registry,
            config=cfg,
            _explicit_session=explicit,
            _explicit_pwd=None,
        )
        AgentInitMixin._init_executor(fake)
        # The injected session is used verbatim, not re-derived.
        assert fake.session is explicit

    def test_memory_path_resolved_from_agent_path(self, tmp_path):
        registry = Registry()
        cfg = AgentConfig(name="a", agent_path=tmp_path)
        cfg.memory = {"path": "mem"}
        fake = SimpleNamespace(
            registry=registry,
            config=cfg,
            _explicit_session=None,
            _explicit_pwd=None,
        )
        AgentInitMixin._init_executor(fake)
        # memory.path is resolved against the agent's config directory.
        assert fake.executor._memory_path == tmp_path / "mem"


# ── _init_subagents ─────────────────────────────────────────────


class TestInitSubagents:
    def test_builds_subagent_manager_wired_to_registry_and_executor(self, monkeypatch):
        from kohakuterrarium.bootstrap import agent_init as ai_mod
        from kohakuterrarium.modules.subagent import SubAgentManager

        captured = {}

        def fake_init_subagents(config, mgr, registry, loader):
            captured["mgr"] = mgr
            captured["registry"] = registry

        monkeypatch.setattr(ai_mod, "init_subagents", fake_init_subagents)
        registry = Registry()
        executor = Executor()
        fake = _FakeAgent(
            config=AgentConfig(name="a"),
            registry=registry,
            executor=executor,
            llm=_FakeLLM(),
            _loader=None,
        )
        AgentInitMixin._init_subagents(fake)
        # A SubAgentManager is created, shares the job store, and inherits
        # the parent executor for tool-context building.
        assert isinstance(fake.subagent_manager, SubAgentManager)
        assert fake.subagent_manager._parent_executor is executor
        assert captured["registry"] is registry


# ── _init_input / _init_output ──────────────────────────────────


class TestInitInputOutput:
    def test_init_input_stores_module(self):
        from kohakuterrarium.builtins.inputs.cli import CLIInput

        cfg = AgentConfig(name="a")
        fake = SimpleNamespace(config=cfg, _loader=None)
        AgentInitMixin._init_input(fake, None)
        assert isinstance(fake.input, CLIInput)

    def test_init_output_builds_router_and_known_outputs(self):
        cfg = AgentConfig(name="a")
        fake = SimpleNamespace(config=cfg, _loader=None)
        AgentInitMixin._init_output(fake, None)
        # An OutputRouter is constructed; no named outputs declared → empty set.
        assert isinstance(fake.output_router, OutputRouter)
        assert fake._known_outputs == set()

    def test_init_output_uses_custom_override(self):
        cfg = AgentConfig(name="a")
        recorder = OutputRecorder()
        fake = SimpleNamespace(config=cfg, _loader=None)
        AgentInitMixin._init_output(fake, recorder)
        # The override becomes the router's default output.
        assert fake.output_router.default_output is recorder


# ── _ensure_skill_tool_registered ───────────────────────────────


class TestEnsureSkillToolRegistered:
    def test_registers_skill_tool_into_registry_and_executor(self):
        registry = Registry()
        executor = Executor()
        fake = SimpleNamespace(registry=registry, executor=executor)
        AgentInitMixin._ensure_skill_tool_registered(fake)
        # The skill tool lands in both registry and executor.
        assert registry.get_tool("skill") is not None
        assert executor.get_tool("skill") is not None

    def test_idempotent_when_already_registered(self):
        registry = Registry()
        first = get_builtin_tool("skill")
        registry.register_tool(first)
        fake = SimpleNamespace(registry=registry, executor=None)
        AgentInitMixin._ensure_skill_tool_registered(fake)
        # Already present → not replaced.
        assert registry.get_tool("skill") is first


# ── _init_skills ────────────────────────────────────────────────


class TestInitSkills:
    def test_builds_registry_and_mirrors_onto_session(self, tmp_path):
        from kohakuterrarium.skills import SkillRegistry

        session = Session(key="skill-sess")
        cfg = AgentConfig(name="a")
        executor = Executor()
        executor._working_dir = tmp_path
        fake = SimpleNamespace(
            config=cfg,
            executor=executor,
            session=session,
            scratchpad=session.scratchpad,
        )
        AgentInitMixin._init_skills(fake)
        # A SkillRegistry is created and mirrored onto session.extra so
        # plugins / studio routes can reach it without an agent ref.
        assert isinstance(fake.skills, SkillRegistry)
        assert session.extra["skills_registry"] is fake.skills

    def test_discovery_failure_yields_empty_registry(self, tmp_path, monkeypatch):
        from kohakuterrarium.bootstrap import agent_init as ai_mod

        def boom(**kw):
            raise RuntimeError("scan blew up")

        monkeypatch.setattr(ai_mod, "discover_skills", boom)
        session = Session(key="s2")
        executor = Executor()
        executor._working_dir = tmp_path
        fake = SimpleNamespace(
            config=AgentConfig(name="a"),
            executor=executor,
            session=session,
            scratchpad=session.scratchpad,
        )
        AgentInitMixin._init_skills(fake)
        # Discovery failure is swallowed — registry still exists, just empty.
        assert len(fake.skills) == 0


# ── _init_triggers ──────────────────────────────────────────────


class TestInitTriggers:
    def test_delegates_to_init_triggers_factory(self, monkeypatch):
        from kohakuterrarium.bootstrap import agent_init as ai_mod

        captured = {}

        def fake_init(config, tm, session, loader):
            captured["tm"] = tm
            captured["session"] = session

        monkeypatch.setattr(ai_mod, "init_triggers", fake_init)
        tm = object()
        session = Session(key="trig")
        fake = SimpleNamespace(
            config=AgentConfig(name="a"),
            trigger_manager=tm,
            session=session,
            _loader=None,
        )
        AgentInitMixin._init_triggers(fake)
        # The trigger manager and session are forwarded to the factory.
        assert captured["tm"] is tm
        assert captured["session"] is session


# ── _prepare_injected_input ─────────────────────────────────────


def _agent_with_slash_result(result, output_router=None):
    """An _FakeAgent whose slash dispatcher returns a fixed result."""
    agent = _FakeAgent(output_router=output_router)

    async def _stub(text):
        return result

    agent._try_slash_command_text = _stub
    return agent


class TestPrepareInjectedInput:
    async def test_non_slash_content_passes_through(self):
        fake = _FakeAgent()
        result = await AgentInitMixin._prepare_injected_input(fake, "plain text", "src")
        assert result == "plain text"

    async def test_non_string_content_passes_through(self):
        fake = _FakeAgent()
        payload = {"parts": ["x"]}
        result = await AgentInitMixin._prepare_injected_input(fake, payload, "src")
        assert result is payload

    async def test_slash_command_with_no_result_returns_original(self):
        # Slash dispatcher returns None → original text flows through.
        fake = _agent_with_slash_result(None)
        result = await AgentInitMixin._prepare_injected_input(fake, "/unknown", "src")
        assert result == "/unknown"

    async def test_slash_command_error_notifies_and_returns_none(self):
        from kohakuterrarium.modules.user_command.base import UserCommandResult

        notes = []
        router = SimpleNamespace(
            notify_activity=lambda kind, msg, metadata=None: notes.append((kind, msg))
        )
        fake = _agent_with_slash_result(
            UserCommandResult(error="bad command"), output_router=router
        )
        result = await AgentInitMixin._prepare_injected_input(fake, "/bad", "src")
        # Error → consumed (None) and the router was notified.
        assert result is None
        assert notes == [("command_error", "bad command")]

    async def test_consumed_command_with_output_notifies(self):
        from kohakuterrarium.modules.user_command.base import UserCommandResult

        notes = []
        router = SimpleNamespace(
            notify_activity=lambda kind, msg, metadata=None: notes.append((kind, msg))
        )
        fake = _agent_with_slash_result(
            UserCommandResult(output="done", consumed=True), output_router=router
        )
        result = await AgentInitMixin._prepare_injected_input(fake, "/ok", "src")
        # consumed → None, and the command result was surfaced.
        assert result is None
        assert notes == [("command_result", "done")]

    async def test_non_consumed_command_output_replaces_input(self):
        from kohakuterrarium.modules.user_command.base import UserCommandResult

        fake = _agent_with_slash_result(
            UserCommandResult(output="expanded text", consumed=False)
        )
        result = await AgentInitMixin._prepare_injected_input(fake, "/skill", "src")
        # Not consumed → the command's output becomes the new input turn.
        assert result == "expanded text"


# ── _try_slash_command_text ─────────────────────────────────────


class TestTrySlashCommandText:
    async def test_delegates_to_input_module_when_supported(self):
        class _Input:
            async def try_user_command(self, text):
                return f"handled:{text}"

        fake = SimpleNamespace(input=_Input())
        result = await AgentInitMixin._try_slash_command_text(fake, "/x")
        assert result == "handled:/x"

    async def test_unknown_command_without_skill_registry_returns_none(self):
        # No input module, no skills registry, unknown command → None.
        fake = SimpleNamespace(input=None, session=None, skills=None)
        result = await AgentInitMixin._try_slash_command_text(
            fake, "/definitely_unknown_cmd"
        )
        assert result is None

    async def test_disabled_skill_returns_error_result(self, tmp_path):
        from kohakuterrarium.skills import Skill, SkillRegistry

        registry = SkillRegistry()
        skill = Skill(
            name="deploy",
            description="d",
            body="how to deploy",
            base_dir=tmp_path,
            origin="test",
            enabled=False,
        )
        registry.add(skill)
        fake = SimpleNamespace(input=None, session=None, skills=registry)
        result = await AgentInitMixin._try_slash_command_text(fake, "/deploy")
        # A disabled skill yields an error result, not a silent None.
        assert result is not None
        assert "disabled" in result.error

    async def test_enabled_skill_returns_unconsumed_turn(self, tmp_path):
        from kohakuterrarium.skills import Skill, SkillRegistry

        registry = SkillRegistry()
        skill = Skill(
            name="review",
            description="d",
            body="how to review code",
            base_dir=tmp_path,
            origin="test",
            enabled=True,
        )
        registry.add(skill)
        fake = SimpleNamespace(input=None, session=None, skills=registry)
        result = await AgentInitMixin._try_slash_command_text(fake, "/review args")
        # An enabled skill turns into an UNconsumed turn (it feeds the LLM).
        assert result is not None
        assert result.consumed is False
        assert result.output

    async def test_unknown_command_with_skill_registry_returns_none(self, tmp_path):
        from kohakuterrarium.skills import SkillRegistry

        registry = SkillRegistry()
        fake = SimpleNamespace(input=None, session=None, skills=registry)
        # A command that is neither a builtin nor a known skill → None.
        result = await AgentInitMixin._try_slash_command_text(
            fake, "/no_such_command_at_all"
        )
        assert result is None

    async def test_builtin_slash_command_executed(self):
        # With no input module, builtin user commands are collected and
        # the matching one (here /help) is executed directly.
        fake = SimpleNamespace(input=None, session=None, skills=None)
        result = await AgentInitMixin._try_slash_command_text(fake, "/help")
        # /help is a real builtin — it returns a command result, not None.
        assert result is not None


# ── _ensure_skill_tool_registered fallback ──────────────────────


class TestEnsureSkillToolFallback:
    def test_falls_back_to_skilltool_when_catalog_misses(self, monkeypatch):
        from kohakuterrarium.bootstrap import agent_init as ai_mod

        # Force the catalog lookup to miss so the SkillTool() fallback runs.
        monkeypatch.setattr(ai_mod, "get_builtin_tool", lambda name: None)
        registry = Registry()
        fake = SimpleNamespace(registry=registry, executor=None)
        AgentInitMixin._ensure_skill_tool_registered(fake)
        # The direct SkillTool() construction path registered a skill tool.
        assert registry.get_tool("skill") is not None

    def test_skilltool_construction_failure_is_swallowed(self, monkeypatch):
        from kohakuterrarium.bootstrap import agent_init as ai_mod

        # Catalog misses AND the SkillTool() fallback itself raises.
        monkeypatch.setattr(ai_mod, "get_builtin_tool", lambda name: None)

        def boom():
            raise RuntimeError("skill tool broken")

        monkeypatch.setattr(ai_mod, "SkillTool", boom)
        registry = Registry()
        fake = SimpleNamespace(registry=registry, executor=None)
        # The exception is caught + logged — no crash, no registration.
        AgentInitMixin._ensure_skill_tool_registered(fake)
        assert registry.get_tool("skill") is None


# ── _init_controller / _create_controller ───────────────────────


class TestInitController:
    def _agent(self, **overrides):
        registry = Registry()
        registry.register_tool(get_builtin_tool("bash"))
        executor = Executor()
        executor._working_dir = Path.cwd()
        defaults = dict(
            config=AgentConfig(name="ctrl-agent"),
            registry=registry,
            executor=executor,
            llm=_FakeLLM(),
            subagent_manager=SimpleNamespace(get_subagents_prompt=lambda: ""),
            _known_outputs=set(),
        )
        defaults.update(overrides)
        return _FakeAgent(**defaults)

    def test_builds_controller_and_controller_config(self):
        agent = self._agent()
        AgentInitMixin._init_controller(agent)
        # A primary controller is created and a reusable ControllerConfig
        # carrying the aggregated system prompt is stored.
        assert agent.controller is not None
        assert agent._controller_config.system_prompt
        # The aggregated prompt embeds the agent's base personality text.
        assert "helpful assistant" in agent._controller_config.system_prompt.lower()

    def test_subagents_prompt_appended_to_base(self):
        agent = self._agent(
            subagent_manager=SimpleNamespace(
                get_subagents_prompt=lambda: "SUBAGENT-SECTION-MARKER"
            )
        )
        AgentInitMixin._init_controller(agent)
        assert "SUBAGENT-SECTION-MARKER" in agent._controller_config.system_prompt

    def test_create_controller_produces_independent_instance(self):
        agent = self._agent()
        AgentInitMixin._init_controller(agent)
        second = AgentInitMixin._create_controller(agent)
        # _create_controller yields a fresh controller (parallel-mode use),
        # distinct from the primary one.
        assert second is not agent.controller
        assert type(second) is type(agent.controller)

    def test_plugins_wired_onto_controller(self):
        from kohakuterrarium.modules.plugin.base import BasePlugin
        from kohakuterrarium.modules.plugin.manager import PluginManager

        manager = PluginManager()
        plugin = BasePlugin()
        plugin.name = "p1"
        manager.register(plugin)

        applied = []
        agent = self._agent(plugins=manager)
        # _apply_plugin_hooks lives on another mixin — stub it on the instance.
        agent._apply_plugin_hooks = lambda: applied.append(True)
        AgentInitMixin._init_controller(agent)
        # When the agent has plugins, they're attached to the controller and
        # the hook-application step runs.
        assert agent.controller.plugins is agent.plugins
        assert applied == [True]

    def test_skill_registry_wires_skill_command_into_controller(self):
        from kohakuterrarium.skills import SkillRegistry

        agent = self._agent(skills=SkillRegistry())
        AgentInitMixin._init_controller(agent)
        # A skill registry registers the "skill" command on the controller
        # and exposes the skill tool in the registry.
        assert agent.registry.get_tool("skill") is not None

    def test_create_controller_carries_output_router(self):
        router = OutputRouter(default_output=OutputRecorder())
        agent = self._agent(output_router=router)
        AgentInitMixin._init_controller(agent)
        fresh = AgentInitMixin._create_controller(agent)
        # A freshly-created controller inherits the agent's output router.
        assert fresh.output_router is router


# ── _init_user_commands ─────────────────────────────────────────


class TestInitUserCommands:
    def test_wires_commands_into_input_module(self):
        wired = {}

        class _Input:
            def set_user_commands(self, commands, context):
                wired["commands"] = commands
                wired["context"] = context

        fake = _FakeAgent(input=_Input(), session=None)
        AgentInitMixin._init_user_commands(fake)
        # Builtin slash commands are collected and pushed into the input
        # module via set_user_commands.
        assert wired["commands"]
        assert "clear" in wired["commands"]

    def test_input_without_command_support_is_noop(self):
        # An input module lacking set_user_commands must not crash.
        fake = _FakeAgent(input=SimpleNamespace(), session=None)
        AgentInitMixin._init_user_commands(fake)
