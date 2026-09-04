"""Agent configuration type definitions."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kohakuterrarium.core.output_wiring import OutputWiringEntry
from kohakuterrarium.modules.tool.doc_mode import DEFAULT_DOC_MODE


@dataclass
class InputConfig:
    """Configuration for input module."""

    type: str = "cli"  # builtin type or "custom"/"package"
    module: str | None = None  # Custom file path or package module reference.
    class_name: str | None = None  # Class instantiated from ``module``.
    prompt: str = "> "
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class TriggerConfig:
    """Configuration for a trigger."""

    type: str  # builtin type (timer, idle, etc.) or "custom"/"package"
    module: str | None = None  # Custom file path or package module reference.
    class_name: str | None = None  # Class instantiated from ``module``.
    prompt: str | None = None
    options: dict[str, Any] = field(default_factory=dict)
    # Optional stable identity — used as trigger_id and as the identity key for
    # inheritance (child-wins override of a base trigger with the same name).
    name: str | None = None


@dataclass
class ToolConfigItem:
    """Configuration for a tool."""

    name: str
    type: str = "builtin"  # "builtin", "custom", or "package"
    module: str | None = None  # Custom file path or package module reference.
    class_name: str | None = None  # Class instantiated from ``module``.
    options: dict[str, Any] = field(default_factory=dict)
    # Per-tool documentation tier; None defers to the creature default.
    doc_mode: str | None = None


@dataclass
class OutputConfigItem:
    """Configuration for a single output module."""

    type: str = "stdout"  # builtin type or "custom"/"package"
    module: str | None = None  # Custom file path or package module reference.
    class_name: str | None = None  # Class instantiated from ``module``.
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class OutputConfig:
    """Configuration for output modules."""

    # Default output (for model "thinking" / stdout)
    type: str = "stdout"  # builtin type or "custom"/"package"
    module: str | None = None  # Custom file path or package module reference.
    class_name: str | None = None  # Class instantiated from ``module``.
    controller_direct: bool = True
    options: dict[str, Any] = field(default_factory=dict)

    # Named outputs for explicit [/output_<name>] blocks
    # Maps name -> OutputConfigItem (e.g., {"discord": OutputConfigItem(...)})
    named_outputs: dict[str, OutputConfigItem] = field(default_factory=dict)


@dataclass
class SubAgentConfigItem:
    """Configuration for a sub-agent."""

    name: str
    type: str = "builtin"  # "builtin", "custom", or "package"
    module: str | None = None  # Custom file path or package module reference.
    config_name: str | None = (
        None  # Selects among configuration objects exposed by a custom module.
    )
    description: str | None = None
    tools: list[str] = field(default_factory=list)
    can_modify: bool = False
    interactive: bool = False  # Whether agent stays alive for context updates
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentConfig:
    """Complete runtime configuration for an agent."""

    name: str
    version: str = "1.0"

    # LLM profile reference (resolves from ~/.kohakuterrarium/llm_profiles.yaml)
    llm_profile: str = (
        ""  # Profile name or selector; empty = use inline settings or default
    )

    # LLM settings (inline, backward compat; overridden by llm_profile if set)
    model: str = ""  # empty = resolve via profile system
    provider: str = ""  # optional provider disambiguator when model is set
    variation_selections: dict[str, str] = field(default_factory=dict)
    variation: str = ""  # optional shorthand; normalized into variation_selections
    auth_mode: str = ""  # empty = resolve via profile; "codex-oauth" for ChatGPT sub
    api_key_env: str = ""  # empty = resolve via profile
    base_url: str = ""  # empty = resolve via profile
    temperature: float = 0.7
    max_tokens: int | None = None  # None = let the API decide
    reasoning_effort: str = "medium"  # none/minimal/low/medium/high/xhigh
    service_tier: str | None = None  # None/priority/flex
    extra_body: dict[str, Any] = field(
        default_factory=dict
    )  # extra fields merged into API request body
    retry_policy: dict[str, Any] | None = None

    # System prompt (loaded from file or inline)
    system_prompt: str = "You are a helpful assistant."
    system_prompt_file: str | None = None

    # Maps a system.md template variable to a file path, agent-folder relative.
    prompt_context_files: dict[str, str] = field(default_factory=dict)

    # Default documentation tier for tools: "brief", "standard", or "full".
    # See ``modules/tool/doc_mode.py``; individual tools may override it.
    tool_doc_mode: str = DEFAULT_DOC_MODE

    # Prompt aggregation controls
    # Set to False if you handle tool/output instructions in your own prompt/context
    include_tools_in_prompt: bool = True  # Add tool list to system prompt
    include_hints_in_prompt: bool = (
        True  # Add framework hints (output format, function calling)
    )

    # Context management - limits LLM conversation history
    max_messages: int = 0  # Max messages to keep (0 = unlimited)
    ephemeral: bool = (
        False  # Clear conversation after each interaction (for group chat)
    )

    # Drop orphan tool_call / tool-result pairs left by compaction.
    sanitize_orphan_tool_calls: bool = True

    input: InputConfig = field(default_factory=InputConfig)
    triggers: list[TriggerConfig] = field(default_factory=list)
    tools: list[ToolConfigItem] = field(default_factory=list)
    subagents: list[SubAgentConfigItem] = field(default_factory=list)
    output: OutputConfig = field(default_factory=OutputConfig)

    # Provider-native tools to skip, e.g. ``["image_gen"]`` on Codex.
    disable_provider_tools: list[str] = field(default_factory=list)

    compact: dict[str, Any] | None = None

    startup_trigger: dict[str, Any] | None = None

    termination: dict[str, Any] | None = None

    # Sub-agent depth limit (0 = unlimited)
    max_subagent_depth: int = 3

    # LLM-turn pool shared with budget_inherit sub-agents (None / 0 = off).
    max_iterations: int | None = None

    # Runtime default plugin packs, e.g. ``["auto-compact"]``.
    default_plugins: list[str] = field(default_factory=list)

    # "native" (provider function calling), "bracket", "xml", or a custom dict.
    tool_format: str | dict = "native"

    agent_path: Path | None = None

    # Session key for shared state isolation (None = use agent name)
    session_key: str | None = None

    mcp_servers: list[dict[str, Any]] = field(default_factory=list)

    plugins: list[dict[str, Any]] = field(default_factory=list)

    memory: dict[str, Any] = field(default_factory=dict)

    # Targets that receive a ``creature_output`` TriggerEvent at turn-end.
    output_wiring: list[OutputWiringEntry] = field(default_factory=list)

    # Package-shipped skills enabled by default; local skills always are.
    skills: list[str] = field(default_factory=list)

    # Byte budget for the ``## Skills`` section; overflow stays callable.
    skill_index_budget_bytes: int = 4096

    # Canonical hint key -> replacement prose ("" omits the block).
    # See ``prompt/framework_hints.py`` for keys and precedence.
    framework_hint_overrides: dict[str, str] = field(default_factory=dict)

    def get_api_key(self) -> str | None:
        """Return the configured API key from the environment."""
        return os.environ.get(self.api_key_env)
