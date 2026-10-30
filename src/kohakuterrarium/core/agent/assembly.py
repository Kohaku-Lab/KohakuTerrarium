"""``Agent``: the top-level orchestrator, assembled from the parts in this package.

``Agent`` is built from per-concern mixins; see ``README.md`` for the part map
and the constructor order.
"""

from typing import TYPE_CHECKING, Any

from kohakuterrarium.bootstrap.agent_init import AgentInitMixin
from kohakuterrarium.core.agent.construct import AgentConstructMixin
from kohakuterrarium.core.agent.extensions import AgentExtensionsMixin
from kohakuterrarium.core.agent.helpers import attach_session_helpers
from kohakuterrarium.core.agent.history.compact import AgentCompactMixin
from kohakuterrarium.core.agent.history.messages import AgentMessagesMixin
from kohakuterrarium.core.agent.lifecycle import AgentLifecycleMixin
from kohakuterrarium.core.agent.model import AgentModelMixin
from kohakuterrarium.core.agent.observability import init_branch_state
from kohakuterrarium.core.agent.session import AgentSessionMixin
from kohakuterrarium.core.agent.startup import AgentStartupMixin
from kohakuterrarium.core.agent.tools.gate import AgentToolGateMixin
from kohakuterrarium.core.agent.tools.plugin_tools import AgentPluginToolsMixin
from kohakuterrarium.core.agent.turn.budget_recovery import attach_recovery_hooks
from kohakuterrarium.core.agent.turn.drive import AgentDriveMixin
from kohakuterrarium.core.agent.turn.event_loop import AgentEventLoopMixin
from kohakuterrarium.core.agent.turn.handlers import AgentHandlersMixin
from kohakuterrarium.core.agent.turn.interrupt import AgentInterruptMixin
from kohakuterrarium.core.agent.turn.run import AgentTurnMixin
from kohakuterrarium.core.config.load import AgentConfig, load_agent_config
from kohakuterrarium.core.loader import ModuleLoader
from kohakuterrarium.core.session import Session
from kohakuterrarium.modules.input.base import InputModule
from kohakuterrarium.modules.output.base import OutputModule
from kohakuterrarium.packages.walk import package_snapshot
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.core.environment import Environment

logger = get_logger(__name__)


class Agent(
    AgentStartupMixin,
    AgentSessionMixin,
    AgentDriveMixin,
    AgentInterruptMixin,
    AgentConstructMixin,
    AgentTurnMixin,
    AgentExtensionsMixin,
    AgentInitMixin,
    AgentEventLoopMixin,
    AgentHandlersMixin,
    AgentMessagesMixin,
    AgentModelMixin,
    AgentCompactMixin,
    AgentLifecycleMixin,
    AgentToolGateMixin,
    AgentPluginToolsMixin,
):
    """Main agent orchestrator. Wires LLM, controller, executor, I/O.

    Construction entry points (``Agent.build`` / ``Agent.from_path``)
    live in :class:`AgentConstructMixin`; the typed turn drivers
    (``run`` / ``run_stream``) in :class:`AgentTurnMixin`; runtime
    extension injection (``add_tool`` / ``add_plugin``) in
    :class:`AgentExtensionsMixin`.
    """

    def __init__(
        self,
        config: AgentConfig,
        *,
        input_module: InputModule | None = None,
        output_module: OutputModule | None = None,
        session: Session | None = None,
        environment: "Environment | None" = None,
        llm: Any = None,
        pwd: str | None = None,
        strict: bool = True,
        tools: list[Any] | None = None,
        plugins: list[Any] | None = None,
        subagents: list[Any] | None = None,
        outputs: dict[str, OutputModule] | None = None,
        user_commands: dict[str, Any] | None = None,
    ):
        """
        Initialize agent from config.

        Args:
            config: Agent configuration
            input_module: Custom input module (uses config if None)
            output_module: Custom output module (uses config if None)
            session: Explicit session (creature-private state). Created from
                     session_key if not provided.
            environment: Shared environment (inter-creature state). None for
                         standalone agents.
            llm: LLM binding — provider instance, selector string,
                 ``LLMProfile``, or None (resolve from config)
            pwd: Explicit working directory (overrides process cwd)
            strict: Raise on construction problems (unresolvable LLM,
                    unknown tool, broken plugin) instead of degrading
                    silently.  Interactive frontends (Studio / Lab /
                    ``kt run``) pass ``strict=False`` so a user can fix
                    the problem at runtime (e.g. rebind the model);
                    programmatic callers get loud failures by default.
            tools: Tool INSTANCES to register alongside the config's
                   tools (e.g. ``kt.tool(fn)`` adapters) — present in
                   the initial system prompt.
            plugins: Plugin INSTANCES to register (enabled).
            subagents: ``SubAgentConfig`` instances to register.
            outputs: Extra named outputs ``{name: OutputModule}``.
            user_commands: Extra slash commands ``{name: UserCommand}``.
        """
        self.config = config
        self._strict = strict
        self._init_event_state()
        self._init_model_state(llm)

        # Explicit working directory (from web API pwd field)
        self._explicit_pwd = pwd

        self._init_session_state()
        self._init_interrupt_state()

        self.compact_manager: Any = None
        self.plugins: Any = None  # PluginManager | None
        attach_session_helpers(self)

        init_branch_state(self)

        # Environment and session (explicit or auto-created in _init_executor)
        self.environment: "Environment | None" = environment
        self._explicit_session: Session | None = session

        # Module loader for custom components
        self._loader = ModuleLoader(agent_path=config.agent_path)

        # Initialize termination checker
        self._termination_checker = self._init_termination()

        # Extra slash commands merged in by _init_user_commands.
        self._extra_user_commands = dict(user_commands or {})

        # Initialize components (methods from AgentInitMixin)
        # Order matters: output before controller (need known_outputs for parser)
        with package_snapshot():
            self._init_llm()
            attach_recovery_hooks(self, self.llm)
            self._init_registry()
            self._init_executor()
            # Instance-injected tools (E7) — registered BEFORE the
            # controller so they land in the initial system prompt.
            for tool_instance in tools or []:
                self.registry.register_tool(tool_instance)
                self.executor.register_tool(tool_instance)
            self._init_plugins()
            for plugin_instance in plugins or []:
                self.plugins.register(plugin_instance)
            self.refresh_plugin_tools()
            self._init_subagents()
            for subagent_cfg in subagents or []:
                self.subagent_manager.register(subagent_cfg)
            self._init_iteration_budget()
            self._init_output(output_module)  # Before controller - sets _known_outputs
            # Instance-injected named outputs join before the parser learns
            # the known-output set.
            for output_name, extra_output in (outputs or {}).items():
                self.output_router.named_outputs[output_name] = extra_output
                self._known_outputs.add(output_name)
            self._init_skills()  # Before controller so skill index is in prompt
            self._init_controller()
            self.watch_renderers()
            self._init_input(input_module)
            self._init_user_commands()
            self._init_triggers()
            if self.plugins is not None:
                self.plugins.bind_host(self)

        logger.info(
            "Agent initialized",
            agent_name=config.name,
            model=getattr(self.llm, "model", config.model),
            tools=len(self.registry.list_tools()),
            triggers=len(self.trigger_manager.list()),
            ephemeral=config.ephemeral,
        )


async def run_agent(config_path: str) -> None:
    """
    Convenience function to run an agent from config path.

    Args:
        config_path: Path to agent config folder
    """
    config = load_agent_config(config_path)
    agent = Agent(config)
    await agent.run_forever()
