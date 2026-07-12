"""User-command aggregation + collision policy (design §8.9, §11.5).

The Agent's live slash-command registry is the union of four sources:
built-ins, package ``user_commands:`` manifest entries, constructor-injected
commands, and the commands active plugins contribute through
``contribute_user_commands()``. This leaf module holds the pure merge: it takes
provenance-tagged contributions and returns one ``name -> command`` mapping,
raising a provenance-carrying error on an unresolved duplicate.

Collision rule: a name provided by more than one source is a hard configuration
error UNLESS exactly one contribution carries ``override=True`` — that one wins
(design §8.9: "duplicate command name = hard configuration error UNLESS an
explicit override policy names the winner"). Zero or two-plus overriders is
still an error. No Agent / LLM / plugin-manager imports.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CommandProvenance:
    """Where a contributed command came from, for collision reporting.

    ``source`` is one of ``builtin`` / ``package`` / ``constructor`` /
    ``plugin``; ``origin`` names the owning package or plugin (empty for
    built-in / constructor).
    """

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
    """Merge provenance-tagged contributions into one command registry.

    Returns ``(commands, provenance)`` mapping each surviving name to its
    command instance and to the provenance of the source that supplied it.
    Raises :class:`UserCommandCollisionError` when a name is contributed by
    more than one source and the override policy does not name a single winner.
    """
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
    """Fold each winning command's aliases into the single command namespace.

    An alias that lands on another command's canonical name or on a second
    command's alias is a hard configuration error (R1-24): aliases are validated
    in the SAME namespace as canonical names, not silently overwritten by a later
    alias-map build. Only winning contributions contribute aliases, so a command
    that lost the canonical collision cannot leak its aliases.
    """
    # token -> (owner label, is that claim an alias?). Canonical names claimed
    # first so an alias can never displace a real command name.
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
