"""Readiness, enqueue, backpressure, and scan helpers for the DriveManager.

:class:`ManagerReadinessMixin` is split out of
:mod:`drive.manager` to respect the file-size cap. It owns the decisions about
*when* a Drive earns a delivery: readiness/dependency evaluation, the
enqueue-once-per-readiness-event gate, per-creature/per-graph backpressure
(design §5.6), and the periodic scan that wakes WAITING Drives and activates
Drives whose ``not_before`` has passed. It relies on the concrete manager for
``_repo`` / ``_config`` / ``_snapshot`` / ``_clock`` / ``_emit`` / ``_dispatcher``
/ ``_readiness_gen``. No LLM, no spec interpretation.
"""

import inspect
import json
from datetime import datetime
from enum import Enum
from typing import Any

from kohakuterrarium.terrarium.drive.acl import DriveOperation
from kohakuterrarium.terrarium.drive.delivery import BLOCKABLE_STATUSES
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
    """Result of a post-settlement re-arm attempt (design §4.4, R1-05).

    Reconcile branches on this to tell a genuinely-not-a-re-arm registration
    apart from one whose re-arm is only temporarily deferred:
    ``NOT_APPLICABLE`` — the registration does not re-arm, so an ordinary resume
    is correct; ``DEFERRED`` — it WOULD re-arm but a cooldown / dependency /
    backpressure / in-flight gate holds it off, so no stale-generation resume is
    enqueued and the next readiness scan retries; ``REARMED`` — the generation
    advanced and the continuation delivery was enqueued.
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


def _call_readiness(
    registration: Any,
    record: DriveRecord,
    deps: dict[str, DriveStatus],
    now: datetime,
    turns_used: int,
) -> Any:
    """Call a registration's readiness role, forwarding ``turns_used`` only when
    the implementation accepts it (the generic role keeps the 3-argument shape)."""
    fn = registration.readiness
    if _accepts_turns_used(fn):
        return fn(record, deps, now, turns_used=turns_used)
    return fn(record, deps, now)


class ManagerReadinessMixin:
    """Readiness / enqueue / backpressure / scan surface (see module docstring)."""

    def _current_readiness_generation(self, drive_id: str) -> int:
        # Readiness generation only advances for registrations that re-arm a
        # waiting Drive; the generic registration never does, so this stays 0.
        return self._readiness_gen.get(drive_id, 0)

    async def _current_generation_settled(self, record: DriveRecord) -> bool:
        """True when the Drive's CURRENT readiness generation has an acknowledged
        delivery for its current lifecycle epoch — the generation ran and settled,
        so the next admission is a re-arm rather than a fresh resume (R1-05)."""
        gen = self._current_readiness_generation(record.drive_id)
        for delivery in await self._repo.list_deliveries(record.drive_id):
            if (
                delivery.readiness_generation == gen
                and delivery.lifecycle_epoch == record.lifecycle_epoch
                and delivery.state == "acknowledged"
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
            validate(spec)  # DriveValidationError propagates (fail closed)

    def _check_payload_size(
        self, field: str, value: dict[str, Any] | None, max_bytes: int
    ) -> None:
        """Central per-field payload guard (R1-09, design §13): reject a
        non-dict, non-JSON-safe, or over-cap payload for one canonical field.

        JSON safety is validated by encoding WITHOUT a ``default=`` coercion, so
        a non-serializable value raises rather than being silently stringified;
        the byte size is the UTF-8 length of that encoding. The manager cap is
        authoritative and runs independently of any registration's own cap."""
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
        """Validate the canonical record payload fields against their configured
        caps (R1-09). ``policy_options`` has no dedicated cap, so it is bounded
        by the metadata cap and JSON-safety-checked so it cannot smuggle
        unbounded canonical data."""
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
            # A missing dependency reads as non-terminal so the Drive stays
            # not-ready rather than delivering against a vanished dependency.
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
        # Registration readiness gate (design §5.2): consult the enabled
        # registration before admitting. A manual wake is an explicit authorized
        # override and bypasses the gate; every other admission (create / scan /
        # activation) is gated and fails closed on a readiness error.
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
        """Whether the enabled registration's readiness permits this admission.

        Returns True when no available registration serves the kind or it
        exposes no readiness role (nothing to gate on). Otherwise the verdict's
        ``ready`` admits any admission and ``initial`` admits only the one
        initial event of an activation epoch (no prior delivery for the current
        lifecycle epoch). A readiness that raises fails closed: the Drive is
        blocked and this admission is denied (design §5.2, §8.8).

        ``superseding`` names deliveries the caller will supersede in the SAME
        atomic mutation (§6.1 recovery): they are excluded from the delivery
        history so an uncertain attempt neither counts as a settled turn nor
        consumes the one-per-epoch initial grant it never completed (R1-05)."""
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
        except Exception as exc:  # readiness failure fails closed (§8.8)
            await self._block_on_readiness_error(record, str(exc))
            return False
        if verdict is None:
            return False
        if getattr(verdict, "ready", False):
            return True
        if getattr(verdict, "initial", False):
            # The one-per-epoch initial grant is consumed only by a delivery that
            # actually reached the creature. An uncertain attempt that reconcile
            # SUPERSEDED never completed, so it must NOT consume the grant: a
            # manual-Goal recovery re-admits the initial rather than being denied
            # because a superseded row exists in the epoch (R1-05, §6.1).
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
        """External §6.1 recovery preflight → a versioned token
        (:class:`RecoveryAdmission`). Computes the readiness verdict OUTSIDE the
        repository transaction — the registration readiness callback must never
        run under the SQLite lock — and pairs it with a fingerprint of the inputs
        it saw. ``_recovery_admitted_in_txn`` re-reads and re-validates every gate
        against txn-current state before anything supersedes the uncertain
        attempt (round-3c concurrent gap)."""
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
        """Whether a §6.1 recovery delivery may be admitted, treating the
        uncertain deliveries named in ``superseding`` as already superseded (they
        are, in the same atomic mutation). Mirrors ``_maybe_enqueue``'s gates —
        deliverable status, a live assignee, wake conditions, no OTHER live
        delivery, readiness (fail-closed), and per-graph pending backpressure —
        so recovery does NOT override readiness (R1-05)."""
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
        """Periodic readiness scan: wake WAITING Drives whose time/dependency
        conditions are met, and activate ACTIVE Drives that became ready but were
        never delivered in their current epoch (e.g. a future ``not_before``)."""
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
                # Already delivered this epoch: consider a post-settlement re-arm
                # once the current generation has acknowledged (design §4.4).
                await self._reevaluate_readiness(record)
                continue
            deps = await self._dependency_statuses(record)
            if wake_conditions_met(record, now, deps):
                await self._maybe_enqueue(record, "activated")

    async def _reevaluate_readiness(
        self, record: DriveRecord, *, reason: str = "ready"
    ) -> ReadinessOutcome:
        """Post-settlement continuation (design §4.4, §5.3, §11.4).

        Once the current generation settles, ask the enabled registration whether
        to re-arm. If it does — and the per-Drive cooldown has elapsed and
        fairness/backpressure allow — advance the readiness generation (a fresh
        §5.3 logical key) and enqueue the next delivery. A readiness calculator
        that raises fails closed: the Drive is blocked, no delivery is admitted,
        and a blocked Drive is never re-evaluated, so there is no crash loop.

        Returns a three-way :class:`ReadinessOutcome` so reconcile can tell a
        registration that does not re-arm (``NOT_APPLICABLE`` -> fall back to an
        ordinary resume) apart from one whose re-arm is only temporarily deferred
        (``DEFERRED`` -> leave it for the next scan, never a stale resume);
        ``REARMED`` means the generation advanced and a continuation was enqueued
        (R1-05)."""
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
            # A turn is still in flight; wait for it to settle rather than
            # falling back to a duplicate resume.
            return ReadinessOutcome.DEFERRED
        deliveries = await self._repo.list_deliveries(record.drive_id)
        gen = self._current_readiness_generation(record.drive_id)
        settled = [
            d
            for d in deliveries
            if d.readiness_generation == gen
            and d.lifecycle_epoch == record.lifecycle_epoch
            and d.state == "acknowledged"
        ]
        if not settled:
            return ReadinessOutcome.NOT_APPLICABLE  # nothing settled at this gen
        now = self._clock()
        deps = await self._dependency_statuses(record)
        turns_used = sum(1 for d in deliveries if d.state == "acknowledged")
        try:
            verdict = _call_readiness(registration, record, deps, now, turns_used)
        except Exception as exc:  # readiness failure fails closed (§8.8)
            await self._block_on_readiness_error(record, str(exc))
            return ReadinessOutcome.DEFERRED  # blocked; never resume a blocked Drive
        if verdict is None or not getattr(verdict, "re_arm", False):
            return ReadinessOutcome.NOT_APPLICABLE
        # The registration WOULD re-arm; from here every gate is a deferral, not a
        # fallback-to-resume signal (a stale resume would only be superseded).
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
            return (
                ReadinessOutcome.DEFERRED
            )  # cooldown not elapsed; a later scan retries
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
        """§8.8 readiness fail-closed: block the Drive (system actor), emit an
        observable condition, and audit via the transition. No delivery is
        admitted, and a blocked Drive is not re-evaluated."""
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
        except DriveError as exc:  # blocking is best-effort; the error is observed
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
        """Rebuild the in-memory readiness-generation map from persisted delivery
        rows (design §4.4 generation persistence). Called at manager start and on
        reconcile so a cold resume continues from the highest generation each
        Drive reached rather than re-minting generation 0 and superseding work."""
        for record in await self._repo.list_drives(DriveQuery(include_terminal=False)):
            highest = 0
            for delivery in await self._repo.list_deliveries(record.drive_id):
                if delivery.readiness_generation > highest:
                    highest = delivery.readiness_generation
            if highest:
                self._readiness_gen[record.drive_id] = highest
