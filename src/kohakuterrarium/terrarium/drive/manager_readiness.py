"""Readiness, enqueue, backpressure, and scan helpers for ``DriveManager``.

Readiness admission decides when a drive earns a delivery. It evaluates
registration and dependency state, enforces one delivery per readiness event,
applies creature and graph backpressure, and scans waiting or time-gated drives.
The concrete manager supplies repository, configuration, registration snapshot,
clock, event emission, dispatch, and readiness-generation state.
"""

import inspect
import json
from datetime import datetime
from enum import Enum
from typing import Any

from kohakuterrarium.terrarium.drive.acl import DriveOperation
from kohakuterrarium.terrarium.drive.delivery import USER_INTERRUPTED_REASON
from kohakuterrarium.terrarium.drive.delivery_failure import BLOCKABLE_STATUSES
from kohakuterrarium.terrarium.drive.errors import (
    DriveBackpressureError,
    DriveError,
    DriveValidationError,
)
from kohakuterrarium.terrarium.drive.manager_admit import (
    RecoveryAdmission,
    recovery_admission_version,
)
from kohakuterrarium.terrarium.drive.models import (
    SYSTEM_ACTOR,
    ActorRef,
    DriveAssignment,
    DriveDelivery,
    DriveRecord,
    DriveStatus,
)
from kohakuterrarium.terrarium.drive.policy import (
    is_deliverable_status,
    wake_conditions_met,
)
from kohakuterrarium.terrarium.drive.requests import CreateDriveRequest, DriveQuery
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_LIVE_DELIVERY_STATES = frozenset({"pending", "claimed", "admitted", "retry_wait"})
_PENDING_DELIVERY_STATES = frozenset({"pending", "claimed", "retry_wait"})


class ReadinessOutcome(Enum):
    """Outcome of attempting to re-arm a settled readiness generation.

    ``NOT_APPLICABLE`` permits ordinary resume because no continuation applies.
    ``DEFERRED`` preserves the settled generation until a temporary gate clears.
    ``REARMED`` means the generation advanced and a continuation was enqueued.
    """

    NOT_APPLICABLE = "not_applicable"
    DEFERRED = "deferred"
    REARMED = "rearmed"


def _accepts_turns_used(fn: Any) -> bool:
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    if "turns_used" in params:
        return True
    return any(p.kind is p.VAR_KEYWORD for p in params.values())


def settled_by_turn(delivery: DriveDelivery) -> bool:
    """Return whether an acknowledged delivery settled through a finished turn.

    A user interrupt acknowledges the row so it is never retried, but it must
    not count as a settled generation that earns a continuation.
    """
    return (
        delivery.state == "acknowledged"
        and delivery.ack_reason != USER_INTERRUPTED_REASON
    )


def _call_readiness(
    registration: Any,
    record: DriveRecord,
    deps: dict[str, DriveStatus],
    now: datetime,
    turns_used: int,
) -> Any:
    """Call readiness while preserving compatibility with three-argument roles."""
    fn = registration.readiness
    if _accepts_turns_used(fn):
        return fn(record, deps, now, turns_used=turns_used)
    return fn(record, deps, now)


