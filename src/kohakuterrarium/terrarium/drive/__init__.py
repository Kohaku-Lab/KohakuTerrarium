"""Drive runtime resource: domain model, errors, policy, and wire (design §3-6).

Cheap public re-exports of the stable Drive vocabulary. Runtime policy and the
Phase B/C/D modules (policy, repository, store, registration, config, manager)
are imported directly from their submodules — for example
``from kohakuterrarium.terrarium.drive.policy import validate_transition`` — so
this package root stays free of logic.
"""

from kohakuterrarium.terrarium.drive.errors import (
    CrossNodeDriveNotSupportedError,
    DriveBackpressureError,
    DriveConflictError,
    DriveDeliveryError,
    DriveError,
    DriveIdempotencyConflictError,
    DriveNotFoundError,
    DrivePermissionError,
    DrivePersistenceRequiredError,
    DriveReconfigurationRequiredError,
    DriveRegistrationDisabledError,
    DriveRegistrationIncompatibleError,
    DriveRegistrationNotFoundError,
    DriveSettingsConflictError,
    DriveTransitionError,
    DriveValidationError,
)
from kohakuterrarium.terrarium.drive.models import (
    ActorRef,
    DriveAssignment,
    DriveAuditRecord,
    DriveAvailability,
    DriveDelivery,
    DriveProgress,
    DriveRecord,
    DriveStatus,
    SYSTEM_ACTOR,
)
from kohakuterrarium.terrarium.drive.requests import (
    CreateDriveRequest,
    DrivePatch,
    DriveQuery,
    DriveTransitionProposal,
)
from kohakuterrarium.terrarium.drive.wire import WIRE_SCHEMA_VERSION

__all__ = [
    "ActorRef",
    "CreateDriveRequest",
    "CrossNodeDriveNotSupportedError",
    "DriveAssignment",
    "DriveAuditRecord",
    "DriveAvailability",
    "DriveBackpressureError",
    "DriveConflictError",
    "DriveDelivery",
    "DriveDeliveryError",
    "DriveError",
    "DriveIdempotencyConflictError",
    "DriveNotFoundError",
    "DrivePatch",
    "DrivePermissionError",
    "DrivePersistenceRequiredError",
    "DriveProgress",
    "DriveQuery",
    "DriveReconfigurationRequiredError",
    "DriveRecord",
    "DriveRegistrationDisabledError",
    "DriveRegistrationIncompatibleError",
    "DriveRegistrationNotFoundError",
    "DriveSettingsConflictError",
    "DriveStatus",
    "DriveTransitionError",
    "DriveTransitionProposal",
    "DriveValidationError",
    "SYSTEM_ACTOR",
    "WIRE_SCHEMA_VERSION",
]
