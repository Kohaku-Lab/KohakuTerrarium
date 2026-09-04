"""Typed exception hierarchy for the public Python API.

Every error the framework raises on a programmatic surface derives from
:class:`KTError`, so callers can catch one base class::

    try:
        agent = await Agent.build("@kt-biome/creatures/general")
    except kohakuterrarium.errors.KTError as e:
        ...

Many subclasses also derive from their historical built-in exception
(``FileNotFoundError``, ``ValueError``, or ``TimeoutError``), preserving
existing ``except`` handlers during migration to the typed hierarchy.

This module is the dependency root and must not import from
``kohakuterrarium``.
"""


class KTError(Exception):
    """Base class for every KohakuTerrarium error."""


# ---------------------------------------------------------------------------
# Request failures mapped to HTTP status codes by the API adapter.
# ---------------------------------------------------------------------------


class NotFoundError(KTError, KeyError):
    """A named session, agent, artifact, or target does not exist."""

    def __str__(self) -> str:
        # Bypass KeyError's quoted representation to preserve raw error details.
        return Exception.__str__(self)


class InvalidRequestError(KTError, ValueError):
    """A request operation received unsupported arguments."""


class ConflictError(KTError):
    """The operation conflicts with existing state."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class ConfigError(KTError, ValueError):
    """Agent or terrarium configuration is invalid."""


class ConfigNotFoundError(ConfigError, FileNotFoundError):
    """An agent or terrarium config path or package reference was not found."""


# ---------------------------------------------------------------------------
# Packages
# ---------------------------------------------------------------------------


class PackageError(KTError, RuntimeError):
    """Base class for package-system errors."""


class PackageRefError(PackageError, ValueError):
    """A package reference is malformed or escapes its package root."""


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
    """No usable LLM could be resolved from the available configuration."""


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


class SessionError(KTError):
    """Session persistence or resume failure."""


class SessionNotResumableError(SessionError, ValueError):
    """A session cannot be resumed because its metadata is invalid or missing."""


class GraphManifestError(SessionNotResumableError):
    """A live-graph manifest is malformed or cannot be used."""

    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


class GraphManifestVersionError(GraphManifestError):
    """A live-graph manifest uses an unsupported schema version."""

    def __init__(self, version: object) -> None:
        super().__init__(
            f"Unsupported live-graph manifest version: {version!r}",
            field="version",
        )
        self.version = version


class GraphManifestCollisionError(GraphManifestError):
    """A saved graph or creature identifier collides with the target engine."""

    def __init__(self, field: str, value: str) -> None:
        super().__init__(
            f"Live-graph manifest {field} {value!r} already exists",
            field=field,
        )
        self.value = value


class GraphManifestPersistenceError(GraphManifestError):
    """A live-graph manifest could not be read or written."""

    def __init__(self, message: str, *, applied: bool = False) -> None:
        super().__init__(message)
        self.applied = applied


class SessionNotFoundError(SessionError, NotFoundError, FileNotFoundError):
    """A named session does not exist on disk or in the engine."""


class SessionLockedError(SessionError, RuntimeError):
    """A writer already holds the session file lock.

    A second writer is rejected because concurrent writers can overwrite
    conversation snapshots and collide on event counters. Read-only access
    does not acquire this lock.
    """

    def __init__(self, message: str, holder_pid: int | None = None) -> None:
        super().__init__(message)
        self.holder_pid = holder_pid


# ---------------------------------------------------------------------------
# Turn execution
# ---------------------------------------------------------------------------


class TurnError(KTError):
    """A turn failed because of an unrecoverable provider or tool error."""


class TurnTimeoutError(TurnError, TimeoutError):
    """A turn exceeded its ``timeout=`` budget and was cancelled."""


class AgentNotRunningError(KTError, RuntimeError):
    """An operation needed a started agent but it isn't running."""