class ManagerReadinessMixin:
    """Apply readiness, delivery uniqueness, and backpressure gates."""

    def _current_readiness_generation(self, drive_id: str) -> int:
        # Generation zero represents registrations that never re-arm; only a
        # continuation decision advances the in-memory counter.
        return self._readiness_gen.get(drive_id, 0)

    async def _current_generation_settled(self, record: DriveRecord) -> bool:
        """Return whether the current epoch and generation already settled.

        A settled generation must be re-armed before another delivery; it cannot
        be treated as a fresh resume.
        """
        gen = self._current_readiness_generation(record.drive_id)
        for delivery in await self._repo.list_deliveries(record.drive_id):
            if (
                delivery.readiness_generation == gen
                and delivery.lifecycle_epoch == record.lifecycle_epoch
                and settled_by_turn(delivery)
            ):
                return True
        return False

    def _create_operation(
        self, request: CreateDriveRequest, actor: ActorRef
    ) -> DriveOperation:
        is_self = (
            actor.kind == "creature"
            and request.scope_type == "creature"
            and request.scope_id == actor.identity
            and request.owner == actor
        )
        return DriveOperation.CREATE_SELF if is_self else DriveOperation.CREATE_GRAPH

    def _validate_spec(self, kind: str, spec: dict[str, Any]) -> None:
        entry = self._snapshot.for_kind(kind) if self._snapshot else None
        if entry is None or not entry.available:
            return
        validate = getattr(entry.registration, "validate_spec", None)
        if callable(validate):
            validate(spec)  # Validation failures deny creation rather than degrading.

    def _check_payload_size(
        self, field: str, value: dict[str, Any] | None, max_bytes: int
    ) -> None:
        """Enforce mapping shape, JSON safety, and the byte cap for one field.

        Encoding deliberately omits ``default=`` so unsupported values fail
        instead of being stringified. Limits apply to UTF-8 bytes and remain
        authoritative even when a registration imposes its own cap.
        """
        if value is None:
            return
        if not isinstance(value, dict):
            raise DriveValidationError(f"{field} must be a dict, got {value!r}")
        try:
            encoded = json.dumps(value, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise DriveValidationError(
                f"{field} must be JSON-serializable: {exc}"
            ) from exc
        if len(encoded) > max_bytes:
            raise DriveValidationError(
                f"{field} is {len(encoded)} bytes, over the {max_bytes}-byte cap"
            )

    def _validate_payloads(
        self,
        *,
        spec: dict[str, Any] | None = None,
        presentation: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        policy_options: dict[str, Any] | None = None,
    ) -> None:
        """Validate canonical payload fields against configured storage limits.

        ``policy_options`` shares the metadata cap because it has no independent
        limit and must not become an unbounded canonical payload.
        """
        cfg = self._config
        self._check_payload_size("spec", spec, cfg.spec_max_bytes)
        self._check_payload_size(
            "presentation", presentation, cfg.presentation_max_bytes
        )
        self._check_payload_size("metadata", metadata, cfg.metadata_max_bytes)
        self._check_payload_size(
            "policy_options", policy_options, cfg.metadata_max_bytes
        )

    def _check_evidence_size(self, evidence: dict[str, Any] | None) -> None:
        self._check_payload_size("evidence", evidence, self._config.evidence_max_bytes)

    async def _dependency_statuses(self, record: DriveRecord) -> dict[str, DriveStatus]:
        out: dict[str, DriveStatus] = {}
        for dep_id in record.dependency_ids:
            dep = await self._repo.get(dep_id)
            # Missing dependencies remain logically active so vanished state
            # cannot accidentally satisfy a terminal dependency condition.
            out[dep_id] = dep.status if dep is not None else DriveStatus.ACTIVE
        return out

    async def _has_live_delivery(self, drive_id: str) -> bool:
        for delivery in await self._repo.list_deliveries(drive_id):
            if delivery.state in _LIVE_DELIVERY_STATES:
                return True
        return False

    async def _has_epoch_delivery(self, drive_id: str, epoch: int) -> bool:
        for delivery in await self._repo.list_deliveries(drive_id):
            if delivery.lifecycle_epoch == epoch:
                return True
        return False

    async def _pending_count(self, graph_id: str) -> int:
        count = 0
        for record in await self._repo.list_drives(DriveQuery(graph_id=graph_id)):
            for delivery in await self._repo.list_deliveries(record.drive_id):
                if delivery.state in _PENDING_DELIVERY_STATES:
                    count += 1
        return count

    async def _check_create_backpressure(
        self, request: CreateDriveRequest, graph_id: str
    ) -> None:
        assignee = request.assignee_creature_id or (
            request.scope_id if request.scope_type == "creature" else None
        )
        if assignee is not None:
            active = await self._repo.list_drives(
                DriveQuery(
                    assignee_creature_id=assignee,
                    statuses=frozenset({DriveStatus.ACTIVE}),
                )
            )
            if len(active) >= self._config.max_active_per_creature:
                raise DriveBackpressureError(
                    f"assignee {assignee!r} is at the active-Drive limit "
                    f"({self._config.max_active_per_creature})"
                )
        if await self._pending_count(graph_id) >= self._config.max_pending_per_graph:
            raise DriveBackpressureError(
                f"graph {graph_id!r} is at the pending-delivery limit "
                f"({self._config.max_pending_per_graph})"
            )

    async def _maybe_enqueue(
        self, record: DriveRecord, reason: str, *, manual: bool = False
    ) -> DriveDelivery | None:
        if not is_deliverable_status(record.status):
            return None
        assignment = await self._repo.get_assignment(record.drive_id)
        if assignment is None or assignment.assignee_creature_id is None:
            return None
        deps = await self._dependency_statuses(record)
        if not wake_conditions_met(record, self._clock(), deps):
            return None
        if await self._has_live_delivery(record.drive_id):
            return None
        # Manual wake is the sole authorized readiness override. Creation,
        # activation, scanning, and recovery all fail closed through this gate.
        if not manual and not await self._readiness_admits(record, deps):
            return None
        if (
            await self._pending_count(assignment.assignee_graph_id)
            >= self._config.max_pending_per_graph
        ):
            self._emit(
                "drive_backpressured",
                record.drive_id,
                {"reason": "max_pending_per_graph"},
            )
            return None
        return await self._enqueue_reason(record, assignment, reason)

    async def _readiness_admits(
        self,
        record: DriveRecord,
        deps: dict[str, DriveStatus],
        *,
        superseding: frozenset[str] = frozenset(),
        evaluated_at: datetime | None = None,
        deliveries: list[DriveDelivery] | None = None,
    ) -> bool:
        """Return whether registration readiness permits admission.

        Missing or unavailable readiness roles impose no additional gate.
        ``ready`` permits admission, while ``initial`` permits only the first
        completed opportunity in a lifecycle epoch. Readiness errors fail closed
        by blocking the drive.

        ``superseding`` deliveries are excluded because uncertain attempts being
        atomically replaced neither settled a turn nor consumed the epoch's
        initial grant.
        """
        entry = self._snapshot.for_kind(record.kind) if self._snapshot else None
        if entry is None or not entry.available:
            return True
        registration = entry.registration
        if not callable(getattr(registration, "readiness", None)):
            return True
        source = (
            list(await self._repo.list_deliveries(record.drive_id))
            if deliveries is None
            else deliveries
        )
        considered = [d for d in source if d.delivery_id not in superseding]
        turns_used = sum(1 for d in considered if d.state == "acknowledged")
        try:
            verdict = _call_readiness(
                registration,
                record,
                deps,
                evaluated_at if evaluated_at is not None else self._clock(),
                turns_used,
            )
        except Exception as exc:  # Registration failures must not admit work.
            await self._block_on_readiness_error(record, str(exc))
            return False
        if verdict is None:
            return False
        if getattr(verdict, "ready", False):
            return True
        if getattr(verdict, "initial", False):
            # Only a non-superseded delivery consumes the epoch's initial grant.
            # This allows recovery of an uncertain first attempt without treating
            # the abandoned row as completed delivery.
            is_initial = not any(
                d.lifecycle_epoch == record.lifecycle_epoch and d.state != "superseded"
                for d in considered
            )
            return is_initial
        return False

    async def _recovery_admitted(
        self,
        record: DriveRecord,
        assignment: DriveAssignment | None,
        superseding: frozenset[str],
    ) -> RecoveryAdmission:
        """Compute a versioned recovery verdict outside the repository lock.

        Transactional admission revalidates every gate and the fingerprint before
        superseding uncertain work, so readiness callbacks never run while the
        SQLite transaction is locked.
        """
        evaluated_at = self._clock()
        deps = await self._dependency_statuses(record)
        deliveries = list(await self._repo.list_deliveries(record.drive_id))
        version = recovery_admission_version(
            record, assignment, deliveries, deps, superseding, evaluated_at
        )
        admits = await self._recovery_verdict(
            record, assignment, superseding, deps, deliveries, evaluated_at
        )
        return RecoveryAdmission(
            admits=admits, version=version, evaluated_at=evaluated_at
        )

    async def _recovery_verdict(
        self,
        record: DriveRecord,
        assignment: DriveAssignment | None,
        superseding: frozenset[str],
        deps: dict[str, DriveStatus],
        deliveries: list[DriveDelivery],
        evaluated_at: datetime,
    ) -> bool:
        """Evaluate recovery with uncertain deliveries treated as superseded.

        Recovery uses the same status, assignment, wake, uniqueness, readiness,
        and graph-capacity gates as ordinary admission; it is not a readiness
        override.
        """
        if not is_deliverable_status(record.status):
            return False
        if (
            assignment is None
            or assignment.drive_id != record.drive_id
            or assignment.assignment_state != "assigned"
            or assignment.lifecycle_epoch != record.lifecycle_epoch
            or not assignment.assignee_graph_id
            or assignment.assignee_creature_id is None
        ):
            return False
        if not wake_conditions_met(record, evaluated_at, deps):
            return False
        if any(
            d.state in _LIVE_DELIVERY_STATES and d.delivery_id not in superseding
            for d in deliveries
        ):
            return False
        if not await self._readiness_admits(
            record,
            deps,
            superseding=superseding,
            evaluated_at=evaluated_at,
            deliveries=deliveries,
        ):
            return False
        if (
            await self._pending_count(assignment.assignee_graph_id)
            >= self._config.max_pending_per_graph
        ):
            self._emit(
                "drive_backpressured",
                record.drive_id,
                {"reason": "max_pending_per_graph"},
            )
            return False
        return True

    async def _enqueue_reason(
        self, record: DriveRecord, assignment: DriveAssignment | None, reason: str
    ) -> DriveDelivery:
        delivery = await self._repo.enqueue_delivery(
            record.drive_id,
            reason=reason,
            readiness_generation=self._current_readiness_generation(record.drive_id),
        )
        self._emit(
            "drive_ready",
            record.drive_id,
            {"delivery_id": delivery.delivery_id, "reason": reason},
        )
        self._dispatcher.notify()
        return delivery

    async def _scan_ready(self) -> None:
        """Wake eligible waiting drives and admit undelivered active drives."""
        now = self._clock()
        for record in await self._repo.list_drives(
            DriveQuery(statuses=frozenset({DriveStatus.WAITING}))
        ):
            assignment = await self._repo.get_assignment(record.drive_id)
            if assignment is None or assignment.assignee_creature_id is None:
                continue
            deps = await self._dependency_statuses(record)
            if not wake_conditions_met(record, now, deps):
                continue
            try:
                woken = await self._repo.transition_drive(
                    record.drive_id,
                    DriveStatus.ACTIVE,
                    expected_revision=record.revision,
                    actor=SYSTEM_ACTOR,
                    operation="wake",
                )
            except DriveError:
                continue
            self._emit(
                "drive_status_changed",
                record.drive_id,
                {"status": woken.status.value, "revision": woken.revision},
            )
            reason = "dependency_ready" if record.dependency_ids else "ready"
            await self._maybe_enqueue(woken, reason)
        for record in await self._repo.list_drives(
            DriveQuery(statuses=frozenset({DriveStatus.ACTIVE}))
        ):
            assignment = await self._repo.get_assignment(record.drive_id)
            if assignment is None or assignment.assignee_creature_id is None:
                continue
            if await self._has_epoch_delivery(record.drive_id, record.lifecycle_epoch):
                # Epoch delivery suppresses another initial admission; only a
                # settled generation may produce a continuation.
                await self._reevaluate_readiness(record)
                continue
            deps = await self._dependency_statuses(record)
            if wake_conditions_met(record, now, deps):
                await self._maybe_enqueue(record, "activated")

    async def _reevaluate_readiness(
        self, record: DriveRecord, *, reason: str = "ready"
    ) -> ReadinessOutcome:
        """Attempt to continue a drive after its current generation settles.

        A registration may re-arm after cooldown, wake conditions, and graph
        capacity permit it. Success advances the readiness generation before
        enqueueing, giving the continuation a fresh logical key. Registration
        errors block the drive and prevent repeated failing scans.

        The three outcomes distinguish registrations that do not continue from
        continuations that are merely waiting on a temporary gate.
        """
        if not is_deliverable_status(record.status):
            return ReadinessOutcome.NOT_APPLICABLE
        entry = self._snapshot.for_kind(record.kind) if self._snapshot else None
        if entry is None or not entry.available:
            return ReadinessOutcome.NOT_APPLICABLE
        registration = entry.registration
        if not callable(getattr(registration, "readiness", None)):
            return ReadinessOutcome.NOT_APPLICABLE
        assignment = await self._repo.get_assignment(record.drive_id)
        if assignment is None or assignment.assignee_creature_id is None:
            return ReadinessOutcome.NOT_APPLICABLE
        if await self._has_live_delivery(record.drive_id):
            # In-flight work defers continuation so resume cannot duplicate it.
            return ReadinessOutcome.DEFERRED
        deliveries = await self._repo.list_deliveries(record.drive_id)
        gen = self._current_readiness_generation(record.drive_id)
        settled = [
            d
            for d in deliveries
            if d.readiness_generation == gen
            and d.lifecycle_epoch == record.lifecycle_epoch
            and settled_by_turn(d)
        ]
        if not settled:
            # Re-arm requires a settled generation.
            return ReadinessOutcome.NOT_APPLICABLE
        now = self._clock()
        deps = await self._dependency_statuses(record)
        turns_used = sum(1 for d in deliveries if d.state == "acknowledged")
        try:
            verdict = _call_readiness(registration, record, deps, now, turns_used)
        except Exception as exc:  # Registration failures must not admit work.
            await self._block_on_readiness_error(record, str(exc))
            # Blocking forbids resume fallback.
            return ReadinessOutcome.DEFERRED
        if verdict is None or not getattr(verdict, "re_arm", False):
            return ReadinessOutcome.NOT_APPLICABLE
        # Once continuation is requested, every remaining gate is temporary;
        # ordinary resume would create stale work that dispatch must supersede.
        last_ack = max(
            (d.acknowledged_at for d in settled if d.acknowledged_at is not None),
            default=None,
        )
        cooldown = self._config.readiness_cooldown_s
        if (
            last_ack is not None
            and cooldown > 0
            and (now - last_ack).total_seconds() < cooldown
        ):
            # A later scan retries after cooldown.
            return ReadinessOutcome.DEFERRED
        if not wake_conditions_met(record, now, deps):
            return ReadinessOutcome.DEFERRED
        if (
            await self._pending_count(assignment.assignee_graph_id)
            >= self._config.max_pending_per_graph
        ):
            self._emit(
                "drive_backpressured",
                record.drive_id,
                {"reason": "max_pending_per_graph"},
            )
            return ReadinessOutcome.DEFERRED
        self._readiness_gen[record.drive_id] = gen + 1
        await self._enqueue_reason(record, assignment, reason)
        return ReadinessOutcome.REARMED

    async def _block_on_readiness_error(self, record: DriveRecord, error: str) -> None:
        """Block a drive after a readiness failure and emit the condition.

        The system transition provides the audit record. Blocked drives are not
        admitted or rescanned, preventing a persistent callback failure loop.
        """
        if record.status not in BLOCKABLE_STATUSES:
            return
        self._emit("drive_readiness_error", record.drive_id, {"error": error})
        try:
            blocked = await self._repo.transition_drive(
                record.drive_id,
                DriveStatus.BLOCKED,
                expected_revision=record.revision,
                actor=SYSTEM_ACTOR,
                status_reason="readiness_error",
                operation="readiness_error_block",
            )
        # The readiness error remains observable if blocking races.
        except DriveError as exc:
            logger.warning(
                "readiness-error block failed", drive_id=record.drive_id, error=str(exc)
            )
            return
        self._emit(
            "drive_status_changed",
            record.drive_id,
            {"status": blocked.status.value, "revision": blocked.revision},
        )

    async def _rebuild_readiness_generations(self) -> None:
        """Restore readiness generations from persisted deliveries.

        Cold resume must continue from each drive's highest generation rather
        than returning to zero and colliding with previously admitted work.
        """
        for record in await self._repo.list_drives(DriveQuery(include_terminal=False)):
            highest = 0
            for delivery in await self._repo.list_deliveries(record.drive_id):
                if delivery.readiness_generation > highest:
                    highest = delivery.readiness_generation
            if highest:
                self._readiness_gen[record.drive_id] = highest
