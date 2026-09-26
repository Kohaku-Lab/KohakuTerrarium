"""Strict tool-only configuration using the existing tool option contract."""

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


class MCPToolsConfig(BaseModel):
    """No model, triggers, prompt, compact, or AgentConfig inheritance."""

    model_config = ConfigDict(extra="forbid")
    name: str = "KT tools"
    workspace: Path
    pwd_guard: Literal["warn", "block", "off"] = "warn"
    tools: list[ToolSpec] = Field(
        default_factory=lambda: [ToolSpec(name=n) for n in SUPPORTED_TOOLS]
    )
    plugins: list[PluginSpec] = Field(default_factory=list)

    @field_validator("workspace")
    @classmethod
    def existing_directory(cls, value: Path) -> Path:
        value = value.resolve()
        if not value.is_dir():
            raise ValueError("workspace must be an existing directory")
        return value

    @field_validator("tools")
    @classmethod
    def unique_tools(cls, values: list[ToolSpec]) -> list[ToolSpec]:
        if len({v.name for v in values}) != len(values):
            raise ValueError("duplicate tool name")
        return values


def load_config(path: Path) -> MCPToolsConfig:
    """Read a dedicated YAML/JSON document; relative workspace is file-relative."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("MCP configuration must be an object")
    if isinstance(data.get("workspace"), str):
        data["workspace"] = path.resolve().parent / data["workspace"]
    return MCPToolsConfig.model_validate(data)
