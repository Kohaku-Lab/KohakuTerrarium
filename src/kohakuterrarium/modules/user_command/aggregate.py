"""User-command aggregation + collision policy (design §8.9, §11.5).

A duplicate name is valid only when exactly one contribution explicitly overrides.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CommandProvenance:
    """Identify a command's source for deterministic collision reporting."""

    source: str
    origin: str = ""

    def label(self) -> str:
        return f"{self.source}:{self.origin}" if self.origin else self.source


@dataclass(frozen=True)
class CommandContribution:
    """One provenance-tagged slash command offered to the registry."""

    name: str
    command: Any
    provenance: CommandProvenance
    override: bool = False


class UserCommandCollisionError(ValueError):
    """Two sources claim the same slash-command name with no explicit winner."""


def aggregate_user_commands(
    contributions: Iterable[CommandContribution],
) -> tuple[dict[str, Any], dict[str, CommandProvenance]]:
    """Merge commands and provenance, requiring one explicit winner per collision."""
    grouped: dict[str, list[CommandContribution]] = {}
    for contribution in contributions:
        grouped.setdefault(contribution.name, []).append(contribution)

    commands: dict[str, Any] = {}
    provenance: dict[str, CommandProvenance] = {}
    winners: dict[str, CommandContribution] = {}
    for name, group in grouped.items():
        winner = _resolve(name, group)
        commands[name] = winner.command
        provenance[name] = winner.provenance
        winners[name] = winner
    _validate_aliases(winners, provenance)
    return commands, provenance


def _validate_aliases(
    winners: dict[str, CommandContribution],
    provenance: dict[str, CommandProvenance],
) -> None:
    """Validate winning aliases in the same namespace as canonical names."""
    # Canonical names claim the namespace before aliases can enter it.
    claimed: dict[str, tuple[str, bool]] = {
        name: (provenance[name].label(), False) for name in winners
    }
    for name in sorted(winners):
        for alias in getattr(winners[name].command, "aliases", None) or []:
            if not isinstance(alias, str) or not alias or alias == name:
                continue
            existing = claimed.get(alias)
            if existing is not None:
                owner_label, existing_is_alias = existing
                kind = "alias" if existing_is_alias else "command"
                raise UserCommandCollisionError(
                    f"user command alias '/{alias}' (declared by "
                    f"{provenance[name].label()} '/{name}') collides with an "
                    f"existing {kind} ({owner_label}); rename the alias"
                )
            claimed[alias] = (f"{provenance[name].label()} '/{name}'", True)


def _resolve(name: str, group: list[CommandContribution]) -> CommandContribution:
    if len(group) == 1:
        return group[0]
    overriders = [c for c in group if c.override]
    if len(overriders) == 1:
        return overriders[0]
    sources = ", ".join(sorted(c.provenance.label() for c in group))
    if not overriders:
        raise UserCommandCollisionError(
            f"user command '/{name}' is provided by multiple sources ({sources}); "
            "set override=True on exactly one to name the winner"
        )
    raise UserCommandCollisionError(
        f"user command '/{name}' has multiple overriding sources ({sources}); "
        "exactly one may set override=True"
    )


__all__ = [
    "CommandContribution",
    "CommandProvenance",
    "UserCommandCollisionError",
    "aggregate_user_commands",
]
