"""
Agent component initialization.

Contains mixin methods for initializing all agent subsystems
(LLM, registry, executor, input, output, triggers, sub-agents).
Separated from the main Agent class to keep file sizes manageable.

Heavy initialization logic is delegated to bootstrap.* factory modules
to reduce import fan-out.
"""

from pathlib import Path
from typing import Any

from kohakuterrarium.bootstrap.io import create_input, create_output
from kohakuterrarium.bootstrap.llm import create_llm_provider
from kohakuterrarium.bootstrap.subagents import init_subagents
from kohakuterrarium.bootstrap.tools import init_tools
from kohakuterrarium.bootstrap.triggers import init_triggers
from kohakuterrarium.builtins.tool_catalog import get_builtin_tool
from kohakuterrarium.builtins.user_commands import (
    get_builtin_user_command,
    list_builtin_user_commands,
)
from kohakuterrarium.core.config import AgentConfig
from kohakuterrarium.core.controller import Controller, ControllerConfig
from kohakuterrarium.core.executor import Executor
from kohakuterrarium.core.loader import ModuleLoader
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.core.session import get_session
from kohakuterrarium.modules.input.base import InputModule
from kohakuterrarium.modules.output.base import OutputModule
from kohakuterrarium.modules.output.router import OutputRouter
from kohakuterrarium.modules.subagent import SubAgentManager
from kohakuterrarium.modules.user_command.base import (
    UserCommandContext,
    UserCommandResult,
    parse_slash_command,
)
from kohakuterrarium.packages import (
    find_package_root_for_path,
    get_package_framework_hints,
)
from kohakuterrarium.parsing.format import BRACKET_FORMAT, XML_FORMAT, ToolCallFormat
from kohakuterrarium.prompt.aggregator import aggregate_system_prompt
from kohakuterrarium.prompt.framework_hints import merge_overrides
from kohakuterrarium.skills import (
    SkillCommand,
    SkillPathScanner,
    SkillRegistry,
    build_user_skill_turn,
    discover_skills,
)
from kohakuterrarium.utils.file_guard import FileReadState, PathBoundaryGuard
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class AgentInitMixin:
    """
    Mixin providing component initialization for the Agent class.

    All _init_* and _create_* methods live here to keep the main Agent
    class focused on its public API and runtime loop.
    """

    config: AgentConfig
    _loader: ModuleLoader

    def _init_llm(self) -> None:
        """Initialize LLM provider from profile or inline config."""
        llm_override = getattr(self, "_llm_override", None)
        self.llm = create_llm_provider(self.config, llm_override=llm_override)

    def _init_registry(self) -> None:
        """Initialize module registry and register tools.

        Order matters:

        1. ``init_tools`` wires user-configured entries from the
           creature YAML.
        2. ``_drop_unsupported_provider_native_tools`` removes any of
           those user-wired entries that the active provider can't
           handle (explicit wiring on the wrong provider → silent drop
           with an INFO log).
        3. ``_auto_inject_provider_native_tools`` adds every native
           tool the active provider advertises via
           ``provider_native_tools`` that isn't already in the registry
           and isn't opted out by the creature's
           ``disable_provider_tools`` list.
        """
        self.registry = Registry()
        init_tools(self.config, self.registry, self._loader)
        self._drop_unsupported_provider_native_tools()
        self._auto_inject_provider_native_tools()

    def _drop_unsupported_provider_native_tools(self) -> None:
        """Remove user-wired provider-native tools the provider can't serve.

        Users may explicitly list e.g. ``image_gen`` under ``tools:``.
        If the active provider's ``provider_name`` is not in the
        tool's ``provider_support`` set, drop the entry — the tool
        can't function here and the provider won't translate it.
        Auto-injection handles the happy path in a separate step.
        """
        llm = getattr(self, "llm", None)
        active = getattr(llm, "provider_name", "") if llm is not None else ""
        for name in list(self.registry.list_tools()):
            tool = self.registry.get_tool(name)
            if tool is None or not getattr(tool, "is_provider_native", False):
                continue
            support = getattr(tool, "provider_support", frozenset())
            if active and active in support:
                continue
            self.registry.unregister_tool(name)
            logger.info(
                "provider_native_tool_dropped",
                tool_name=name,
                active_provider=active or "<unset>",
                supported_providers=sorted(support) or None,
            )

    def _auto_inject_provider_native_tools(self) -> None:
        """Auto-register provider-native tools advertised by the LLM.

        Every provider declares the builtin tool names it can serve
        via the ``provider_native_tools`` class attribute. Those
        entries are injected into the registry automatically — the
        creature does NOT have to list them under ``tools:``. To
        suppress any of them, add the tool name to the creature's
        ``disable_provider_tools`` list.
        """
        llm = getattr(self, "llm", None)
        offered = (
            getattr(llm, "provider_native_tools", frozenset()) if llm else frozenset()
        )
        if not offered:
            return

        disabled = set(self.config.disable_provider_tools or ())
        existing = set(self.registry.list_tools())

        for name in sorted(offered):
            if name in disabled:
                logger.debug(
                    "provider_native_tool_opted_out",
                    tool_name=name,
                    active_provider=getattr(llm, "provider_name", ""),
                )
                continue
            if name in existing:
                # User already wired it with custom config — respect that.
                continue
            tool = get_builtin_tool(name)
            if tool is None:
                logger.warning(
                    "provider_native_tool_not_in_catalog",
                    tool_name=name,
                    active_provider=getattr(llm, "provider_name", ""),
                )
                continue
            self.registry.register_tool(tool)
            logger.info(
                "provider_native_tool_injected",
                tool_name=name,
                active_provider=getattr(llm, "provider_name", ""),
            )

    def _init_executor(self) -> None:
        """Initialize background executor."""
        self.executor = Executor()

        # Register tools from registry
        for tool_name in self.registry.list_tools():
            tool = self.registry.get_tool(tool_name)
            if tool:
                self.executor.register_tool(tool)

        # Wire session for ToolContext building
        # Use explicit session if provided, else create from session_key
        explicit = getattr(self, "_explicit_session", None)
        if explicit is not None:
            self.session = explicit
        else:
            session_key = self.config.session_key or self.config.name
            self.session = get_session(session_key)

        # Backward-compatible accessors
        self.channel_registry = self.session.channels
        self.scratchpad = self.session.scratchpad

        # Set executor context
        self.executor._agent = self
        self.executor._agent_name = self.config.name
        self.executor._session = self.session
        self.executor._environment = getattr(self, "environment", None)
        self.executor._tool_format = (
            self.config.tool_format
            if isinstance(self.config.tool_format, str)
            else "bracket"
        )
        # Working dir: explicit pwd from API/config > process cwd.
        # agent_path is for resolving config-relative paths (prompts, custom tools).
        explicit_pwd = getattr(self, "_explicit_pwd", None)
        if explicit_pwd:
            self.executor._working_dir = Path(explicit_pwd).resolve()
        else:
            self.executor._working_dir = Path.cwd()
        if hasattr(self.config, "agent_path") and self.config.agent_path:
            memory_config = getattr(self.config, "memory", None)
            if isinstance(memory_config, dict) and memory_config.get("path"):
                self.executor._memory_path = (
                    self.config.agent_path / memory_config["path"]
                )

        # File safety guards
        self._file_read_state = FileReadState()
        self.executor._file_read_state = self._file_read_state

        pwd_guard_mode = getattr(self.config, "pwd_guard", "warn")
        self._path_guard = PathBoundaryGuard(
            cwd=self.executor._working_dir,
            mode=pwd_guard_mode,
        )
        self.executor._path_guard = self._path_guard

    def _init_subagents(self) -> None:
        """Initialize sub-agent manager and register sub-agents."""
        # Pass parent's tool_format so sub-agents inherit it
        parent_tool_format = (
            self.config.tool_format
            if isinstance(self.config.tool_format, str)
            else "bracket"
        )

        self.subagent_manager = SubAgentManager(
            parent_registry=self.registry,
            llm=self.llm,
            agent_path=self.config.agent_path,
            job_store=self.executor.job_store,  # Share job store so wait command works
            max_depth=self.config.max_subagent_depth,
            tool_format=parent_tool_format,
        )
        # Inherit parent's tool context builder (working_dir, file guards, etc.)
        self.subagent_manager._parent_executor = self.executor

        init_subagents(self.config, self.subagent_manager, self.registry, self._loader)

    def _resolve_tool_format(self) -> ToolCallFormat | None:
        """
        Resolve tool_format config to a ToolCallFormat instance.

        Returns:
            ToolCallFormat for bracket/xml/custom, or None for native mode
            (native mode bypasses the stream parser entirely).
        """
        fmt = self.config.tool_format
        if isinstance(fmt, str):
            match fmt:
                case "bracket":
                    return BRACKET_FORMAT
                case "xml":
                    return XML_FORMAT
                case "native":
                    return None  # Native mode bypasses parser
                case _:
                    logger.warning(
                        "Unknown tool_format, using bracket", tool_format=fmt
                    )
                    return BRACKET_FORMAT
        elif isinstance(fmt, dict):
            return ToolCallFormat(**fmt)
        return BRACKET_FORMAT

    def _init_controller(self) -> None:
        """Initialize controller."""
        # Build system prompt
        # Aggregator auto-adds: tool list (name + description), framework hints
        # system.md should only contain agent personality/guidelines
        base_prompt = self.config.system_prompt

        # Add sub-agents section if any registered (respects include_tools_in_prompt)
        if self.config.include_tools_in_prompt:
            subagents_prompt = self.subagent_manager.get_subagents_prompt()
            if subagents_prompt:
                base_prompt = base_prompt + "\n\n" + subagents_prompt

        known_outputs = getattr(self, "_known_outputs", set())

        # Resolve tool format from config
        self._tool_format = self._resolve_tool_format()
        tool_format_name = (
            self.config.tool_format
            if isinstance(self.config.tool_format, str)
            else "custom"
        )

        # Resolve framework-hint overrides: package-level (kohaku.yaml)
        # merges under creature-level (AgentConfig.framework_hint_overrides).
        pkg_root = find_package_root_for_path(self.config.agent_path)
        package_hints = get_package_framework_hints(pkg_root)
        hint_overrides = merge_overrides(
            package_hints, self.config.framework_hint_overrides
        )

        skill_registry = getattr(self, "skills", None)
        if skill_registry is not None:
            self._ensure_skill_tool_registered()

        logger.debug(
            "Building system prompt",
            known_outputs=known_outputs,
            tool_format=tool_format_name,
            hint_override_keys=sorted(hint_overrides.keys()) if hint_overrides else [],
        )
        system_prompt = aggregate_system_prompt(
            base_prompt,
            self.registry,
            include_tools=self.config.include_tools_in_prompt,
            include_hints=self.config.include_hints_in_prompt,
            tool_format=tool_format_name,
            known_outputs=known_outputs,
            framework_hint_overrides=hint_overrides or None,
            skill_registry=getattr(self, "skills", None),
            skill_index_budget_bytes=getattr(
                self.config, "skill_index_budget_bytes", 4096
            ),
        )

        # Store controller config for creating controllers on-demand (parallel mode)
        self._controller_config = ControllerConfig(
            system_prompt=system_prompt,
            include_job_status=True,
            include_tools_list=False,  # Already in aggregated prompt
            max_messages=self.config.max_messages,
            ephemeral=self.config.ephemeral,
            known_outputs=getattr(self, "_known_outputs", set()),
            tool_format=tool_format_name,
            sanitize_orphan_tool_calls=self.config.sanitize_orphan_tool_calls,
        )

        # Primary controller (always exists)
        # Note: Controller handles framework commands (read, info, jobs, wait)
        # via its own _commands dict and ControllerContext
        self.controller = self._create_controller()

    def _ensure_skill_tool_registered(self) -> None:
        """Expose procedural skills through the normal tool registry."""
        if self.registry.get_tool("skill") is not None:
            return
        tool = get_builtin_tool("skill")
        if tool is None:
            try:
                from kohakuterrarium.builtins.tools.skill import SkillTool

                tool = SkillTool()
            except Exception as exc:
                logger.warning(
                    "Skill tool not found in builtin catalog", error=str(exc)
                )
                return
        self.registry.register_tool(tool)
        if getattr(self, "executor", None) is not None:
            self.executor.register_tool(tool)

    async def _try_slash_command_text(self, text: str) -> Any | None:
        """Run the configured slash dispatcher for programmatic inputs."""
        input_module = getattr(self, "input", None)
        if input_module is not None and hasattr(input_module, "try_user_command"):
            return await input_module.try_user_command(text)

        commands: dict[str, Any] = {}
        for name in list_builtin_user_commands():
            cmd = get_builtin_user_command(name)
            if cmd:
                commands[name] = cmd
        context = UserCommandContext(agent=self, session=getattr(self, "session", None))
        name, args = parse_slash_command(text)
        cmd = commands.get(name)
        if cmd is not None:
            return await cmd.execute(args, context)

        registry = getattr(self, "skills", None)
        if registry is None:
            return None
        skill = registry.get(name)
        if skill is None:
            return None
        if not skill.enabled:
            return UserCommandResult(
                error=f"Skill '{name}' is disabled. Enable with /skill enable {name}."
            )

        return UserCommandResult(
            output=build_user_skill_turn(skill, args),
            consumed=False,
        )

    async def _prepare_injected_input(
        self,
        content: Any,
        source: str,
    ) -> Any | None:
        """Resolve programmatic slash input before building a user event."""
        if not isinstance(content, str) or not content.startswith("/"):
            return content
        result = await self._try_slash_command_text(content)
        if result is None:
            return content
        if result.error:
            if self.output_router is not None:
                self.output_router.notify_activity(
                    "command_error",
                    result.error,
                    metadata={"source": source, "command": content},
                )
            return None
        if result.consumed:
            if result.output and self.output_router is not None:
                self.output_router.notify_activity(
                    "command_result",
                    result.output,
                    metadata={"source": source, "command": content},
                )
            return None
        return result.output or content

    def _create_controller(self) -> Controller:
        """Create a new controller instance (for parallel processing)."""
        controller = Controller(
            self.llm,
            self._controller_config,
            executor=self.executor,
            registry=self.registry,
        )
        if hasattr(self, "output_router"):
            controller.output_router = self.output_router
        skill_registry = getattr(self, "skills", None)
        if skill_registry is not None:
            controller.register_command("skill", SkillCommand(skill_registry))
            if hasattr(controller, "_context"):
                controller._context.skills_registry = skill_registry
        return controller

    def _init_skills(self) -> None:
        """Discover procedural skills and build the runtime registry.

        Scans (in priority order, high → low):

        1. ``<cwd>/.kt/skills``, ``.claude/skills``, ``.agents/skills``
        2. ``~/.kohakuterrarium/skills``, ``~/.claude/skills``,
           ``~/.agents/skills``
        3. ``<agent>/prompts/skills``
        4. every installed package's ``skills:`` manifest entries

        Last-wins across origins (spec 1.1 exception). Runtime
        enable-state is persisted to the session scratchpad under
        ``skills.enabled``.
        """
        scratchpad = getattr(self, "scratchpad", None)
        self.skills = SkillRegistry(scratchpad=scratchpad)
        self.skill_path_scanner = SkillPathScanner()

        cwd = Path(self.executor._working_dir) if self.executor else Path.cwd()
        agent_path = getattr(self.config, "agent_path", None)
        declared = list(getattr(self.config, "skills", []) or [])

        try:
            discovered = discover_skills(
                cwd=cwd,
                agent_path=Path(agent_path) if agent_path else None,
                declared_package_skills=declared,
            )
        except Exception as exc:
            logger.warning(
                "Skill discovery failed",
                error=str(exc),
                exc_info=True,
            )
            discovered = []

        for skill in discovered:
            self.skills.add(skill)

        # Mirror the registry onto session.extra so plugins and the
        # studio routes can reach it without an agent reference.
        session = getattr(self, "session", None)
        if session is not None:
            session.extra["skills_registry"] = self.skills

        logger.info(
            "Skills registry initialized",
            skill_count=len(self.skills),
            enabled=len(self.skills.list_enabled()),
        )

    def _init_input(self, custom_input: InputModule | None) -> None:
        """Initialize input module."""
        self.input = create_input(self.config, custom_input, self._loader)

    def _init_output(self, custom_output: OutputModule | None) -> None:
        """Initialize output modules (default and named)."""
        default_output, named_outputs = create_output(
            self.config, custom_output, self._loader
        )

        # Store known outputs for parser config
        self._known_outputs = set(named_outputs.keys())
        logger.info("Named outputs registered", named_outputs=list(self._known_outputs))

        self.output_router = OutputRouter(default_output, named_outputs=named_outputs)

    def _init_user_commands(self) -> None:
        """Wire user commands (slash commands) into the input module."""
        # Load all builtins by default
        commands: dict = {}
        for name in list_builtin_user_commands():
            cmd = get_builtin_user_command(name)
            if cmd:
                commands[name] = cmd

        context = UserCommandContext(
            agent=self,
            session=getattr(self, "session", None),
            input_module=self.input,
        )

        # Wire into input module (if it supports commands)
        if hasattr(self.input, "set_user_commands"):
            self.input.set_user_commands(commands, context)

    def _init_triggers(self) -> None:
        """Initialize trigger modules from config into trigger_manager."""
        session = getattr(self, "session", None)
        init_triggers(self.config, self.trigger_manager, session, self._loader)
