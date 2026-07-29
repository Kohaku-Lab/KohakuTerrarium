"""Runtime command/skill inventory and explicit invocation resolution."""

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


@dataclass(frozen=True)
class ExplicitInvocation:
    """Resolved explicit slash invocation."""

    kind: Literal["command", "skill"]
    name: str
    requested_name: str
    value: Any


class InvocationResolutionError(ValueError):
    """Base error for explicit command/skill resolution."""

    def __init__(self, name: str, message: str) -> None:
        self.name = name
        super().__init__(message)


class DisabledSkillError(InvocationResolutionError):
    """Raised when a user explicitly requests a disabled skill."""


class UnknownInvocationError(InvocationResolutionError):
    """Raised when no command, alias, or skill matches the requested name."""


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


def resolve_explicit_invocation(agent: Any, name: str) -> ExplicitInvocation:
    """Resolve a user-selected slash target, with commands and aliases first."""
    requested = name.strip().lstrip("/")
    normalized = requested.casefold()
    commands = agent.list_user_commands()
    for canonical_name in (requested, normalized):
        command = commands.get(canonical_name)
        if command is not None:
            return ExplicitInvocation("command", canonical_name, normalized, command)

    command_matches = [
        (canonical_name, candidate)
        for canonical_name, candidate in commands.items()
        if canonical_name.casefold() == normalized
    ]
    if len(command_matches) == 1:
        canonical_name, command = command_matches[0]
        return ExplicitInvocation("command", canonical_name, normalized, command)
    if len(command_matches) > 1:
        raise UnknownInvocationError(
            normalized,
            f"Ambiguous command name: /{normalized}",
        )

    for canonical_name, candidate in commands.items():
        aliases = {str(alias).casefold() for alias in getattr(candidate, "aliases", ())}
        if normalized in aliases:
            return ExplicitInvocation("command", canonical_name, normalized, candidate)

    registry = getattr(agent, "skills", None)
    skill = registry.get(requested) if registry is not None else None
    if skill is None and registry is not None:
        skill = registry.get(normalized)
    if skill is None and registry is not None:
        matches = [
            candidate
            for candidate in registry.all()
            if candidate.name.casefold() == normalized
        ]
        if len(matches) == 1:
            skill = matches[0]
        elif len(matches) > 1:
            raise UnknownInvocationError(
                normalized,
                f"Ambiguous skill name: /{normalized}",
            )
    if skill is None:
        raise UnknownInvocationError(
            normalized,
            f"Unknown command or skill: /{normalized}",
        )
    if not skill.enabled:
        raise DisabledSkillError(
            normalized,
            f"Skill is disabled: /{normalized}",
        )

    return ExplicitInvocation("skill", skill.name, normalized, skill)
