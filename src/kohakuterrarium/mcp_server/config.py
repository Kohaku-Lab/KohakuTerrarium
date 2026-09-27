"""Direct tool settings and explicit local delegation target registration."""

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

SUPPORTED_TOOLS = (
    "read",
    "write",
    "edit",
    "multi_edit",
    "glob",
    "grep",
    "tree",
    "bash",
    "python",
)


class ToolSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: Literal[
        "read", "write", "edit", "multi_edit", "glob", "grep", "tree", "bash", "python"
    ]
    type: Literal["builtin"] = "builtin"
    config: dict[str, Any] = Field(default_factory=dict)


class PluginSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, strict=True)
    name: str
    type: Literal["custom", "package"] = "package"
    module: str | None = None
    class_name: str | None = Field(default=None, alias="class")
    options: dict[str, Any] = Field(default_factory=dict)


class DelegationTarget(BaseModel):
    """A locally registered execution definition, independent of session state."""

    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["creature", "subagent"]
    config: str = Field(min_length=1)
    description: str = ""


class GlobalToolsConfig(BaseModel):
    """No model, triggers, prompt, compact, or AgentConfig inheritance."""

    model_config = ConfigDict(extra="forbid")
    name: str = "KT tools"
    pwd_guard: Literal["warn", "block", "off"] = "warn"
    tools: list[ToolSpec] = Field(
        default_factory=lambda: [ToolSpec(name=n) for n in SUPPORTED_TOOLS]
    )
    plugins: list[PluginSpec] = Field(default_factory=list)
    delegation: dict[str, DelegationTarget] = Field(default_factory=dict)

    @field_validator("tools")
    @classmethod
    def unique_tools(cls, values: list[ToolSpec]) -> list[ToolSpec]:
        if len({v.name for v in values}) != len(values):
            raise ValueError("duplicate tool name")
        return values

    @field_validator("delegation")
    @classmethod
    def named_targets(cls, values: dict[str, DelegationTarget]):
        if any(not name.strip() or name != name.strip() for name in values):
            raise ValueError(
                "Delegation target names must be nonempty without surrounding whitespace"
            )
        return values


class MCPToolsConfig(GlobalToolsConfig):
    """Execution settings bound to one registered default directory."""

    workspace: Path

    @field_validator("workspace")
    @classmethod
    def existing_directory(cls, value: Path) -> Path:
        value = value.resolve()
        if not value.is_dir():
            raise ValueError("workspace must be an existing directory")
        return value


def load_global_config(path: Path) -> GlobalToolsConfig:
    """Read global tool settings, resolving module and target paths at the file."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "workspace" in data:
        raise ValueError("Global MCP configuration must be an object without workspace")
    config = GlobalToolsConfig.model_validate(data)
    for target in config.delegation.values():
        if not target.config.startswith("@"):
            target.config = str(
                (path.resolve().parent / Path(target.config).expanduser()).resolve()
            )
    for plugin in config.plugins:
        if plugin.module and not plugin.module.startswith("@"):
            plugin.module = str(
                (path.resolve().parent / Path(plugin.module).expanduser()).resolve()
            )
    return config


def load_config(path: Path) -> MCPToolsConfig:
    """Read a dedicated YAML/JSON document; relative workspace is file-relative."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("MCP configuration must be an object")
    if isinstance(data.get("workspace"), str):
        data["workspace"] = path.resolve().parent / data["workspace"]
    config = MCPToolsConfig.model_validate(data)
    for target in config.delegation.values():
        if not target.config.startswith("@"):
            target.config = str(
                (path.resolve().parent / Path(target.config).expanduser()).resolve()
            )
    return config
