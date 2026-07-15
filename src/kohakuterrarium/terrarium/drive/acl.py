"""Actor authorization + registration-availability fail-closed gate.

The Drive manager calls this enforcement layer for every mutation. It composes
two checks:

1. **core ACL** — the owner/assignee/privileged capability model. The canonical
   capability enum, operation table, and default-capability derivation already
   live in :mod:`drive.policy`; this module reuses them and only adds an
   ``extra_grants`` widening for explicitly-granted operator capabilities.
2. **registration availability** — for kind-semantic operations the record's
   registration must be AVAILABLE in the snapshot, else the operation fails
   closed. Generic admin transitions (pause / block / cancel), reads, progress,
   assignment, ownership transfer, and admin remain usable even when the
   registration is disabled/unavailable (§8.6): records stay administratively
   pausable / cancellable / retirable.

Tool presence only exposes an operation; authorization still depends on actor,
record, assignment, and registration availability.
"""

from kohakuterrarium.terrarium.drive.errors import (
    DriveError,
    DrivePermissionError,
    DriveRegistrationDisabledError,
    DriveRegistrationIncompatibleError,
    DriveValidationError,
)
from kohakuterrarium.terrarium.drive.models import (
    ActorRef,
    DriveAssignment,
    DriveAvailability,
    DriveRecord,
    DriveStatus,
)
from kohakuterrarium.terrarium.drive.policy import (
    DriveCapability,
    DriveOperation,
    effective_capabilities,
    _ASSIGNEE_TRANSITIONS,
    _OPERATION_CAPABILITY,
)
from kohakuterrarium.terrarium.drive.snapshot import (
    EnabledRegistrySnapshot,
    availability_for_kind,
    derive_availability,
)

# Re-export capabilities so callers depend on one authorization surface.
CORE_CAPABILITIES: frozenset[DriveCapability] = frozenset(DriveCapability)

# Trusted operator elevation grants graph-wide capabilities that request payloads
# can never self-assert.
OPERATOR_GRANTS: frozenset[DriveCapability] = frozenset(
    {
        DriveCapability.CREATE_GRAPH,
        DriveCapability.ASSIGN,
        DriveCapability.TRANSFER_OWNER,
        DriveCapability.VERIFY_TERMINAL,
        DriveCapability.ADMIN,
    }
)

# Kind-semantic operations fail closed without an available compatible
# registration.
KIND_SEMANTIC_OPERATIONS: frozenset[DriveOperation] = frozenset(
    {
        DriveOperation.CREATE_SELF,
        DriveOperation.CREATE_GRAPH,
        DriveOperation.UPDATE,
        DriveOperation.PROPOSE_TERMINAL,
        DriveOperation.VERIFY_TERMINAL,
    }
)

# Suspension targets remain available for administration even when kind policy is
# unavailable.
ADMIN_TRANSITION_TARGETS: frozenset[DriveStatus] = frozenset(
    {DriveStatus.PAUSED, DriveStatus.BLOCKED, DriveStatus.CANCELLED}
)
# Resuming pursuit requires available kind policy.
SEMANTIC_TRANSITION_TARGETS: frozenset[DriveStatus] = frozenset(
    {DriveStatus.ACTIVE, DriveStatus.WAITING}
)

# Per-record action lists omit creation because it has no existing record.
_DTO_OPERATIONS: tuple[DriveOperation, ...] = (
    DriveOperation.READ,
    DriveOperation.UPDATE,
    DriveOperation.ASSIGN,
    DriveOperation.UNASSIGN,
    DriveOperation.REASSIGN,
    DriveOperation.TRANSFER_OWNER,
    DriveOperation.TRANSITION,
    DriveOperation.PROPOSE_TERMINAL,
    DriveOperation.VERIFY_TERMINAL,
    DriveOperation.REPORT_PROGRESS,
    DriveOperation.RETIRE,
    DriveOperation.REPLAY,
    DriveOperation.ADMIN,
)


def capabilities_for(
    actor: ActorRef,
    record: DriveRecord | None,
    assignment: DriveAssignment | None,
    *,
    is_privileged: bool = False,
    extra_grants: frozenset[DriveCapability] = frozenset(),
) -> frozenset[DriveCapability]:
    """Default-policy capabilities plus any explicit ``extra_grants`` (§3.6).

    Owner defaults deliberately exclude ``transfer_owner``; a foreign-owned
    assignee gets manage/report/propose only; privilege adds graph-scoped
    create/assign/transfer/admin. ``extra_grants`` is the audited widening path
    for operators (design §3.6: widening is never silent).
    """
    caps = set(
        effective_capabilities(actor, record, assignment, is_privileged=is_privileged)
    )
    for grant in extra_grants:
        if not isinstance(grant, DriveCapability):
            raise DriveValidationError(
                f"extra_grants entries must be DriveCapability, got {grant!r}"
            )
        caps.add(grant)
    return frozenset(caps)


def _caps_allow(
    caps: frozenset[DriveCapability],
    operation: DriveOperation,
    target_status: DriveStatus | None,
) -> bool:
    """Whether a capability set satisfies ``operation`` — the same predicate as
    :func:`drive.policy.is_operation_allowed`, but over an explicit set so
    ``extra_grants`` is honored. The operation->capability tables it consults
    are owned by :mod:`drive.policy` (single source of truth)."""
    if operation == DriveOperation.REPORT_PROGRESS:
        return bool(
            caps
            & {
                DriveCapability.UPDATE_OWNED,
                DriveCapability.MANAGE_ASSIGNED,
                DriveCapability.ADMIN,
            }
        )
    if operation == DriveOperation.TRANSITION:
        if DriveCapability.TRANSITION in caps:
            return True
        return (
            DriveCapability.MANAGE_ASSIGNED in caps
            and target_status in _ASSIGNEE_TRANSITIONS
        )
    return _OPERATION_CAPABILITY[operation] in caps


