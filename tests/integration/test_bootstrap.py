"""Integration test for the ``bootstrap/`` package.

``bootstrap/`` is the set of agent-initialization factories:
``llm`` / ``tools`` / ``subagents`` / ``triggers`` / ``io`` / ``plugins``
plus the ``AgentInitMixin`` (``agent_init``) that the real ``Agent``
class mixes in. Nobody calls these factories piecemeal — the canonical
consumer is ``Agent.__init__``, which runs the ``_init_*`` mixin methods
in a fixed order, and ``Agent.from_path`` which feeds them a config
loaded off disk.

So this test drives bootstrap the way the codebase does: it writes a
real on-disk creature config dir (``config.yaml`` + ``system.md`` +,
for the rich variant, a ``custom/`` Python module), calls
``Agent.from_path`` / ``Agent(config)``, and then asserts every factory
produced a real, wired component — and that the wiring is *live* by
running an actual turn through a ``ScriptedLLM``.

The only seam is the LLM provider: ``bootstrap.llm.create_llm_provider``
(and the re-import in ``bootstrap.agent_init``) are monkeypatched to
return a ``ScriptedLLM``. Everything else — registry, executor, plugin
manager, sub-agent manager, output router, controller, input module,
trigger manager — is the real collaborator.

Each ``Test*`` method is one complete workflow, never a shape check.
"""

import textwrap
from pathlib import Path

import pytest

from kohakuterrarium.bootstrap import agent_init as _agent_init
from kohakuterrarium.bootstrap import llm as _bootstrap_llm
from kohakuterrarium.bootstrap.io import create_input, create_output
from kohakuterrarium.builtins.outputs import StdoutOutput
from kohakuterrarium.bootstrap.llm import (
    _create_from_inline,
    _extract_controller_data,
    _is_meaningful_config_value,
    create_llm_from_profile_name,
    create_llm_provider,
)
from kohakuterrarium.bootstrap.subagents import create_subagent_config
from kohakuterrarium.bootstrap.tools import create_tool
from kohakuterrarium.bootstrap.triggers import create_trigger
from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.config import build_agent_config
from kohakuterrarium.core.config_types import (
    SubAgentConfigItem,
    ToolConfigItem,
    TriggerConfig,
)
from kohakuterrarium.core.events import create_user_input_event
from kohakuterrarium.core.loader import ModuleLoader
from kohakuterrarium.modules.plugin.base import BasePlugin
from kohakuterrarium.modules.trigger.callable import CallableTriggerTool
from kohakuterrarium.modules.trigger.channel import ChannelTrigger
from kohakuterrarium.modules.trigger.context import ContextUpdateTrigger
from kohakuterrarium.modules.trigger.timer import TimerTrigger
from kohakuterrarium.testing.llm import ScriptedLLM, ScriptEntry

# ── the only seam: the LLM provider factory ──────────────────────


@pytest.fixture
def scripted_llm(monkeypatch):
    """Patch both import sites of ``create_llm_provider`` to a ScriptedLLM.

    ``bootstrap.agent_init`` does ``from bootstrap.llm import
    create_llm_provider`` at module load, so patching only
    ``bootstrap.llm`` would miss the binding the mixin actually calls.
    A holder object lets each test set its own script before
    constructing the Agent.
    """

    class _Holder:
        def __init__(self) -> None:
            self.script: list = ["OK"]
            self.built: list[ScriptedLLM] = []

        def set_script(self, script: list) -> None:
            self.script = script

    holder = _Holder()

    def _fake_create(config, llm_override=None):
        llm = ScriptedLLM(holder.script)
        holder.built.append(llm)
        return llm

    monkeypatch.setattr(_bootstrap_llm, "create_llm_provider", _fake_create)
    monkeypatch.setattr(_agent_init, "create_llm_provider", _fake_create)
    return holder


# ── on-disk creature config builders ─────────────────────────────


def _write_minimal_creature(root: Path) -> Path:
    """A bare creature: no tools, no plugins, no sub-agents, no triggers.

    Exercises the happy path where each factory still has to produce a
    *real* component even when the config declares nothing for it.
    """
    config_dir = root / "minimal_creature"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        textwrap.dedent("""\
            name: minimal
            version: "1.0"
            controller:
              model: gpt-4-test
              api_key_env: ""
              tool_format: bracket
              include_tools_in_prompt: true
              include_hints_in_prompt: false
            system_prompt_file: system.md
            input:
              type: none
            output:
              type: stdout
            """),
        encoding="utf-8",
    )
    (config_dir / "system.md").write_text(
        "You are the minimal test creature.\n", encoding="utf-8"
    )
    return config_dir


