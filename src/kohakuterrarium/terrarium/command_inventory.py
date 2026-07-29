"""Runtime command and skill inventory for interactive clients."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class RuntimeCommand:
    """Serializable user command metadata for interactive clients."""

    name: str
    aliases: tuple[str, ...]
    description: str
    source: str
    origin: str | None
    type: Literal["command"] = "command"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "aliases": list(self.aliases),
            "description": self.description,
            "source": self.source,
            "origin": self.origin,
        }


@dataclass(frozen=True)
class RuntimeSkill:
    """Serializable skill metadata for interactive clients."""

    name: str
    description: str
    source: str
    enabled: bool
    invocation_blocked: bool
    type: Literal["skill"] = "skill"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "source": self.source,
            "enabled": self.enabled,
            "invocation_blocked": self.invocation_blocked,
        }


@dataclass(frozen=True)
class RuntimeCommandInventory:
    """Point-in-time command and skill inventory for one live creature."""

    commands: tuple[RuntimeCommand, ...]
    skills: tuple[RuntimeSkill, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "commands": [entry.to_dict() for entry in self.commands],
            "skills": [entry.to_dict() for entry in self.skills],
        }


def _command_source(provenance: Any) -> str:
    if provenance is None:
        return "runtime"
    source = getattr(provenance, "source", "runtime")
    value = getattr(source, "value", source)
    return str(value or "runtime")


def _command_origin(provenance: Any) -> str | None:
    origin = getattr(provenance, "origin", None)
    return str(origin) if origin is not None else None


def build_command_inventory(agent: Any) -> RuntimeCommandInventory:
    """Build the live command/skill inventory exposed to interactive clients."""
    commands = agent.list_user_commands()
    provenance = getattr(agent, "_user_command_provenance", {}) or {}
    command_entries = tuple(
        RuntimeCommand(
            name=name,
            aliases=tuple(getattr(command, "aliases", ()) or ()),
            description=str(getattr(command, "description", "") or ""),
            source=_command_source(provenance.get(name)),
            origin=_command_origin(provenance.get(name)),
        )
        for name, command in sorted(commands.items())
    )

    registry = getattr(agent, "skills", None)
    skills = registry.all() if registry is not None else []
    skill_entries = tuple(
        RuntimeSkill(
            name=skill.name,
            description=skill.description,
            source=skill.origin or "runtime",
            enabled=bool(skill.enabled),
            invocation_blocked=bool(skill.invocation_blocked),
        )
        for skill in sorted(skills, key=lambda item: item.name)
    )
    return RuntimeCommandInventory(commands=command_entries, skills=skill_entries)
