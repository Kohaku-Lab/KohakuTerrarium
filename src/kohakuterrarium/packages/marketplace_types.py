"""Typed errors + dataclasses for :mod:`kohakuterrarium.packages.marketplace`.

Split out of ``marketplace.py`` so that module stays under the 600-line
soft cap.  Nothing here imports from the marketplace module — the
dependency arrow goes one way only (marketplace ← marketplace_types).
"""

from dataclasses import dataclass

# ──────────────────────────────────────────────────────────────────
# Typed errors
# ──────────────────────────────────────────────────────────────────


class MarketplaceError(Exception):
    """Base class for marketplace-side failures."""


class MarketplaceUnavailableError(MarketplaceError):
    """All configured sources failed AND no cached copy is usable."""


class MarketplaceNotFoundError(MarketplaceError):
    """The requested ``@name`` is not in any configured source."""


class IncompatibleFrameworkError(MarketplaceError):
    """A name exists but no version satisfies the running framework."""


class InvalidSpecError(MarketplaceError):
    """The spec string did not parse — caller should not have routed here."""


# ──────────────────────────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class MarketplaceVersion:
    """One released version of a marketplace entry."""

    tag: str
    released: str
    framework: str = ""
    notes: str = ""
    notes_url: str = ""
    commit: str = ""
    yanked: bool = False


@dataclass(frozen=True, slots=True)
class MarketplaceEntry:
    """A single package in the marketplace."""

    name: str
    repo: str
    description: str
    tags: tuple[str, ...]
    author: str
    license: str
    framework: str
    versions: tuple[MarketplaceVersion, ...]
    homepage: str = ""
    source_url: str = ""
    source_alias: str = ""


@dataclass(slots=True)
class MarketplaceSource:
    """A configured source URL."""

    alias: str
    url: str
    added: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"alias": self.alias, "url": self.url, "added": self.added}
