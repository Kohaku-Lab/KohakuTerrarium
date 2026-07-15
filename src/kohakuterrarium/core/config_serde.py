"""AgentConfig <-> primitive-dict (de)serialization.

Keeping these helpers dependent only on configuration data types avoids
an import cycle between session resume and the terrarium engine. The wire
module re-exports them for compatibility with its existing DTO surface.
"""

from dataclasses import asdict, fields
from pathlib import Path
from typing import Any

from kohakuterrarium.core.config_types import (
    AgentConfig,
    InputConfig,
    OutputConfig,
    OutputConfigItem,
    SubAgentConfigItem,
    ToolConfigItem,
    TriggerConfig,
)
from kohakuterrarium.core.output_wiring import OutputWiringEntry

_NESTED_LIST_FIELDS: dict[str, type] = {
    "triggers": TriggerConfig,
    "tools": ToolConfigItem,
    "subagents": SubAgentConfigItem,
    "output_wiring": OutputWiringEntry,
}


def stringify_paths(value: Any) -> Any:
    """Recursively convert Path / tuple values to wire-safe primitives."""
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {k: stringify_paths(v) for k, v in value.items()}
    if isinstance(value, list):
        return [stringify_paths(v) for v in value]
    if isinstance(value, tuple):
        return [stringify_paths(v) for v in value]
    return value


def pack_agent_config(c: AgentConfig) -> dict[str, Any]:
    """Convert an agent configuration to wire-safe primitives."""
    return stringify_paths(asdict(c))


def unpack_agent_config(d: dict[str, Any]) -> AgentConfig:
    """Reconstruct an agent configuration from wire-safe primitives."""
    payload = dict(d)
    known = {f.name for f in fields(AgentConfig)}
    payload = {k: v for k, v in payload.items() if k in known}

    if "input" in payload and isinstance(payload["input"], dict):
        payload["input"] = InputConfig(**payload["input"])
    if "output" in payload and isinstance(payload["output"], dict):
        out_dict = dict(payload["output"])
        named = out_dict.pop("named_outputs", {}) or {}
        out = OutputConfig(**out_dict)
        out.named_outputs = {name: OutputConfigItem(**v) for name, v in named.items()}
        payload["output"] = out
    for name, cls in _NESTED_LIST_FIELDS.items():
        if name in payload and isinstance(payload[name], list):
            payload[name] = [
                cls(**item) if isinstance(item, dict) else item
                for item in payload[name]
            ]
    if payload.get("agent_path") is not None and isinstance(payload["agent_path"], str):
        payload["agent_path"] = Path(payload["agent_path"])
    return AgentConfig(**payload)


__all__ = [
    "pack_agent_config",
    "stringify_paths",
    "unpack_agent_config",
]
