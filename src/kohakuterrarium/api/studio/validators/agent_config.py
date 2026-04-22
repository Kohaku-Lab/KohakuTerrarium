"""Pydantic mirror of ``core.config_types.AgentConfig``.

Used at the HTTP boundary to validate creature-save bodies. Not
the runtime representation — the framework continues to load
configs through its own dataclass system. Keep fields + defaults
in sync with ``kohakuterrarium/core/config_types.py``.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class InputConfigIn(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str = "cli"
    module: str | None = None
    class_name: str | None = None
    prompt: str = "> "
    options: dict[str, Any] = Field(default_factory=dict)


class TriggerConfigIn(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str
    module: str | None = None
    class_name: str | None = None
    prompt: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    name: str | None = None


class ToolConfigItemIn(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    type: str = "builtin"
    module: str | None = None
    class_name: str | None = None
    doc: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)


class SubAgentConfigItemIn(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    type: str = "builtin"
    module: str | None = None
    config_name: str | None = None
    description: str | None = None
    tools: list[str] = Field(default_factory=list)
    can_modify: bool = False
    interactive: bool = False
    options: dict[str, Any] = Field(default_factory=dict)


class OutputConfigItemIn(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str = "stdout"
    module: str | None = None
    class_name: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)


class OutputConfigIn(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str = "stdout"
    module: str | None = None
    class_name: str | None = None
    controller_direct: bool = True
    options: dict[str, Any] = Field(default_factory=dict)
    named_outputs: dict[str, OutputConfigItemIn] = Field(default_factory=dict)


class AgentConfigIn(BaseModel):
    """Validate a creature config being saved via the studio API."""

    model_config = ConfigDict(extra="allow")

    name: str
    version: str = "1.0"
    base_config: str | None = None

    llm_profile: str = ""
    model: str = ""
    provider: str = ""
    variation_selections: dict[str, str] = Field(default_factory=dict)
    variation: str = ""
    auth_mode: str = ""
    api_key_env: str = ""
    base_url: str = ""
    temperature: float = 0.7
    max_tokens: int | None = None
    reasoning_effort: str = "medium"
    service_tier: str | None = None
    extra_body: dict[str, Any] = Field(default_factory=dict)

    system_prompt: str = "You are a helpful assistant."
    system_prompt_file: str | None = None
    prompt_context_files: dict[str, str] = Field(default_factory=dict)

    skill_mode: str = "dynamic"
    include_tools_in_prompt: bool = True
    include_hints_in_prompt: bool = True

    max_messages: int = 0
    ephemeral: bool = False

    input: InputConfigIn = Field(default_factory=InputConfigIn)
    triggers: list[TriggerConfigIn] = Field(default_factory=list)
    tools: list[ToolConfigItemIn] = Field(default_factory=list)
    subagents: list[SubAgentConfigItemIn] = Field(default_factory=list)
    output: OutputConfigIn = Field(default_factory=OutputConfigIn)

    compact: dict[str, Any] | None = None
    startup_trigger: dict[str, Any] | None = None
    termination: dict[str, Any] | None = None
    max_subagent_depth: int = 3
    tool_format: str | dict = "bracket"
    session_key: str | None = None
    mcp_servers: list[dict[str, Any]] = Field(default_factory=list)
    plugins: list[dict[str, Any]] = Field(default_factory=list)
    memory: dict[str, Any] = Field(default_factory=dict)
    output_wiring: list[dict[str, Any]] = Field(default_factory=list)


def canonical_order() -> list[str]:
    """Canonical top-level key order for YAML serialization."""
    return list(AgentConfigIn.model_fields.keys())