def _operation_needs_registration(
    operation: DriveOperation, target_status: DriveStatus | None
) -> bool:
    if operation in KIND_SEMANTIC_OPERATIONS:
        return True
    if operation == DriveOperation.TRANSITION:
        return target_status not in ADMIN_TRANSITION_TARGETS
    return False


def _registration_error(availability: DriveAvailability, kind: str) -> DriveError:
    if availability is DriveAvailability.REGISTRATION_INCOMPATIBLE:
        return DriveRegistrationIncompatibleError(
            f"enabled registration cannot serve the schema version for kind {kind!r}"
        )
    return DriveRegistrationDisabledError(
        f"no available registration serves kind {kind!r} ({availability.value})"
    )


def authorize(
    operation: DriveOperation,
    actor: ActorRef,
    record: DriveRecord | None,
    assignment: DriveAssignment | None,
    snapshot: EnabledRegistrySnapshot | None,
    *,
    is_privileged: bool = False,
    target_status: DriveStatus | None = None,
    kind: str | None = None,
    schema_version: int | None = None,
    extra_grants: frozenset[DriveCapability] = frozenset(),
) -> None:
    """Raise unless ``actor`` may run ``operation`` on this Drive (design §8.8).

    Core ACL is checked first (fail closed -> :class:`DrivePermissionError`),
    then the registration-availability gate for kind-semantic operations (fail
    closed -> :class:`DriveRegistrationDisabledError` /
    :class:`DriveRegistrationIncompatibleError`). For ``create`` there is no
    record yet, so ``kind`` + ``schema_version`` supply the availability inputs.
    """
    caps = capabilities_for(
        actor,
        record,
        assignment,
        is_privileged=is_privileged,
        extra_grants=extra_grants,
    )
    if not _caps_allow(caps, operation, target_status):
        raise DrivePermissionError(
            f"actor {actor.format()!r} may not {operation.value} this Drive"
        )
    if snapshot is None or not _operation_needs_registration(operation, target_status):
        return
    resolved_kind = record.kind if record is not None else kind
    if resolved_kind is None:
        return
    resolved_version = (
        record.schema_version if record is not None else (schema_version or 1)
    )
    availability = availability_for_kind(resolved_kind, resolved_version, snapshot)
    if availability is not DriveAvailability.AVAILABLE:
        raise _registration_error(availability, resolved_kind)


def is_authorized(
    operation: DriveOperation,
    actor: ActorRef,
    record: DriveRecord | None,
    assignment: DriveAssignment | None,
    snapshot: EnabledRegistrySnapshot | None,
    *,
    is_privileged: bool = False,
    target_status: DriveStatus | None = None,
    kind: str | None = None,
    schema_version: int | None = None,
    extra_grants: frozenset[DriveCapability] = frozenset(),
) -> bool:
    """Return whether authorization succeeds without propagating its error."""
    try:
        authorize(
            operation,
            actor,
            record,
            assignment,
            snapshot,
            is_privileged=is_privileged,
            target_status=target_status,
            kind=kind,
            schema_version=schema_version,
            extra_grants=extra_grants,
        )
    except DriveError:
        return False
    return True


def allowed_actions(
    actor: ActorRef,
    record: DriveRecord | None,
    assignment: DriveAssignment | None,
    snapshot: EnabledRegistrySnapshot | None,
    *,
    is_privileged: bool = False,
    extra_grants: frozenset[DriveCapability] = frozenset(),
) -> tuple[str, ...]:
    """The DTO-facing action list for one record (design §9.4).

    Includes an operation when the actor is authorized by ACL and — for
    kind-semantic operations — the record's registration is AVAILABLE. Generic
    admin transitions stay listed even when the registration is unavailable, so
    the UI can still pause/cancel/retire.
    """
    caps = capabilities_for(
        actor,
        record,
        assignment,
        is_privileged=is_privileged,
        extra_grants=extra_grants,
    )
    available = (
        record is None
        or snapshot is None
        or derive_availability(record, snapshot) is DriveAvailability.AVAILABLE
    )
    out: list[str] = []
    for op in _DTO_OPERATIONS:
        if not _dto_op_allowed(caps, op):
            continue
        if not available and op in KIND_SEMANTIC_OPERATIONS:
            continue
        out.append(op.value)
    return tuple(out)


def _dto_op_allowed(caps: frozenset[DriveCapability], op: DriveOperation) -> bool:
    if op == DriveOperation.TRANSITION:
        # Offer transition controls when the actor can perform at least one
        # permitted transition.
        return bool(
            caps & {DriveCapability.TRANSITION, DriveCapability.MANAGE_ASSIGNED}
        )
    if op == DriveOperation.REPORT_PROGRESS:
        return bool(
            caps
            & {
                DriveCapability.UPDATE_OWNED,
                DriveCapability.MANAGE_ASSIGNED,
                DriveCapability.ADMIN,
            }
        )
    return _OPERATION_CAPABILITY[op] in caps


__all__ = [
    "ADMIN_TRANSITION_TARGETS",
    "CORE_CAPABILITIES",
    "KIND_SEMANTIC_OPERATIONS",
    "OPERATOR_GRANTS",
    "SEMANTIC_TRANSITION_TARGETS",
    "DriveCapability",
    "DriveOperation",
    "allowed_actions",
    "authorize",
    "capabilities_for",
    "is_authorized",
]
