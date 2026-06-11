"""Agent constructor classmethods (``Agent.build`` / ``Agent.from_path``).

Split out of :mod:`agent` to keep that module under the 1000-line hard
cap — the same pattern as :mod:`agent_model` / :mod:`agent_compact`.
Both classmethods return ``cls(...)`` so subclasses construct
themselves.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any

from kohakuterrarium.builtins.inputs.none import NoneInput
from kohakuterrarium.builtins.outputs.none import NoneOutput
from kohakuterrarium.core.config import AgentConfig, load_agent_config
from kohakuterrarium.core.session import Session
from kohakuterrarium.modules.input.base import InputModule
from kohakuterrarium.modules.output.base import OutputModule

if TYPE_CHECKING:
    from kohakuterrarium.core.agent import Agent
    from kohakuterrarium.core.environment import Environment


class AgentConstructMixin:
    """Mixin providing the public construction entry points."""

    async def __aenter__(self) -> "Agent":
        """``async with await Agent.build(...) as agent`` — starts the agent.

        Idempotent on an already-started agent so the context manager
        composes with an explicit ``await agent.start()``.
        """
        if not getattr(self, "is_running", False):
            await self.start()
        return self  # type: ignore[return-value]

    async def __aexit__(self, *exc: Any) -> None:
        await self.stop()

    @classmethod
    async def build(
        cls,
        config: "AgentConfig | str | Path",
        *,
        llm: Any = None,
        pwd: str | None = None,
        io: str = "config",
        strict: bool = True,
        tools: list[Any] | None = None,
        plugins: list[Any] | None = None,
        subagents: list[Any] | None = None,
        outputs: "dict[str, OutputModule] | None" = None,
        user_commands: dict[str, Any] | None = None,
        input_module: InputModule | None = None,
        output_module: OutputModule | None = None,
        session: Session | None = None,
        environment: "Environment | None" = None,
    ) -> "Agent":
        """Build an agent — the canonical programmatic constructor.

        Args:
            config: Agent config folder path, a ``@pkg/...`` package
                reference, or an already-loaded :class:`AgentConfig`.
            llm: LLM binding — a provider instance (e.g. ``ScriptedLLM``),
                a selector string (profile / preset name or
                ``provider/model[@variations]``), an ``LLMProfile``, or
                ``None`` to resolve from the config.
            pwd: Explicit working directory (overrides process cwd).
            io: How much of the config's I/O boots — ``"config"``
                (as declared), ``"none"`` (input suppressed), or
                ``"headless"`` (input suppressed AND default output
                silenced; the batch/programmatic default choice).
                Explicit ``input_module`` / ``output_module`` win over
                ``io``.
            strict: Raise on construction problems (unresolvable LLM,
                unknown tool, broken plugin) instead of degrading
                silently.  Programmatic default; interactive frontends
                pass ``strict=False``.
            tools: Tool INSTANCES (e.g. ``kt.tool(fn)`` adapters) —
                registered before the prompt aggregates.
            plugins: Plugin INSTANCES to register (enabled).
            subagents: ``SubAgentConfig`` instances to register.
            outputs: Extra named outputs ``{name: OutputModule}``.
            user_commands: Extra slash commands ``{name: UserCommand}``.
            input_module: Custom input module (overrides config).
            output_module: Custom output module (overrides config).
            session: Explicit session (creature-private state).
            environment: Shared environment (inter-creature state).

        Returns:
            Configured Agent instance (not started — use
            ``async with agent`` or ``await agent.start()``).
        """
        if io not in ("config", "none", "headless"):
            raise ValueError(
                f"io= must be 'config', 'none', or 'headless' — got {io!r}"
            )
        if input_module is None and io in ("none", "headless"):
            input_module = NoneInput()
        if output_module is None and io == "headless":
            output_module = NoneOutput()
        if not isinstance(config, AgentConfig):
            config = load_agent_config(config)
        return cls(
            config,
            llm=llm,
            pwd=pwd,
            strict=strict,
            tools=tools,
            plugins=plugins,
            subagents=subagents,
            outputs=outputs,
            user_commands=user_commands,
            input_module=input_module,
            output_module=output_module,
            session=session,
            environment=environment,
        )

    @classmethod
    def from_path(
        cls,
        config_path: str,
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
    ) -> "Agent":
        """
        Create agent from config directory path (sync low-level
        constructor — prefer :meth:`build` in new code).

        Args:
            config_path: Path to agent config folder or ``@pkg/...`` ref
            input_module: Custom input module (overrides config)
            output_module: Custom output module (overrides config)
            session: Explicit session (creature-private state)
            environment: Shared environment (inter-creature state)
            llm: LLM binding — provider instance, selector string,
                ``LLMProfile``, or None (resolve from config)
            pwd: Explicit working directory (overrides process cwd)
            tools: Tool INSTANCES to register (e.g. ``kt.tool`` adapters)
            plugins: Plugin INSTANCES to register (enabled)

        Returns:
            Configured Agent instance
        """
        config = load_agent_config(config_path)
        return cls(
            config,
            input_module=input_module,
            output_module=output_module,
            session=session,
            environment=environment,
            llm=llm,
            pwd=pwd,
            strict=strict,
            tools=tools,
            plugins=plugins,
        )
