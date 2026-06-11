"""Typed exception hierarchy for the public Python API.

Every error the framework raises on a programmatic surface derives from
:class:`KTError`, so callers can catch one base class::

    try:
        agent = await Agent.build("@kt-biome/creatures/general")
    except kohakuterrarium.errors.KTError as e:
        ...

Many subclasses ALSO derive from the builtin exception the same failure
historically raised (``FileNotFoundError`` / ``ValueError`` /
``TimeoutError``), so pre-existing ``except`` sites keep working while
callers migrate to the typed hierarchy.

This module is the bottom of the dependency graph: it must not import
anything from ``kohakuterrarium`` (everything imports *it*).
"""


class KTError(Exception):
    """Base class for every KohakuTerrarium error."""


# ---------------------------------------------------------------------------
# Generic request-shaped failures (used by the studio tier; the api/
# adapter maps them onto HTTP status codes — see ``api/app.py``)
# ---------------------------------------------------------------------------


class NotFoundError(KTError, KeyError):
    """A named resource (session / agent / artifact / target) doesn't exist."""

    def __str__(self) -> str:
        # KeyError.__str__ repr()s its argument (quotes the message);
        # keep the raw message so error details stay byte-identical
        # with the pre-typed-error surface.
        return Exception.__str__(self)


class InvalidRequestError(KTError, ValueError):
    """A request-shaped operation got arguments it cannot honour."""


class ConflictError(KTError):
    """The operation conflicts with existing state (e.g. fork target exists)."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class ConfigError(KTError, ValueError):
    """Invalid agent / terrarium configuration content."""


class ConfigNotFoundError(ConfigError, FileNotFoundError):
    """Agent / terrarium config path (or ``@pkg`` reference) not found."""


# ---------------------------------------------------------------------------
# Packages
# ---------------------------------------------------------------------------


class PackageError(KTError):
    """Base class for package-system errors."""


class PackageRefError(PackageError, ValueError):
    """Malformed ``@`` reference (bare ``@``, traversal escape, ...)."""


class PackageNotInstalledError(PackageError, FileNotFoundError):
    """An ``@<package>/...`` reference names a package that isn't installed."""


class PackagePathNotFoundError(PackageError, FileNotFoundError):
    """The package exists but the referenced sub-path doesn't."""


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------


class LLMError(KTError):
    """LLM provider construction or call failure."""


class LLMNotConfiguredError(LLMError, ValueError):
    """No usable LLM could be resolved (missing key, unknown profile, ...)."""


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


class SessionError(KTError):
    """Session persistence / resume failure."""


class SessionNotResumableError(SessionError, ValueError):
    """The session file exists but cannot be resumed (bad / missing meta)."""


class SessionNotFoundError(SessionError, NotFoundError, FileNotFoundError):
    """The named / referenced session does not exist on disk or in-engine."""


# ---------------------------------------------------------------------------
# Turn execution
# ---------------------------------------------------------------------------


class TurnError(KTError):
    """A turn failed (provider error, unrecoverable tool crash, ...)."""


class TurnTimeoutError(TurnError, TimeoutError):
    """A turn exceeded its ``timeout=`` budget and was cancelled."""


class AgentNotRunningError(KTError, RuntimeError):
    """An operation needed a started agent but it isn't running."""