def _write_rich_creature(root: Path) -> Path:
    """A fully-loaded creature: custom tool + custom plugin (from a
    ``custom/`` Python module), builtin tools, a builtin sub-agent, a
    timer trigger, and an ``llm_profile`` reference.

    Every ``bootstrap`` factory has real work to do here.
    """
    config_dir = root / "rich_creature"
    config_dir.mkdir()
    custom_dir = config_dir / "custom"
    custom_dir.mkdir()

    # A custom tool + a custom plugin, loaded via ModuleLoader from a
    # file inside the creature folder — the ``type: custom`` path.
    (custom_dir / "ext.py").write_text(
        textwrap.dedent('''\
            """Custom tool + plugin for the bootstrap integration test."""

            from kohakuterrarium.modules.tool.base import (
                BaseTool,
                ExecutionMode,
                ToolResult,
            )
            from kohakuterrarium.modules.plugin.base import BasePlugin


            class StampTool(BaseTool):
                """Echoes its ``text`` arg back, prefixed."""

                @property
                def tool_name(self) -> str:
                    return "stamp"

                @property
                def description(self) -> str:
                    return "Stamp and echo the given text."

                @property
                def execution_mode(self) -> ExecutionMode:
                    return ExecutionMode.DIRECT

                async def _execute(self, args, **kwargs) -> ToolResult:
                    return ToolResult(output="STAMPED:" + str(args.get("text", "")))


            class TagPlugin(BasePlugin):
                """Appends a marker to every assistant turn via post_llm_call."""

                name = "tagplugin"
                priority = 50

                def __init__(self) -> None:
                    super().__init__()
                    self.calls = 0

                async def post_llm_call(
                    self, messages, response, usage, **kwargs
                ) -> str:
                    self.calls += 1
                    return response + " [tagged]"
            '''),
        encoding="utf-8",
    )

    # A custom trigger class + a custom sub-agent config object, both
    # loaded via ModuleLoader from the creature folder — the
    # ``type: custom`` trigger / sub-agent module paths.
    (custom_dir / "extras.py").write_text(
        textwrap.dedent('''\
            """Custom trigger + sub-agent config for the bootstrap test."""

            import asyncio

            from kohakuterrarium.modules.trigger.base import BaseTrigger
            from kohakuterrarium.modules.subagent.config import SubAgentConfig


            class PulseTrigger(BaseTrigger):
                """A trivial custom trigger — only needs to construct.

                The ModuleLoader instantiates custom modules as
                ``cls(**options)``, so the constructor takes the option
                keys (``prompt`` + ``beats``) directly as kwargs. The
                trigger never actually fires (it blocks forever) so the
                trigger manager's run-loop stays parked.
                """

                def __init__(self, prompt="", beats=1, **kwargs):
                    super().__init__()
                    self.prompt = prompt
                    self.beats = beats

                async def wait_for_trigger(self):
                    await asyncio.Event().wait()
                    return None


            # An importable SubAgentConfig object — the ``custom`` sub-agent
            # path with ``module`` + ``config_name``.
            scout_config = SubAgentConfig(
                name="scout",
                description="a module-defined scout sub-agent",
                system_prompt="You are the scout.",
            )
            '''),
        encoding="utf-8",
    )

    (config_dir / "config.yaml").write_text(
        textwrap.dedent("""\
            name: rich
            version: "1.0"
            controller:
              llm: openai/gpt-4-bootstrap-test
              tool_format: bracket
              include_tools_in_prompt: true
              include_hints_in_prompt: false
            system_prompt_file: system.md
            input:
              type: none
            output:
              type: stdout
              named_outputs:
                report:
                  type: stdout
            tools:
              - name: read
                type: builtin
              - name: write
                type: builtin
              - name: bash
                type: builtin
                timeout: "45"
                max_output: "2048"
              - name: add_timer
                type: trigger
              - name: stamp
                type: custom
                module: ./custom/ext.py
                class: StampTool
            subagents:
              - name: research
                type: builtin
              - name: critic
                type: builtin
                extra_prompt: "Be extra harsh."
              - name: scout
                type: custom
                module: ./custom/extras.py
                config: scout_config
            triggers:
              - name: heartbeat
                type: timer
                prompt: "tick"
                interval: 3600
              - name: ctx-watch
                type: context
                prompt: "context changed"
                debounce_ms: 250
              - name: chan-watch
                type: channel
                prompt: "channel message"
                channel: ops
              - name: pulse
                type: custom
                module: ./custom/extras.py
                class: PulseTrigger
                prompt: "pulse fired"
                beats: 3
            plugins:
              - name: tagplugin
                type: custom
                module: ./custom/ext.py
                class: TagPlugin
              - name: budget
                type: builtin
            """),
        encoding="utf-8",
    )
    (config_dir / "system.md").write_text(
        "You are the rich test creature.\n", encoding="utf-8"
    )
    return config_dir


# ── workflow 1: minimal creature, full build + live turn ─────────


