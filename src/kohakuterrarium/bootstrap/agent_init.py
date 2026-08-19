"""
Agent subsystem initialization delegated to focused bootstrap factories.
"""

import importlib
from pathlib import Path
from typing import Any, Callable

from kohakuterrarium.bootstrap.io import create_input, create_output
from kohakuterrarium.bootstrap.llm import coerce_llm_provider, create_llm_provider
from kohakuterrarium.llm.deferred_provider import DeferredLLMProvider
from kohakuterrarium.bootstrap.subagents import init_subagents
from kohakuterrarium.bootstrap.tools import init_tools
from kohakuterrarium.bootstrap.triggers import init_triggers
from kohakuterrarium.builtins.plugin_catalog import resolve_plugin_specs
from kohakuterrarium.builtins.tool_catalog import get_builtin_tool
from kohakuterrarium.builtins.tools.skill import SkillTool
from kohakuterrarium.builtins.user_commands import (
    get_builtin_user_command,
    list_builtin_user_commands,
)
from kohakuterrarium.core.budget import IterationBudget
from kohakuterrarium.core.config import AgentConfig
from kohakuterrarium.core.controller import Controller, ControllerConfig
from kohakuterrarium.core.executor import Executor
from kohakuterrarium.core.loader import ModuleLoader
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.core.session import Session, get_session
from kohakuterrarium.core.termination import TerminationChecker, TerminationConfig
from kohakuterrarium.modules.input.base import InputModule
from kohakuterrarium.modules.output.base import OutputModule
from kohakuterrarium.modules.output.router import OutputRouter
from kohakuterrarium.modules.plugin.base import PluginContext
from kohakuterrarium.modules.subagent import SubAgentManager
from kohakuterrarium.modules.user_command.aggregate import (
    CommandContribution,
    CommandProvenance,
    aggregate_user_commands,
)
from kohakuterrarium.modules.user_command.base import (
    UserCommandContext,
    UserCommandResult,
    parse_slash_command,
)
from kohakuterrarium.packages.locations import find_package_root_for_path
from kohakuterrarium.packages.manifest import get_package_framework_hints
from kohakuterrarium.packages.resolve import ensure_package_importable
from kohakuterrarium.packages.slots import iter_package_user_command_entries
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
    """Initialize the components used by the Agent runtime."""

    config: AgentConfig
    _loader: ModuleLoader

    def _init_llm(self) -> None:
        """Bind an injected provider or resolve a configured LLM.

        Direct instances and profiles fail immediately when invalid. In
        non-strict mode, selector resolution may install a deferred provider so
        an existing creature remains accessible until the user selects a model.
        """
        llm_spec = getattr(self, "_llm_spec", None)
        if llm_spec is not None and not isinstance(llm_spec, str):
            # Explicit objects are caller-owned bindings and must fail eagerly.
            self.llm = coerce_llm_provider(llm_spec, self.config)
            return
        try:
            self.llm = create_llm_provider(self.config, llm_spec)
        except (ValueError, RuntimeError) as exc:
            if getattr(self, "_strict", True):
                # Strict builds reject model errors before creating an unusable agent.
                raise
            logger.warning(
                "agent build: no LLM provider yet, deferring (reason=%s)",
                exc,
            )
            self.llm = DeferredLLMProvider(reason=str(exc))

    def _init_registry(self) -> None:
        """Register configured tools, remove unsupported native tools, then inject offered ones."""
        self.registry = Registry()
        init_tools(
            self.config,
            self.registry,
            self._loader,
            strict=getattr(self, "_strict", True),
        )
        self._drop_unsupported_provider_native_tools()
        self._auto_inject_provider_native_tools()

    def _drop_unsupported_provider_native_tools(self) -> None:
        """Remove configured provider-native tools unsupported by the active LLM."""
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
        """Register advertised provider-native tools unless disabled or overridden."""
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
                # Explicit configuration takes precedence over automatic injection.
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

    def _init_iteration_budget(self) -> None:
        """Create the iteration counter shared by the parent and sub-agents."""
        cap = getattr(self.config, "max_iterations", None)
        if not cap or cap <= 0:
            self.iteration_budget = None
            return
        self.iteration_budget = IterationBudget(remaining=int(cap), total=int(cap))
        if hasattr(self, "subagent_manager") and self.subagent_manager is not None:
            self.subagent_manager.iteration_budget = self.iteration_budget
        logger.info(
            "Iteration budget configured",
            agent_name=self.config.name,
            max_iterations=cap,
        )

    def _init_termination(self) -> "TerminationChecker | None":
        """Initialize termination checker from config."""
        if not self.config.termination:
            return None

        tc = TerminationConfig(
            max_turns=self.config.termination.get("max_turns", 0),
            max_tokens=self.config.termination.get("max_tokens", 0),
            max_duration=self.config.termination.get("max_duration", 0),
            idle_timeout=self.config.termination.get("idle_timeout", 0),
            keywords=self.config.termination.get("keywords", []),
        )
        checker = TerminationChecker(tc)
        if checker.is_active:
            logger.info("Termination conditions configured", config=str(tc))
        return checker

    def _init_executor(self) -> None:
        """Initialize background executor."""
        self.executor = Executor()

        for tool_name in self.registry.list_tools():
            tool = self.registry.get_tool(tool_name)
            if tool:
                self.executor.register_tool(tool)

        # Explicit sessions win; session_key opts into sharing; otherwise isolate state.
        explicit = getattr(self, "_explicit_session", None)
        if explicit is not None:
            self.session = explicit
        elif self.config.session_key:
            self.session = get_session(self.config.session_key)
        else:
            self.session = Session(key=self.config.name)

        # Preserve the legacy direct accessors over the selected session.
        self.channel_registry = self.session.channels
        self.scratchpad = self.session.scratchpad

        self.executor._agent = self
        self.executor._agent_name = self.config.name
        self.executor._session = self.session
        self.executor._environment = getattr(self, "environment", None)
        self.executor._tool_format = (
            self.config.tool_format
            if isinstance(self.config.tool_format, str)
            else "bracket"
        )
        # The runtime working directory is independent of config-relative agent_path.
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

        self._file_read_state = FileReadState()
        self.executor._file_read_state = self._file_read_state

        pwd_guard_mode = getattr(self.config, "pwd_guard", "warn")
        self._path_guard = PathBoundaryGuard(
            cwd=self.executor._working_dir,
            mode=pwd_guard_mode,
        )
        self.executor._path_guard = self._path_guard

    def _init_subagents(self) -> None:
        """Create the sub-agent manager with inherited runtime context."""
        # Sub-agents inherit the parent's call syntax.
        parent_tool_format = (
            self.config.tool_format
            if isinstance(self.config.tool_format, str)
            else "bracket"
        )

        default_plugin_specs = resolve_plugin_specs(
            getattr(self.config, "default_plugins", []) or []
        )
        self.subagent_manager = SubAgentManager(
            parent_registry=self.registry,
            llm=self.llm,
            agent_path=self.config.agent_path,
            job_store=self.executor.job_store,  # Shared jobs let parent commands await children.
            max_depth=self.config.max_subagent_depth,
            tool_format=parent_tool_format,
            default_plugin_specs=default_plugin_specs,
        )
        # Reuse the parent executor's working directory and safety guards.
        self.subagent_manager._parent_executor = self.executor

        init_subagents(self.config, self.subagent_manager, self.registry, self._loader)

    def _resolve_tool_format(self) -> ToolCallFormat | None:
        """Resolve configured call syntax; native mode bypasses stream parsing."""
        fmt = self.config.tool_format
        if isinstance(fmt, str):
            match fmt:
                case "bracket":
                    return BRACKET_FORMAT
                case "xml":
                    return XML_FORMAT
                case "native":
                    return None  # Native calls are already structured by the provider.
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
        system_prompt = self._build_aggregated_prompt()
        tool_format_name = (
            self.config.tool_format
            if isinstance(self.config.tool_format, str)
            else "custom"
        )

        # Retain immutable settings for controllers created by parallel turns.
        self._controller_config = ControllerConfig(
            system_prompt=system_prompt,
            include_job_status=True,
            include_tools_list=False,  # The aggregated prompt already contains tools.
            include_subagent_schema_guidance=self.config.include_tools_in_prompt,
            max_messages=self.config.max_messages,
            ephemeral=self.config.ephemeral,
            known_outputs=getattr(self, "_known_outputs", set()),
            tool_format=tool_format_name,
            sanitize_orphan_tool_calls=self.config.sanitize_orphan_tool_calls,
        )

        # The primary controller also owns framework command dispatch.
        self.controller = self._create_controller()
        if getattr(self, "plugins", None):
            self.controller.plugins = self.plugins
            self._apply_plugin_hooks()

    def _build_aggregated_prompt(self) -> str:
        """Build a system prompt from the current registry and runtime context."""
        # The aggregator adds tools and framework hints; system.md remains agent-specific.
        base_prompt = self.config.system_prompt

        # Sub-agent inventory follows the same prompt-inclusion switch as tools.
        if self.config.include_tools_in_prompt:
            subagents_prompt = self.subagent_manager.get_subagents_prompt()
            if subagents_prompt:
                base_prompt = base_prompt + "\n\n" + subagents_prompt

        known_outputs = getattr(self, "_known_outputs", set())

        self._tool_format = self._resolve_tool_format()
        tool_format_name = (
            self.config.tool_format
            if isinstance(self.config.tool_format, str)
            else "custom"
        )

        # Creature hint overrides take precedence over package defaults.
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
        plugin_context = None
        if getattr(self, "plugins", None):
            plugin_context = PluginContext(
                agent_name=self.config.name,
                working_dir=(
                    Path(self.executor._working_dir) if self.executor else Path.cwd()
                ),
                model=getattr(self.llm, "model", ""),
                _host_agent=self,
            )
        # Supply standard runtime variables expected by prompt templates.
        prompt_extra_context: dict = {
            "agent_name": self.config.name,
            "creature_name": self.config.name,
            "pwd": str(self.executor._working_dir) if self.executor else "",
            "model": getattr(self.llm, "model", ""),
        }
        return aggregate_system_prompt(
            base_prompt,
            self.registry,
            include_tools=self.config.include_tools_in_prompt,
            include_hints=self.config.include_hints_in_prompt,
            skill_mode=self.config.skill_mode,
            tool_format=tool_format_name,
            known_outputs=known_outputs,
            extra_context=prompt_extra_context,
            framework_hint_overrides=hint_overrides or None,
            skill_registry=getattr(self, "skills", None),
            skill_index_budget_bytes=getattr(
                self.config, "skill_index_budget_bytes", 4096
            ),
            runtime_plugins=getattr(self, "plugins", None),
            plugin_context=plugin_context,
        )

    def _ensure_skill_tool_registered(self) -> None:
        """Expose procedural skills through the normal tool registry."""
        if self.registry.get_tool("skill") is not None:
            return
        tool = get_builtin_tool("skill")
        if tool is None:
            try:
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

        commands = getattr(self, "_user_command_registry", None)
        if not commands:
            commands = {}
            for name in list_builtin_user_commands():
                cmd = get_builtin_user_command(name)
                if cmd:
                    commands[name] = cmd
            commands.update(getattr(self, "_extra_user_commands", {}) or {})
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
        """Create a controller sharing the agent's runtime components."""
        controller = Controller(
            self.llm,
            self._controller_config,
            executor=self.executor,
            registry=self.registry,
        )
        if hasattr(self, "output_router"):
            controller.output_router = self.output_router
        if getattr(self, "plugins", None):
            controller.plugins = self.plugins
        skill_registry = getattr(self, "skills", None)
        if skill_registry is not None:
            controller.register_command("skill", SkillCommand(skill_registry))
            if hasattr(controller, "_context"):
                controller._context.skills_registry = skill_registry
        # Tool documentation overrides resolve relative to the creature config.
        if hasattr(controller, "_context"):
            controller._context.agent_path = getattr(self.config, "agent_path", None)
        return controller

    def _init_skills(self) -> None:
        """Discover procedural skills and persist their enable state in the session.

        Project, user, creature, and package locations are scanned in priority
        order, with later discoveries replacing earlier names.
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

        # Session metadata exposes skills to plugins and Studio without an agent handle.
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

        # Named outputs constrain parser routing targets.
        self._known_outputs = set(named_outputs.keys())
        logger.info("Named outputs registered", named_outputs=list(self._known_outputs))

        self.output_router = OutputRouter(default_output, named_outputs=named_outputs)

    def _init_user_commands(self) -> None:
        """Aggregate every user-command source and bind it to the input module."""
        commands, provenance = self._aggregate_user_commands()
        self._user_command_registry = commands
        self._user_command_provenance = provenance
        context = UserCommandContext(
            agent=self,
            session=getattr(self, "session", None),
            input_module=self.input,
        )
        self._user_command_context = context
        if hasattr(self.input, "set_user_commands"):
            self.input.set_user_commands(commands, context)

    def _aggregate_user_commands(
        self,
    ) -> tuple[dict[str, Any], dict[str, CommandProvenance]]:
        """Combine command sources while requiring a unique explicit override.

        Constructor-injected commands override by default so applications can
        replace built-ins deliberately.
        """
        contributions: list[CommandContribution] = []
        for name in list_builtin_user_commands():
            cmd = get_builtin_user_command(name)
            if cmd is not None:
                contributions.append(
                    CommandContribution(
                        name=name,
                        command=cmd,
                        provenance=CommandProvenance(source="builtin"),
                        override=bool(getattr(cmd, "override", False)),
                    )
                )
        contributions.extend(self._load_package_user_command_contributions())
        for name, cmd in (getattr(self, "_extra_user_commands", {}) or {}).items():
            contributions.append(
                CommandContribution(
                    name=name,
                    command=cmd,
                    provenance=CommandProvenance(source="constructor"),
                    override=True,
                )
            )
        plugins = getattr(self, "plugins", None)
        if plugins is not None:
            contributions.extend(plugins.collect_user_commands())
        return aggregate_user_commands(contributions)

    def _load_package_user_command_contributions(self) -> list[CommandContribution]:
        """Load package commands while isolating failures to the broken package."""
        out: list[CommandContribution] = []
        for pkg_name, entry in iter_package_user_command_entries():
            name = entry.get("name")
            module = entry.get("module", "")
            class_name = entry.get("class") or entry.get("class_name", "")
            if not name or not module or not class_name:
                continue
            try:
                ensure_package_importable(pkg_name)
                mod = importlib.import_module(module)
                cmd = getattr(mod, class_name)()
            except Exception as exc:
                logger.warning(
                    "Failed to load package user command",
                    command=name,
                    package=pkg_name,
                    module_path=module,
                    class_name=class_name,
                    error=str(exc),
                    exc_info=True,
                )
                continue
            out.append(
                CommandContribution(
                    name=name,
                    command=cmd,
                    provenance=CommandProvenance(source="package", origin=pkg_name),
                    override=bool(getattr(cmd, "override", False)),
                )
            )
        return out

    def refresh_user_commands(self) -> dict[str, Any]:
        """Rebuild commands and notify every live input or inventory listener."""
        commands, provenance = self._aggregate_user_commands()
        self._user_command_registry = commands
        self._user_command_provenance = provenance
        context = getattr(self, "_user_command_context", None)
        if context is None:
            context = UserCommandContext(
                agent=self,
                session=getattr(self, "session", None),
                input_module=getattr(self, "input", None),
            )
            self._user_command_context = context
        input_module = getattr(self, "input", None)
        if input_module is not None and hasattr(input_module, "set_user_commands"):
            input_module.set_user_commands(commands, context)
        for listener in list(getattr(self, "_user_command_listeners", []) or []):
            try:
                listener(commands)
            except Exception as exc:  # pragma: no cover - listeners are isolated
                logger.warning(
                    "user-command listener failed", error=str(exc), exc_info=True
                )
        return commands

    def list_user_commands(self) -> dict[str, Any]:
        """Return a copy of the live aggregated command registry."""
        return dict(getattr(self, "_user_command_registry", {}) or {})

    def add_user_command_listener(self, listener: "Callable[[dict], None]") -> None:
        """Register a listener that keeps interactive command inventories current."""
        listeners = getattr(self, "_user_command_listeners", None)
        if listeners is None:
            listeners = []
            self._user_command_listeners = listeners
        if listener not in listeners:
            listeners.append(listener)

    def _init_triggers(self) -> None:
        """Initialize trigger modules from config into trigger_manager."""
        session = getattr(self, "session", None)
        init_triggers(self.config, self.trigger_manager, session, self._loader)
