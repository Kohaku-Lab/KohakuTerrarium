"""Define sub-agent capabilities, prompt loading, limits, and output routing."""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class OutputTarget(Enum):
    """Where sub-agent output goes."""

    CONTROLLER = "controller"
    EXTERNAL = "external"


class ContextUpdateMode(Enum):
    """How interactive sub-agents handle context updates."""

    INTERRUPT_RESTART = "interrupt_restart"
    QUEUE_APPEND = "queue_append"
    FLUSH_REPLACE = "flush_replace"


@dataclass
class SubAgentConfig:
    """Configure one sub-agent's tools, prompts, limits, plugins, and routing."""

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
    return_as_context: bool = False
    max_turns: int = 0
    timeout: float = 0
    # Runtime budget axes belong to the budget plugin, not core sub-agent config.
    default_plugins: list[str] = field(default_factory=list)
    plugins: list[dict[str, Any]] = field(default_factory=list)
    compact: dict[str, Any] | None = None
    model: str | None = None
    temperature: float | None = None
    memory_path: str | None = None
    modifying_tools: set[str] | None = None
    tool_format: str | None = None
    notify_controller_on_background_complete: bool = True
    # Legacy iteration limits may share the parent pool or allocate an isolated one.
    budget_inherit: bool = True
    budget_allocation: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def load_prompt(self, agent_path: Path | None = None) -> str:
        """Load the prompt with inline override, extras, and optional path context."""
        prompt = ""

        if self.system_prompt:
            prompt = self.system_prompt
        elif self.prompt_file and agent_path:
            prompt_path = agent_path / self.prompt_file
            if prompt_path.exists():
                prompt = prompt_path.read_text(encoding="utf-8")
            else:
                # Missing prompt files must not leave the sub-agent without a role.
                prompt = f"You are a {self.name} sub-agent."
        else:
            prompt = f"You are a {self.name} sub-agent."

        # Inline system prompts are complete overrides and exclude extra fragments.
        if not self.system_prompt:
            extra = self.extra_prompt
            if self.extra_prompt_file and agent_path:
                extra_path = agent_path / self.extra_prompt_file
                if extra_path.exists():
                    extra = extra_path.read_text(encoding="utf-8")

            if extra:
                prompt = f"{prompt}\n\n## Additional Instructions\n\n{extra}"

        if self.memory_path and agent_path:
            full_memory_path = agent_path / self.memory_path
            path_context = f"\n\n## Path Context\nMemory folder path: `{full_memory_path}`\nUse this exact path when calling tools.\n"
            prompt = prompt + path_context

        return prompt

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SubAgentConfig":
        """Create a config from mapping data while preserving unknown fields in extra."""
        # Coercion must not mutate the caller's configuration mapping.
        data = dict(data)

        if "output_to" in data and isinstance(data["output_to"], str):
            data["output_to"] = OutputTarget(data["output_to"])
        if "context_mode" in data and isinstance(data["context_mode"], str):
            data["context_mode"] = ContextUpdateMode(data["context_mode"])

        if "modifying_tools" in data and isinstance(data["modifying_tools"], list):
            data["modifying_tools"] = set(data["modifying_tools"])

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
    """Represent the sub-agent metadata shown in prompt inventories."""

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
