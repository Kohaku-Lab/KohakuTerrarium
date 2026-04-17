"""Agent configuration type definitions."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class InputConfig:
    """Configuration for input module."""

    type: str = "cli"  # builtin type or "custom"/"package"
    module: str | None = None  # For custom: "./custom/input.py", for package: "pkg.mod"
    class_name: str | None = None  # Class name to instantiate
    prompt: str = "> "
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class TriggerConfig:
    """Configuration for a trigger."""

    type: str  # builtin type (timer, idle, etc.) or "custom"/"package"
    module: str | None = None  # For custom: "./custom/trigger.py"
    class_name: str | None = None  # Class name to instantiate
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
    module: str | None = None  # For custom: "./custom/tools/my_tool.py"
    class_name: str | None = None  # Class name to instantiate
    doc: str | None = None  # Override skill doc path
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class OutputConfigItem:
    """Configuration for a single output module."""

    type: str = "stdout"  # builtin type or "custom"/"package"
    module: str | None = None  # For custom: "./custom/output.py"
    class_name: str | None = None  # Class name to instantiate
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class OutputConfig:
    """Configuration for output modules."""

    # Default output (for model "thinking" / stdout)
    type: str = "stdout"  # builtin type or "custom"/"package"
    module: str | None = None  # For custom: "./custom/output.py"
    class_name: str | None = None  # Class name to instantiate
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
    module: str | None = None  # For custom: "./custom/subagents/my_agent.py"
    config_name: str | None = (
        None  # Config object name in module (e.g., "MY_AGENT_CONFIG")
    )
    description: str | None = None
    tools: list[str] = field(default_factory=list)
    can_modify: bool = False
    interactive: bool = False  # Whether agent stays alive for context updates
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentConfig:
    """
    Complete configuration for an agent.

    Loaded from a config file (YAML/JSON/TOML) in the agent folder.
    """

    name: str
    version: str = "1.0"

    # Inheritance: path to base creature/agent config directory
    base_config: str | None = None

    # LLM profile reference (resolves from ~/.kohakuterrarium/llm_profiles.yaml)
    llm_profile: str = ""  # Profile name; empty = use inline settings or default

    # LLM settings (inline, backward compat; overridden by llm_profile if set)
    model: str = ""  # empty = resolve via profile system
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

    # System prompt (loaded from file or inline)
    system_prompt: str = "You are a helpful assistant."
    system_prompt_file: str | None = None

    # Files to inject into system prompt as template variables
    # Maps variable name to file path (relative to agent folder)
    # Example: { "character": "memory/character.md" }
    # Use in system.md: {{ character }}
    prompt_context_files: dict[str, str] = field(default_factory=dict)

    # Skill loading mode: "dynamic" or "static"
    # - dynamic: Model uses [/info] to read tool docs on demand (less tokens upfront)
    # - static: All tool docs included in system prompt (no [/info] needed)
    skill_mode: str = "dynamic"

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

    # Module configs
    input: InputConfig = field(default_factory=InputConfig)
    triggers: list[TriggerConfig] = field(default_factory=list)
    tools: list[ToolConfigItem] = field(default_factory=list)
    subagents: list[SubAgentConfigItem] = field(default_factory=list)
    output: OutputConfig = field(default_factory=OutputConfig)

    # Auto-compact config (dict with max_tokens, threshold, target, keep_recent_turns)
    compact: dict[str, Any] | None = None

    # Startup trigger (fires once when agent starts)
    startup_trigger: dict[str, Any] | None = None

    # Termination conditions
    termination: dict[str, Any] | None = None  # Raw termination config dict

    # Sub-agent depth limit (0 = unlimited)
    max_subagent_depth: int = 3

    # Tool call format: "bracket", "xml", "native", or custom dict
    tool_format: str | dict = "bracket"

    # Path to agent folder
    agent_path: Path | None = None

    # Session key for shared state isolation (None = use agent name)
    session_key: str | None = None

    # MCP server configurations (connected on agent start)
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)

    # Plugin configurations (loaded during agent init)
    plugins: list[dict[str, Any]] = field(default_factory=list)

    # Memory / embedding configuration
    memory: dict[str, Any] = field(default_factory=dict)

    def get_api_key(self) -> str | None:
        """Get API key from environment."""
        return os.environ.get(self.api_key_env)


# Environment variable pattern: ${VAR} or ${VAR:default}
ENV_VAR_PATTERN = re.compile(r"\$\{([^}:]+)(?::([^}]*))?\}")


def _interpolate_env_vars(value: Any) -> Any:
    """Recursively interpolate environment variables in config values."""
    if isinstance(value, str):

        def replace_env(match: re.Match) -> str:
            var_name = match.group(1)
            default = match.group(2)
            return os.environ.get(var_name, default if default is not None else "")

        return ENV_VAR_PATTERN.sub(replace_env, value)
    elif isinstance(value, dict):
        return {k: _interpolate_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_interpolate_env_vars(v) for v in value]
    return value
