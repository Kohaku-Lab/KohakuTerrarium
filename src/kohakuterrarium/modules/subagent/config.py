"""
Sub-agent configuration.

Defines configuration for sub-agents including tool access, execution limits,
and output routing.
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class OutputTarget(Enum):
    """Where sub-agent output goes."""

    CONTROLLER = "controller"  # Return to parent controller (default)
    EXTERNAL = "external"  # Stream directly to user/output


class ContextUpdateMode(Enum):
    """How interactive sub-agents handle context updates."""

    INTERRUPT_RESTART = "interrupt_restart"  # Stop current, start new response
    QUEUE_APPEND = "queue_append"  # Queue updates, process after current
    FLUSH_REPLACE = "flush_replace"  # Flush output, replace context immediately


@dataclass
class SubAgentConfig:
    """
    Configuration for a sub-agent.

    Attributes:
        name: Sub-agent identifier
        description: One-line description for controller
        tools: List of allowed tool names
        system_prompt: System prompt for the sub-agent
        prompt_file: Path to system prompt file (relative to agent folder)
        extra_prompt: Extra prompt text appended to base prompt
        extra_prompt_file: Path to extra prompt file (relative to agent folder)
        can_modify: Whether sub-agent can modify files
        stateless: No persistent state between calls
        interactive: Receives ongoing context updates from parent
        context_mode: How to handle context updates (for interactive agents)
        output_to: Where output goes (controller or external)
        output_module: Output module name (if output_to=external)
        return_as_context: Return output text to parent controller as context
        max_turns: Maximum conversation turns
        timeout: Maximum execution time in seconds
        model: LLM model to use (None = inherit from parent)
        temperature: LLM temperature (None = inherit from parent)
        memory_path: Path to memory folder (for memory sub-agents)
        notify_controller_on_background_complete: Whether background completion
            should push a new event back into the parent controller loop
        extra: Additional configuration
    """

    name: str
    description: str = ""
    tools: list[str] = field(default_factory=list)
    system_prompt: str = ""
    prompt_file: str | None = None
    extra_prompt: str = ""
    extra_prompt_file: str | None = None
    can_modify: bool = False
    stateless: bool = True
    interactive: bool = False
    context_mode: ContextUpdateMode = ContextUpdateMode.INTERRUPT_RESTART
    output_to: OutputTarget = OutputTarget.CONTROLLER
    output_module: str | None = None
    return_as_context: bool = False  # Return output text to parent as context
    max_turns: int = 0  # 0 = unlimited (agent runs until task is done)
    timeout: float = 0  # 0 = no timeout
    # Plugin packs to opt into (e.g. ``["auto-compact"]``). Budget axes
    # are NOT a sub-agent core field — declare the ``budget`` plugin via
    # ``plugins`` with its own options if you want one.
    default_plugins: list[str] = field(default_factory=list)
    # Per-instance plugin entries (``[{name, module, class, options}, …]``).
    plugins: list[dict[str, Any]] = field(default_factory=list)
    compact: dict[str, Any] | None = None
    model: str | None = None
    temperature: float | None = None
    memory_path: str | None = None
    modifying_tools: set[str] | None = None
    tool_format: str | None = None  # None = inherit from parent
    notify_controller_on_background_complete: bool = True
    # Shared-iteration-budget controls (legacy ``max_iterations`` path,
    # distinct from the runtime ``budget`` plugin).
    # - budget_inherit=True (default): the child reuses the parent's
    #   IterationBudget reference — every child turn decrements the
    #   same pool the parent draws from.
    # - budget_allocation=N: hand the child a fresh isolated budget of
    #   N turns; parent's remaining counter is untouched.
    # - budget_inherit=False and allocation=None: today's behavior,
    #   child runs unbounded (or bounded by its own ``max_turns``).
    budget_inherit: bool = True
    budget_allocation: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def load_prompt(self, agent_path: Path | None = None) -> str:
        """
        Load system prompt from file or use inline prompt.

        Resolution order:
        1. If system_prompt is set -> use it entirely (full override, backward compat)
        2. If extra_prompt or extra_prompt_file is set -> base prompt + extra
        3. If neither -> base prompt only

        Args:
            agent_path: Base path for resolving prompt_file

        Returns:
            System prompt string with path context injected
        """
        prompt = ""

        if self.system_prompt:
            prompt = self.system_prompt
        elif self.prompt_file and agent_path:
            prompt_path = agent_path / self.prompt_file
            if prompt_path.exists():
                prompt = prompt_path.read_text(encoding="utf-8")
        else:
            prompt = f"You are a {self.name} sub-agent."

        # Append extra prompt if set (only when system_prompt is NOT a full override)
        # When system_prompt is explicitly set, it's a full override - don't append extra
        if not self.system_prompt:
            extra = self.extra_prompt
            if self.extra_prompt_file and agent_path:
                extra_path = agent_path / self.extra_prompt_file
                if extra_path.exists():
                    extra = extra_path.read_text(encoding="utf-8")

            if extra:
                prompt = f"{prompt}\n\n## Additional Instructions\n\n{extra}"

        # Inject path context if memory_path is set
        if self.memory_path and agent_path:
            full_memory_path = agent_path / self.memory_path
            path_context = f"\n\n## Path Context\nMemory folder path: `{full_memory_path}`\nUse this exact path when calling tools.\n"
            prompt = prompt + path_context

        return prompt

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SubAgentConfig":
        """Create config from dictionary (e.g., YAML config)."""
        # Handle enum conversions
        if "output_to" in data and isinstance(data["output_to"], str):
            data["output_to"] = OutputTarget(data["output_to"])
        if "context_mode" in data and isinstance(data["context_mode"], str):
            data["context_mode"] = ContextUpdateMode(data["context_mode"])

        data = dict(data)

        # Convert modifying_tools list to set if present
        if "modifying_tools" in data and isinstance(data["modifying_tools"], list):
            data["modifying_tools"] = set(data["modifying_tools"])

        # Filter to known fields
        known_fields = {
            "name",
            "description",
            "tools",
            "system_prompt",
            "prompt_file",
            "extra_prompt",
            "extra_prompt_file",
            "can_modify",
            "stateless",
            "interactive",
            "context_mode",
            "output_to",
            "output_module",
            "return_as_context",
            "max_turns",
            "timeout",
            "default_plugins",
            "plugins",
            "compact",
            "model",
            "temperature",
            "memory_path",
            "modifying_tools",
            "tool_format",
            "notify_controller_on_background_complete",
            "budget_inherit",
            "budget_allocation",
            "extra",
        }

        filtered = {k: v for k, v in data.items() if k in known_fields}
        extra = {k: v for k, v in data.items() if k not in known_fields}

        if extra:
            filtered.setdefault("extra", {}).update(extra)

        return cls(**filtered)

    def to_dict(self) -> dict[str, Any]:
        """Return a plain dict representation for tests and tooling."""
        data = dict(self.__dict__)
        data["context_mode"] = self.context_mode.value
        data["output_to"] = self.output_to.value
        if self.modifying_tools is not None:
            data["modifying_tools"] = sorted(self.modifying_tools)
        return data


@dataclass
class SubAgentInfo:
    """
    Sub-agent information for registration and system prompt.

    Lightweight representation for listing available sub-agents.
    """

    name: str
    description: str
    can_modify: bool = False
    interactive: bool = False

    def to_prompt_line(self) -> str:
        """Format for system prompt sub-agent list."""
        suffix = ""
        if self.can_modify:
            suffix = " [can modify files]"
        if self.interactive:
            suffix = " [interactive]"
        return f"- {self.name}: {self.description}{suffix}"

    @classmethod
    def from_config(cls, config: SubAgentConfig) -> "SubAgentInfo":
        """Create info from config."""
        return cls(
            name=config.name,
            description=config.description,
            can_modify=config.can_modify,
            interactive=config.interactive,
        )