class TestBootstrapIntegration:
    async def test_minimal_creature_builds_and_runs_a_turn(
        self, scripted_llm, tmp_path
    ):
        """``Agent.from_path`` on a bare creature: every bootstrap factory
        still yields a real, wired component, and a turn flows through.
        """
        scripted_llm.set_script(["Hello from the minimal creature."])
        config_dir = _write_minimal_creature(tmp_path)

        agent = Agent.from_path(str(config_dir))

        # bootstrap.llm -> the scripted provider is the one the mixin bound.
        assert isinstance(agent.llm, ScriptedLLM)
        assert agent.llm is scripted_llm.built[-1]

        # bootstrap.tools -> registry built. Config declared no tools, so
        # the only entry is ``skill``, auto-registered by _init_controller
        # because skill discovery always produces a registry.
        assert agent.registry.list_tools() == ["skill"]

        # _init_executor -> executor + session wired off the registry.
        assert agent.executor is not None
        assert agent.executor._agent is agent
        assert agent.executor._agent_name == "minimal"
        assert agent.session is not None

        # bootstrap.plugins -> a real PluginManager exists even with no
        # config plugins (catalog plugins land registered-but-disabled).
        assert agent.plugins is not None
        plugin_names = {p["name"] for p in agent.plugins.list_plugins()}
        assert "tagplugin" not in plugin_names

        # bootstrap.subagents -> manager built, no sub-agents registered.
        assert agent.subagent_manager.list_subagents() == []

        # _init_output -> router with a real default output module.
        assert agent.output_router is not None
        assert agent.output_router.default_output is not None

        # _init_controller -> controller wired to the same llm + registry,
        # system prompt assembled from system.md.
        assert agent.controller is not None
        assert agent.controller.llm is agent.llm
        assert "minimal test creature" in agent.get_system_prompt()

        # _init_input -> NoneInput (config said ``type: none``).
        assert type(agent.input).__name__ == "NoneInput"

        # _init_triggers -> manager built, nothing registered.
        assert agent.trigger_manager.list() == []

        # Now prove the wiring is LIVE: run a real turn end-to-end.
        await agent.start()
        try:
            await agent._process_event(create_user_input_event("hi there"))
        finally:
            await agent.stop()

        assert agent.llm.call_count == 1
        # The user input reached the LLM through the controller.
        assert "hi there" in agent.llm.last_user_message
        # The scripted assistant text landed in the conversation.
        history = agent.conversation_history
        assert any(
            m.get("role") == "assistant"
            and "minimal creature" in str(m.get("content", ""))
            for m in history
        )

        # ── bootstrap.llm pure-logic + error paths. The provider
        #    constructors need a live backend, but the config-extraction
        #    helpers and the "nothing configured" guards are pure and
        #    are what protect a mis-typed creature config. Drive them
        #    directly against the real (un-patched) factory functions. ──
        cfg = agent.config
        # An empty model string is NOT a meaningful override (it equals
        # the dataclass default), a non-empty one IS.
        assert _is_meaningful_config_value("model", "") is False
        assert _is_meaningful_config_value("model", None) is False
        assert _is_meaningful_config_value("model", "gpt-4-real") is True
        assert _is_meaningful_config_value("extra_body", {}) is False
        assert _is_meaningful_config_value("extra_body", {"k": 1}) is True
        # The minimal config pins ``model: gpt-4-test`` inline — that is
        # a meaningful override and surfaces in the extracted controller
        # data the profile resolver consumes.
        assert _extract_controller_data(cfg) == {"model": "gpt-4-test"}
        # The inline factory needs an API key for a model with no
        # auth_mode; the bare creature provides none, so it raises a
        # clear ValueError instead of bricking the agent later.
        with pytest.raises(ValueError, match="API key not found"):
            _create_from_inline(cfg)
        # A config with NO model at all hits the earlier guard.
        no_model = build_agent_config(
            {
                "name": "no-model",
                "controller": {"api_key_env": ""},
                "input": {"type": "none"},
                "output": {"type": "stdout"},
            },
            config_dir,
        )
        with pytest.raises(ValueError, match="No LLM model configured"):
            _create_from_inline(no_model)
        # Resolving a profile name that does not exist is a hard error.
        with pytest.raises(ValueError, match="Model profile not found"):
            create_llm_from_profile_name("no-such-profile-xyz")
        # The REAL ``create_llm_provider`` entry point (un-patched): a
        # creature with an inline model and no profile reference falls
        # straight through to ``_create_from_inline``, which raises the
        # API-key error — proving the profile→inline fallthrough fires.
        with pytest.raises(ValueError, match="API key not found"):
            create_llm_provider(cfg)
        # ``_extract_controller_data`` picks up an ``llm_profile``
        # reference and surfaces it under the ``llm`` key the profile
        # resolver consumes.
        cfg.llm_profile = "some-profile-ref"
        extracted = _extract_controller_data(cfg)
        assert extracted["llm"] == "some-profile-ref"
        assert extracted["model"] == "gpt-4-test"
        cfg.llm_profile = None

        # ── bootstrap.agent_init slash-command + injected-input paths:
        #    a programmatic ``/command`` input is resolved through
        #    ``_prepare_injected_input`` -> ``_try_slash_command_text``
        #    BEFORE it becomes a user turn. The builtin ``/help`` command
        #    is consumed (never reaches the LLM); an unknown ``/xyz``
        #    falls through verbatim as ordinary text. ──
        await agent.start()
        try:
            calls_before = agent.llm.call_count
            # ``/help`` is a builtin user command — consumed, no LLM turn.
            await agent.inject_input("/help")
            assert agent.llm.call_count == calls_before
            # An unknown slash token is NOT a command — it falls through
            # as plain text and DOES reach the LLM as a user turn (the
            # ScriptedLLM repeats its last scripted entry past the end).
            await agent.inject_input("/definitely-not-a-command please")
            assert agent.llm.call_count == calls_before + 1
            assert "/definitely-not-a-command" in agent.llm.last_user_message
        finally:
            await agent.stop()

    # ── workflow 2: rich creature — tools + plugin + sub-agent +
    #    trigger + llm_profile, all wired and exercised live ─────────

    async def test_rich_creature_wires_every_factory_and_runs(
        self, scripted_llm, tmp_path
    ):
        """A fully-loaded creature built via ``Agent.from_path``.

        Asserts every bootstrap factory produced its component AND that
        each is live: a config-declared custom tool actually executes, a
        config-declared custom plugin's hook actually fires, and a
        config-declared timer trigger is registered and startable.
        """
        # Turn 1 calls the custom ``stamp`` tool; turn 2 wraps up.
        # Bracket tool_format: ``@@key=value`` args, one per line.
        scripted_llm.set_script(
            [
                ScriptEntry("[/stamp]\n@@text=payload\n[stamp/]"),
                ScriptEntry("All done."),
            ]
        )
        config_dir = _write_rich_creature(tmp_path)

        agent = Agent.from_path(str(config_dir))

        # bootstrap.llm via profile reference (controller.llm: ...).
        assert isinstance(agent.llm, ScriptedLLM)

        # bootstrap.tools -> builtins + a trigger-tool + one custom tool,
        # all in the registry AND mirrored into the executor.
        tool_names = set(agent.registry.list_tools())
        assert {"read", "write", "bash", "stamp"} <= tool_names
        assert agent.registry.get_tool("stamp").description == (
            "Stamp and echo the given text."
        )
        assert "stamp" in agent.executor._tools
        assert "read" in agent.executor._tools
        # ``type: builtin`` with ``timeout`` / ``max_output`` keys: the
        # tool factory coerces the YAML string values onto ToolConfig.
        bash_tool = agent.registry.get_tool("bash")
        assert bash_tool.config.timeout == 45.0
        assert bash_tool.config.max_output == 2048
        # ``type: trigger`` resolves a universal trigger class into a
        # CallableTriggerTool by its setup_tool_name.
        timer_tool = agent.registry.get_tool("add_timer")
        assert isinstance(timer_tool, CallableTriggerTool)

        # bootstrap.subagents -> the builtin ``research`` sub-agent plus a
        # ``critic`` whose inline ``extra_prompt`` option was overlaid
        # onto the builtin config, plus a ``scout`` loaded as a
        # ``SubAgentConfig`` object from a custom module.
        assert "research" in agent.subagent_manager.list_subagents()
        assert "critic" in agent.subagent_manager.list_subagents()
        assert "scout" in agent.subagent_manager.list_subagents()
        assert agent.registry.get_subagent("research") is not None
        critic_cfg = agent.registry.get_subagent("critic")
        assert critic_cfg.extra_prompt == "Be extra harsh."
        scout_cfg = agent.registry.get_subagent("scout")
        assert scout_cfg.description == "a module-defined scout sub-agent"
        assert scout_cfg.system_prompt == "You are the scout."

        # bootstrap.io -> the named output ``report`` was registered as a
        # known output alongside the default stdout.
        assert "report" in agent.output_router.named_outputs
        assert "report" in agent._known_outputs

        # bootstrap.triggers -> the timer trigger is registered (not yet
        # started) under its explicit name, plus a context + channel
        # trigger built from the same factory.
        trigger_ids = {info.trigger_id for info in agent.trigger_manager.list()}
        assert {"heartbeat", "ctx-watch", "chan-watch"} <= trigger_ids
        heartbeat = agent.trigger_manager.get_trigger("heartbeat")
        assert isinstance(heartbeat, TimerTrigger)
        assert heartbeat.interval == 3600
        assert heartbeat.is_running is False
        ctx_trigger = agent.trigger_manager.get_trigger("ctx-watch")
        assert isinstance(ctx_trigger, ContextUpdateTrigger)
        chan_trigger = agent.trigger_manager.get_trigger("chan-watch")
        assert isinstance(chan_trigger, ChannelTrigger)
        assert chan_trigger.channel_name == "ops"
        # The ``type: custom`` trigger was loaded from the creature's
        # custom module via ModuleLoader — its constructor received the
        # config's prompt + extra option.
        assert "pulse" in trigger_ids
        pulse_trigger = agent.trigger_manager.get_trigger("pulse")
        assert type(pulse_trigger).__name__ == "PulseTrigger"
        assert pulse_trigger.prompt == "pulse fired"
        assert pulse_trigger.beats == 3

        # bootstrap.plugins -> the custom config plugin is loaded AND
        # enabled (config plugins are active, not disabled-discovered).
        # The config-listed ``budget`` catalog plugin is also enabled,
        # while the OTHER catalog plugins land registered-but-disabled.
        plugin_names = {p["name"] for p in agent.plugins.list_plugins()}
        assert "tagplugin" in plugin_names
        assert {"budget", "permgate", "sandbox"} <= plugin_names
        tag_plugin = agent.plugins.get_plugin("tagplugin")
        assert isinstance(tag_plugin, BasePlugin)
        assert agent.plugins.is_enabled("tagplugin")
        assert agent.plugins.is_enabled("budget")  # config-listed -> active
        assert not agent.plugins.is_enabled("permgate")  # discovered -> disabled
        assert tag_plugin.calls == 0  # not fired yet

        # Now run two real turns. ``agent.start()`` also calls
        # ``trigger_manager.start_all()`` -> proves the trigger is
        # startable, and ``plugins.load_all`` -> fires on_load.
        await agent.start()
        try:
            assert heartbeat.is_running is True  # start_all() started it

            await agent._process_event(
                create_user_input_event("please stamp something")
            )

            # The custom tool actually executed: its output is fed back
            # into the conversation as a tool-batch result message (the
            # bracket tool_format delivers results via a user-role turn).
            history = agent.conversation_history
            all_content = " ".join(str(m.get("content", "")) for m in history)
            assert "STAMPED:payload" in all_content, history

            # The custom plugin's post_llm_call hook actually fired —
            # once per LLM turn (tool turn + wrap-up turn).
            assert tag_plugin.calls == 2
            assert any(
                m.get("role") == "assistant" and "[tagged]" in str(m.get("content", ""))
                for m in history
            )
        finally:
            await agent.stop()

        # stop() tears the trigger back down.
        assert heartbeat.is_running is False
        assert agent.llm.call_count == 2

        # ── direct factory-driver coverage for the bootstrap sub-agent /
        #    trigger / tool factories: the override-overlay branches and
        #    the graceful-degradation guards the codebase relies on. ──
        loader = ModuleLoader(agent_path=config_dir)

        # create_subagent_config (builtin): the inline ``options`` dict
        # overlays selected fields onto the catalog config — extra
        # prompt FILE, default_plugins, model, and the background-notify
        # flag all land on the returned SubAgentConfig.
        overlaid = create_subagent_config(
            SubAgentConfigItem(
                name="critic",
                type="builtin",
                options={
                    "extra_prompt_file": "prompts/harsh.md",
                    "default_plugins": ["budget"],
                    "model": "gpt-4-override",
                    "notify_controller_on_background_complete": True,
                },
            ),
            loader,
        )
        assert overlaid is not None
        assert overlaid.extra_prompt_file == "prompts/harsh.md"
        assert overlaid.default_plugins == ["budget"]
        assert overlaid.model == "gpt-4-override"
        assert overlaid.notify_controller_on_background_complete is True
        # create_subagent_config (custom) loaded from the module +
        # config_name pair — same path the ``scout`` config above took.
        scout_obj = create_subagent_config(
            SubAgentConfigItem(
                name="scout",
                type="custom",
                module="./custom/extras.py",
                config_name="scout_config",
            ),
            loader,
        )
        assert scout_obj is not None
        assert scout_obj.name == "scout"
        # A custom sub-agent that names a module but NO loader is
        # available → None (graceful), not a crash.
        assert (
            create_subagent_config(
                SubAgentConfigItem(
                    name="scout",
                    type="custom",
                    module="./custom/extras.py",
                    config_name="scout_config",
                ),
                None,
            )
            is None
        )

        # create_trigger (custom): loads the PulseTrigger class from the
        # creature module; missing loader / missing class degrade to None.
        pulse = create_trigger(
            TriggerConfig(
                type="custom",
                module="./custom/extras.py",
                class_name="PulseTrigger",
                prompt="direct pulse",
                options={"beats": 9},
            ),
            None,
            loader,
        )
        assert type(pulse).__name__ == "PulseTrigger"
        assert pulse.beats == 9
        assert (
            create_trigger(
                TriggerConfig(
                    type="custom",
                    module="./custom/extras.py",
                    class_name="PulseTrigger",
                ),
                None,
                None,  # no loader
            )
            is None
        )

        # create_tool: a custom tool entry with a module but NO loader
        # degrades to None; an invalid timeout value is rejected and the
        # tool is dropped (negative max_output / non-numeric timeout).
        assert (
            create_tool(
                ToolConfigItem(
                    name="x",
                    type="custom",
                    module="./custom/ext.py",
                    class_name="StampTool",
                ),
                None,
            )
            is None
        )
        assert (
            create_tool(
                ToolConfigItem(
                    name="bash", type="builtin", options={"timeout": "not-a-number"}
                ),
                loader,
            )
            is None
        )
        assert (
            create_tool(
                ToolConfigItem(
                    name="bash", type="builtin", options={"max_output": "-5"}
                ),
                loader,
            )
            is None
        )

    # ── workflow 3: the inline-config path
    #    (terrarium/factory-style construction) ──────────────────────

    async def test_inline_config_dict_path_builds_same_components(
        self, scripted_llm, tmp_path
    ):
        """``terrarium/factory.py`` and the API build creatures by handing
        ``Agent(config)`` an ``AgentConfig`` constructed from a dict via
        ``build_agent_config`` — not via ``Agent.from_path``.

        This mirrors that path: the system prompt is assembled from a
        ``system.md`` on disk, but the config dict is built in-process.
        bootstrap must wire the same components either way.
        """
        config_dir = tmp_path / "inline_creature"
        config_dir.mkdir()
        (config_dir / "system.md").write_text(
            "You are the inline creature.\n", encoding="utf-8"
        )
        # A custom input + output module loaded from a file inside the
        # creature folder — the ``type: custom`` IO path.
        custom_dir = config_dir / "custom"
        custom_dir.mkdir()
        (custom_dir / "io.py").write_text(
            textwrap.dedent('''\
                """Custom input + output modules for the inline bootstrap test."""

                from kohakuterrarium.modules.input.base import InputModule
                from kohakuterrarium.modules.output.base import OutputModule


                class SilentInput(InputModule):
                    """An input that never yields — just needs to construct."""

                    async def get_input(self):
                        return None


                class CollectOutput(OutputModule):
                    """Output that appends every write to a list."""

                    def __init__(self, options=None):
                        self.collected = []

                    async def write(self, text: str) -> None:
                        self.collected.append(text)

                    async def write_stream(self, chunk: str) -> None:
                        self.collected.append(chunk)
                '''),
            encoding="utf-8",
        )

        config_data = {
            "name": "inline",
            "version": "1.0",
            "controller": {
                "model": "gpt-4-test",
                "api_key_env": "",
                "tool_format": "bracket",
                "include_tools_in_prompt": True,
                "include_hints_in_prompt": False,
            },
            "system_prompt_file": "system.md",
            "input": {
                "type": "custom",
                "module": "./custom/io.py",
                "class": "SilentInput",
            },
            "output": {
                "type": "custom",
                "module": "./custom/io.py",
                "class": "CollectOutput",
            },
            "tools": [{"name": "read", "type": "builtin"}],
            # Inline custom sub-agent: ``type: custom`` with NO module —
            # the factory builds a SubAgentConfig from the inline fields.
            "subagents": [
                {"name": "critic", "type": "builtin"},
                {
                    "name": "inline_helper",
                    "type": "custom",
                    "description": "an inline-defined helper",
                    "tools": ["read"],
                    "system_prompt": "You are an inline helper.",
                },
            ],
        }
        config = build_agent_config(config_data, config_dir)

        scripted_llm.set_script(["Inline creature replying."])
        agent = Agent(config)

        # Same wiring guarantees as the from_path path. The config
        # declared ``read``; ``skill`` is the auto-registered extra.
        assert isinstance(agent.llm, ScriptedLLM)
        assert set(agent.registry.list_tools()) == {"read", "skill"}
        assert "read" in agent.executor._tools
        # Both sub-agents wired: a builtin and an inline custom one.
        assert "critic" in agent.subagent_manager.list_subagents()
        assert "inline_helper" in agent.subagent_manager.list_subagents()
        inline_helper = agent.registry.get_subagent("inline_helper")
        assert inline_helper is not None
        assert inline_helper.description == "an inline-defined helper"
        assert agent.plugins is not None
        assert agent.controller.llm is agent.llm
        assert "inline creature" in agent.get_system_prompt()
        # The custom IO modules were loaded from the creature folder.
        assert type(agent.input).__name__ == "SilentInput"
        assert type(agent.output_router.default_output).__name__ == "CollectOutput"

        # Live turn proves the inline-built agent is fully operational —
        # and the custom output module actually received the stream.
        await agent.start()
        try:
            await agent._process_event(create_user_input_event("ping"))
        finally:
            await agent.stop()

        assert agent.llm.call_count == 1
        assert "ping" in agent.llm.last_user_message
        assert any(
            m.get("role") == "assistant"
            and "Inline creature replying." in str(m.get("content", ""))
            for m in agent.conversation_history
        )
        assert "".join(agent.output_router.default_output.collected) != ""

        # ── direct factory-driver coverage: the bootstrap factories are
        #    pure functions ``terrarium/factory.py`` and the resume path
        #    call with hand-built config objects. Exercise their
        #    builtin / fallback / unknown branches directly. ──
        loader = ModuleLoader(agent_path=config_dir)

        # create_input: builtin cli, an override short-circuit, and the
        # unknown-type fallback to CLIInput.
        builtin_in = create_input(config, None, loader)
        assert type(builtin_in).__name__ == "SilentInput"
        override = create_input(
            config, builtin_in, loader
        )  # input_override returned verbatim
        assert override is builtin_in
        unknown_cfg = build_agent_config(
            {**config_data, "input": {"type": "totally-unknown"}}, config_dir
        )
        fallback_in = create_input(unknown_cfg, None, loader)
        assert type(fallback_in).__name__ == "CLIInput"
        # A custom input that declares a module but is handed NO loader
        # degrades to CLIInput (not a crash).
        custom_in_cfg = build_agent_config(
            {
                **config_data,
                "input": {
                    "type": "custom",
                    "module": "./custom/io.py",
                    "class": "SilentInput",
                },
            },
            config_dir,
        )
        assert type(create_input(custom_in_cfg, None, None)).__name__ == "CLIInput"
        # A custom input MISSING module/class also degrades to CLIInput.
        bad_custom_in = build_agent_config(
            {**config_data, "input": {"type": "custom"}}, config_dir
        )
        assert type(create_input(bad_custom_in, None, loader)).__name__ == "CLIInput"
        # A bare-name input type that no installed package provides
        # falls through to CLIInput after the package lookup misses.
        bare_in_cfg = build_agent_config(
            {**config_data, "input": {"type": "no_such_package_input"}}, config_dir
        )
        assert type(create_input(bare_in_cfg, None, loader)).__name__ == "CLIInput"

        # create_output: a config with a named output + an unknown-type
        # default falls back to StdoutOutput while the named one builds.
        out_cfg = build_agent_config(
            {
                **config_data,
                "output": {
                    "type": "not-a-real-output",
                    "named_outputs": {"side": {"type": "stdout"}},
                },
            },
            config_dir,
        )
        default_out, named = create_output(out_cfg, None, loader)
        assert type(default_out).__name__ == "StdoutOutput"
        assert "side" in named
        assert type(named["side"]).__name__ == "StdoutOutput"
        # An output_override is returned verbatim as the default output.
        sentinel_out = StdoutOutput()
        forced_default, _ = create_output(out_cfg, sentinel_out, loader)
        assert forced_default is sentinel_out
        # A custom OUTPUT loaded through the loader from the creature
        # folder builds the real class; the named output beside it does
        # too — proving the named-output loop runs the same factory.
        custom_out_cfg = build_agent_config(
            {
                **config_data,
                "output": {
                    "type": "custom",
                    "module": "./custom/io.py",
                    "class": "CollectOutput",
                    "named_outputs": {
                        "log": {
                            "type": "custom",
                            "module": "./custom/io.py",
                            "class": "CollectOutput",
                        }
                    },
                },
            },
            config_dir,
        )
        custom_default, custom_named = create_output(custom_out_cfg, None, loader)
        assert type(custom_default).__name__ == "CollectOutput"
        assert type(custom_named["log"]).__name__ == "CollectOutput"
        # A custom output declaring a module but handed NO loader, and a
        # bare-name output no package provides, both degrade to stdout.
        custom_out_no_loader, _ = create_output(custom_out_cfg, None, None)
        assert type(custom_out_no_loader).__name__ == "StdoutOutput"
        bare_out_cfg = build_agent_config(
            {**config_data, "output": {"type": "no_such_package_output"}}, config_dir
        )
        bare_default, _ = create_output(bare_out_cfg, None, loader)
        assert type(bare_default).__name__ == "StdoutOutput"

        # create_trigger: every builtin branch + the unknown-type None.
        timer = create_trigger(
            TriggerConfig(type="timer", prompt="tick", options={"interval": 12.0}),
            None,
            loader,
        )
        assert isinstance(timer, TimerTrigger)
        assert timer.interval == 12.0
        ctx = create_trigger(TriggerConfig(type="context", prompt="ctx"), None, loader)
        assert isinstance(ctx, ContextUpdateTrigger)
        chan = create_trigger(
            TriggerConfig(type="channel", options={"channel": "ops"}), None, loader
        )
        assert isinstance(chan, ChannelTrigger)
        assert create_trigger(TriggerConfig(type="bogus-trigger"), None, loader) is None
        # custom trigger missing module/class -> None (graceful).
        assert create_trigger(TriggerConfig(type="custom"), None, loader) is None

        # create_tool: an unknown builtin name and an unknown type both
        # return None without raising.
        assert create_tool(ToolConfigItem(name="read", type="builtin"), loader) is not (
            None
        )
        assert (
            create_tool(ToolConfigItem(name="no_such_builtin", type="builtin"), loader)
            is None
        )
        assert create_tool(ToolConfigItem(name="x", type="bad-type"), loader) is None
        # A custom tool entry with no module/class -> None (graceful).
        assert create_tool(ToolConfigItem(name="x", type="custom"), loader) is None
        # An invalid builtin tool-config value (max_output non-int) is
        # rejected and the tool is dropped.
        assert (
            create_tool(
                ToolConfigItem(
                    name="bash", type="builtin", options={"max_output": "not-an-int"}
                ),
                loader,
            )
            is None
        )

        # create_subagent_config: unknown builtin name + unknown type.
        assert (
            create_subagent_config(
                SubAgentConfigItem(name="ghost", type="builtin"), loader
            )
            is None
        )
        assert (
            create_subagent_config(
                SubAgentConfigItem(name="x", type="nonsense-type"), loader
            )
            is None
        )
        # An inline custom sub-agent (no module) builds straight from
        # the item fields.
        inline_sa = create_subagent_config(
            SubAgentConfigItem(
                name="adhoc", type="custom", description="ad-hoc helper"
            ),
            loader,
        )
        assert inline_sa is not None
        assert inline_sa.name == "adhoc"
        assert inline_sa.description == "ad-hoc helper"

    # ── workflow 4: bootstrap's fallback behaviour is real, not a
    #    crash — an unknown tool type is dropped, the rest still wire ─

    async def test_unknown_tool_type_is_dropped_rest_of_build_survives(
        self, scripted_llm, tmp_path
    ):
        """``bootstrap.tools.create_tool`` returns ``None`` for an unknown
        tool type and logs a warning — it must NOT abort the whole agent
        build. The valid tools beside it still register, and the agent
        still runs.

        This pins the "factory degrades gracefully" contract that the
        codebase relies on (a typo in one creature entry shouldn't brick
        the creature).
        """
        config_dir = tmp_path / "degraded_creature"
        config_dir.mkdir()
        (config_dir / "system.md").write_text(
            "You are the degraded creature.\n", encoding="utf-8"
        )
        # Every factory gets at least one broken entry: a bad tool type,
        # a custom tool with a missing module, a bad trigger type, a bad
        # sub-agent type, and a plugin that can't resolve. NONE of these
        # may abort the agent build — they degrade to "dropped + logged".
        (config_dir / "config.yaml").write_text(
            textwrap.dedent("""\
                name: degraded
                version: "1.0"
                controller:
                  model: gpt-4-test
                  api_key_env: ""
                  tool_format: bracket
                  include_tools_in_prompt: true
                  include_hints_in_prompt: false
                system_prompt_file: system.md
                input:
                  type: none
                output:
                  type: stdout
                tools:
                  - name: read
                    type: builtin
                  - name: bogus
                    type: not_a_real_type
                  - name: write
                    type: builtin
                  - name: ghost_tool
                    type: custom
                    module: ./custom/does_not_exist.py
                    class: GhostTool
                triggers:
                  - name: good-timer
                    type: timer
                    prompt: "tick"
                    interval: 3600
                  - name: bad-trigger
                    type: not_a_real_trigger
                subagents:
                  - name: research
                    type: builtin
                  - name: phantom
                    type: not_a_real_subagent_type
                plugins:
                  - name: nonexistent_plugin_xyz
                """),
            encoding="utf-8",
        )

        scripted_llm.set_script(["Degraded creature still works."])
        agent = Agent.from_path(str(config_dir))

        # The unknown-type tool entry + the custom tool with a missing
        # module were both dropped; the two valid builtins registered
        # around them.
        registered = set(agent.registry.list_tools())
        assert "read" in registered
        assert "write" in registered
        assert "bogus" not in registered
        assert "ghost_tool" not in registered

        # The bad trigger entry was dropped; the valid timer survived.
        trigger_ids = {info.trigger_id for info in agent.trigger_manager.list()}
        assert "good-timer" in trigger_ids
        assert "bad-trigger" not in trigger_ids

        # The bad sub-agent entry was dropped; the valid builtin survived.
        assert "research" in agent.subagent_manager.list_subagents()
        assert "phantom" not in agent.subagent_manager.list_subagents()

        # The unresolvable plugin name did not land in the manager, but
        # the manager itself still built (catalog plugins still present).
        plugin_names = {p["name"] for p in agent.plugins.list_plugins()}
        assert "nonexistent_plugin_xyz" not in plugin_names
        assert "budget" in plugin_names  # catalog discovery still ran

        # And the agent is still fully operational end-to-end.
        await agent.start()
        try:
            await agent._process_event(create_user_input_event("status?"))
        finally:
            await agent.stop()

        assert agent.llm.call_count == 1
        assert any(
            m.get("role") == "assistant"
            and "Degraded creature still works." in str(m.get("content", ""))
            for m in agent.conversation_history
        )
