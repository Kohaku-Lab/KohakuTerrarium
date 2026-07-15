"""Typed errors for the Drive runtime resource (design §9.5).

Every error derives from :class:`kohakuterrarium.errors.KTError` and, where a
historical builtin fits the failure, also that builtin — so pre-typed
``except`` sites and the ``api/`` HTTP status mapping keep working.
"""

from kohakuterrarium.errors import (
    ConflictError,
    InvalidRequestError,
    KTError,
    NotFoundError,
)


class DriveError(KTError):
    """Base class for every Drive runtime error."""


class DriveNotFoundError(DriveError, NotFoundError):
    """No Drive with the referenced ``drive_id`` exists in scope."""


class DriveValidationError(DriveError, InvalidRequestError):
    """A Drive record/DTO/payload violates a structural invariant."""


class DriveTransitionError(DriveError, InvalidRequestError):
    """A requested status transition is not permitted by policy."""

    def __init__(
        self,
        message: str,
        *,
        from_status: str | None = None,
        to_status: str | None = None,
    ) -> None:
        super().__init__(message)
        self.from_status = from_status
        self.to_status = to_status


class DrivePermissionError(DriveError, PermissionError):
    """The actor lacks the capability required for this mutation."""


class DriveConflictError(DriveError, ConflictError):
    """Optimistic-concurrency failure: ``expected_revision`` is missing/stale."""

    def __init__(
        self,
        message: str,
        *,
        expected_revision: int | None = None,
        actual_revision: int | None = None,
    ) -> None:
        super().__init__(message)
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision


class DriveIdempotencyConflictError(DriveError, ConflictError):
    """An idempotency key was reused with a different normalized operation."""

    def __init__(self, message: str, *, idempotency_key: str | None = None) -> None:
        super().__init__(message)
        self.idempotency_key = idempotency_key


class DriveProposalConflictError(DriveError, ConflictError):
    """A proposal ID identifies different proposals across topology sources."""

    def __init__(self, message: str, *, proposal_id: str | None = None) -> None:
        super().__init__(message)
        self.proposal_id = proposal_id


class DriveRegistrationNotFoundError(DriveError, NotFoundError):
    """No registration serves the requested Drive ``kind``."""


class DriveRegistrationDisabledError(DriveError):
    """A registration serving the kind exists but is not enabled."""


class DriveRegistrationIncompatibleError(DriveError):
    """The enabled registration cannot serve this record's schema version."""


class DriveSchemaVersionError(DriveError):
    """A Drive store declares an invalid or unsupported schema version."""


class DriveReconfigurationRequiredError(DriveError):
    """A settings change cannot be applied live and needs a restart/drain."""


class DrivePersistenceRequiredError(DriveError):
    """A durable Drive was requested but no persistent backend is available."""


class DriveBackpressureError(DriveError):
    """A configured pending/active limit would be exceeded."""


class DriveDeliveryError(DriveError):
    """A delivery could not be admitted, claimed, or acknowledged."""


class DriveSettingsConflictError(DriveError, ConflictError):
    """A managed Drive settings save lost an optimistic-concurrency race."""

    def __init__(
        self,
        message: str,
        *,
        expected_revision: str | None = None,
        actual_revision: str | None = None,
    ) -> None:
        super().__init__(message)
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision


class CrossNodeDriveNotSupportedError(DriveError):
    """A Drive operation requires unsupported cross-node behavior."""
