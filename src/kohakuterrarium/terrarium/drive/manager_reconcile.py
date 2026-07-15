"""Lifecycle reconciliation + recovery DriveManager operations (design §6, §13).

Recovery and assignment-restoration operations for :class:`DriveManager`.

The mixin replays dead letters, reconciles interrupted delivery, blocks drives
whose assignees disappear, and restores persisted assignments after a cold
resume changes runtime creature or graph identifiers. Reconciliation always
revalidates persisted assignments against live topology and routes admission
through the same readiness gate used by normal delivery.

The concrete manager supplies repository, clock, identity, mutation, dispatch,
topology-validation, and readiness-generation services.
"""

from dataclasses import replace
from typing import Any

from kohakuterrarium.terrarium.drive.acl import DriveOperation, authorize
from kohakuterrarium.terrarium.drive.delivery import BLOCKABLE_STATUSES
from kohakuterrarium.terrarium.drive.errors import (
    DriveDeliveryError,
    DriveNotFoundError,
    DriveValidationError,
)
from kohakuterrarium.terrarium.drive.lifecycle import classify_uncertain
from kohakuterrarium.terrarium.drive.manager_admit import (
    RecoveryAdmission,
    recovery_admission_version,
)
from kohakuterrarium.terrarium.drive.manager_readiness import (
    _LIVE_DELIVERY_STATES,
    _PENDING_DELIVERY_STATES,
    ReadinessOutcome,
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
from kohakuterrarium.terrarium.drive.repository import (
    Mutation,
    in_graph,
    new_audit,
    new_outbox,
)
from kohakuterrarium.terrarium.drive.requests import DriveQuery


class DriveManagerReconcileMixin:
    """Recover interrupted delivery and restore persisted assignments."""

    async def replay_dead_letter(
        self, delivery_id: str, *, actor: ActorRef, is_privileged: bool = False
    ) -> DriveDelivery:
        """Replay a dead letter under a new delivery ID while preserving lineage.

        Replay does not reactivate a blocked drive; status changes remain a
        separate authorized operation before admission can succeed.
        """
        async with self._repo.transaction() as txn:
            old = await txn.get_delivery(delivery_id)
        if old is None:
            raise DriveDeliveryError(f"no delivery {delivery_id!r}")
        if old.state != "dead_letter":
            raise DriveDeliveryError(f"delivery {delivery_id!r} is not dead-lettered")
        record = await self._require(old.drive_id)
        assignment = await self._repo.get_assignment(old.drive_id)
        authorize(
            DriveOperation.REPLAY,
            actor,
            record,
            assignment,
            self._snapshot,
            is_privileged=is_privileged,
        )

        async def build(txn, now):
            current = await txn.get_drive(old.drive_id)
            assign = await txn.get_assignment(old.drive_id)
            if assign is None or assign.assignee_creature_id is None:
                raise DriveValidationError(
                    f"cannot replay for unassigned Drive {old.drive_id!r}"
                )
            new_delivery = DriveDelivery(
                delivery_id=self._mint(),
                drive_id=old.drive_id,
                drive_revision=current.revision,
                lifecycle_epoch=current.lifecycle_epoch,
                assignment_id=assign.assignment_id,
                assignee_creature_id=assign.assignee_creature_id,
                reason="retry",
                state="pending",
                attempt=0,
                available_at=now,
                created_at=now,
                readiness_generation=self._current_readiness_generation(old.drive_id),
            )
            outbox = new_outbox(
                old.drive_id,
                "drive_ready",
                now,
                self._mint(),
                delivery_id=new_delivery.delivery_id,
                payload={"replayed_from": delivery_id},
            )
            audit = new_audit(
                current,
                actor,
                "replay_delivery",
                now,
                self._mint(),
                details={
                    "replayed_from": delivery_id,
                    "new_delivery": new_delivery.delivery_id,
                },
            )
            return (
                Mutation(deliveries=[new_delivery], outbox=[outbox], audit=[audit]),
                new_delivery,
            )

        new_delivery = await self._run_mutation(
            "replay", actor, None, {"delivery_id": delivery_id}, build
        )
        self._emit(
            "drive_delivery_replayed",
            old.drive_id,
            {"replayed_from": delivery_id, "delivery_id": new_delivery.delivery_id},
        )
        self._dispatcher.notify()
        return new_delivery

    async def reconcile(
        self,
        *,
        creature_id: str | None = None,
        graph_id: str | None = None,
        all: bool = False,
    ) -> None:
        """Resume still-current active Drives and recover uncertain attempts."""
        await self._rebuild_readiness_generations()
        for record in await self._reconcile_targets(creature_id, graph_id, all):
            await self._reconcile_drive(record)
        self._dispatcher.notify()

    async def _reconcile_targets(
        self, creature_id: str | None, graph_id: str | None, all: bool
    ) -> tuple[DriveRecord, ...]:
        if all:
            return await self._repo.list_drives(DriveQuery(include_terminal=False))
        if graph_id is not None:
            return await self._repo.list_drives(
                DriveQuery(graph_id=graph_id, include_terminal=False)
            )
        if creature_id is not None:
            return await self._repo.list_drives(
                DriveQuery(assignee_creature_id=creature_id, include_terminal=False)
            )
        return ()

    async def _reconcile_drive(self, record: DriveRecord) -> None:
        assignment = await self._repo.get_assignment(record.drive_id)
        # Persisted assignments may outlive their runtime topology. Invalid or
        # cross-graph targets are orphaned and blocked rather than redelivered.
        if (
            assignment is not None
            and assignment.assignee_creature_id is not None
            and self._topology_validator is not None
            and not self._topology_validator(assignment)
        ):
            # Orphaning is idempotent at reconciliation time so repeated scans do
            # not churn revisions or duplicate audit entries.
            if assignment.assignment_state != "orphaned":
                await self.orphan_and_block(record, reason="reconcile_invalid_topology")
            return
        now = self._clock()
        deliveries = await self._repo.list_deliveries(record.drive_id)
        uncertain = [d for d in deliveries if classify_uncertain(d)]
        if uncertain:
            # Superseding an uncertain attempt and enqueuing its recovery are one
            # transaction so a crash cannot erase recovery intent. Deferred
            # attempts remain uncertain and are retried with recovery semantics
            # once readiness, dependencies, and backpressure permit admission.
            claimed = [d for d in deliveries if d.state == "claimed"]
            await self._recover_uncertain(record, assignment, uncertain, claimed)
            return
        for delivery in deliveries:
            if delivery.state == "claimed":
                await self._repo.mark_delivery(delivery.delivery_id, "pending", now=now)
        # Resume uses normal readiness admission and fails closed on readiness
        # errors. Reconciliation never applies the manual-wake override.
        reason = "resume"
        # A settled continuation re-arms its next readiness generation instead of
        # producing a stale duplicate. Registrations that cannot re-arm fall back
        # to ordinary resume; deferred re-arms wait for a later readiness scan.
        if await self._current_generation_settled(record):
            outcome = await self._reevaluate_readiness(record, reason=reason)
            if outcome is ReadinessOutcome.NOT_APPLICABLE:
                await self._maybe_enqueue(record, reason)
        elif await self._maybe_enqueue(record, reason) is None:
            await self._reevaluate_readiness(record, reason=reason)

    async def _recover_uncertain(
        self,
        record: DriveRecord,
        assignment: DriveAssignment | None,
        uncertain: list[DriveDelivery],
        claimed: list[DriveDelivery],
    ) -> None:
        """Atomically supersede uncertain attempts and enqueue one recovery.

        Deferred attempts stay uncertain so their eventual restart retains
        recovery semantics. Readiness is evaluated outside the repository lock,
        then represented by a short-lived versioned token. The transaction
        rechecks status, assignment, delivery uniqueness, dependencies, and
        backpressure before accepting that token or changing any attempt.
        """
        superseding = frozenset(d.delivery_id for d in uncertain)
        token = await self._recovery_admitted(record, assignment, superseding)

        async def build(txn, moment):
            mutation = Mutation()
            # A claimed row represents a dispatcher lease, not completed work;
            # reconciliation releases stale claims even when recovery is deferred.
            for delivery in claimed:
                current_claim = await txn.get_delivery(delivery.delivery_id)
                if current_claim is not None and current_claim.state == "claimed":
                    mutation.deliveries.append(replace(current_claim, state="pending"))
            recovery: DriveDelivery | None = None
            if await self._recovery_admitted_in_txn(
                txn, record, superseding, token, moment
            ):
                current = await txn.get_drive(record.drive_id)
                assign = await txn.get_assignment(record.drive_id)
                # Use transaction-current rows so stale preflight data cannot
                # overwrite an acknowledgement or another concurrent state change.
                for delivery_id in superseding:
                    current_delivery = await txn.get_delivery(delivery_id)
                    mutation.deliveries.append(
                        replace(current_delivery, state="superseded")
                    )
                recovery = DriveDelivery(
                    delivery_id=self._mint(),
                    drive_id=record.drive_id,
                    drive_revision=current.revision,
                    lifecycle_epoch=current.lifecycle_epoch,
                    assignment_id=assign.assignment_id,
                    assignee_creature_id=assign.assignee_creature_id,
                    reason="recovery",
                    state="pending",
                    attempt=0,
                    available_at=moment,
                    created_at=moment,
                    readiness_generation=self._current_readiness_generation(
                        record.drive_id
                    ),
                )
                mutation.deliveries.append(recovery)
                mutation.outbox.append(
                    new_outbox(
                        record.drive_id,
                        "drive_ready",
                        moment,
                        self._mint(),
                        delivery_id=recovery.delivery_id,
                    )
                )
            return mutation, recovery

        recovery = await self._run_mutation(
            "recover", SYSTEM_ACTOR, None, {"drive_id": record.drive_id}, build
        )
        if recovery is not None:
            self._emit(
                "drive_ready",
                record.drive_id,
                {"delivery_id": recovery.delivery_id, "reason": "recovery"},
            )
            self._dispatcher.notify()

    async def _recovery_admitted_in_txn(
        self,
        txn,
        record: DriveRecord,
        superseding: frozenset[str],
        token: RecoveryAdmission,
        now,
    ) -> bool:
        """Validate recovery admission against transaction-current state.

        Admission requires a deliverable drive, a live assignment, no competing
        live delivery or recovery for the epoch, available graph capacity, and an
        unexpired readiness token whose version still matches current inputs.
        Failure defers without superseding the uncertain attempt.
        """
        current = await txn.get_drive(record.drive_id)
        if current is None or not is_deliverable_status(current.status):
            return False
        # Readiness callbacks run outside the repository lock; token expiry bounds
        # how long their time-sensitive result may be reused.
        if not token.valid_at(now):
            return False
        assignment = await txn.get_assignment(record.drive_id)
        if (
            assignment is None
            or assignment.drive_id != current.drive_id
            or assignment.assignment_state != "assigned"
            or assignment.lifecycle_epoch != current.lifecycle_epoch
            or not assignment.assignee_graph_id
            or assignment.assignee_creature_id is None
        ):
            return False
        deliveries = list(await txn.deliveries_for_drive(record.drive_id))
        current_superseding = [d for d in deliveries if d.delivery_id in superseding]
        if len(current_superseding) != len(superseding) or any(
            not classify_uncertain(d) for d in current_superseding
        ):
            return False
        if any(
            d.state in _LIVE_DELIVERY_STATES and d.delivery_id not in superseding
            for d in deliveries
        ):
            return False
        # A drive and lifecycle epoch may have only one non-superseded recovery,
        # which makes concurrent reconciliation converge on one delivery.
        if any(
            d.reason == "recovery"
            and d.lifecycle_epoch == current.lifecycle_epoch
            and d.state != "superseded"
            for d in deliveries
        ):
            return False
        deps = await self._dependency_statuses_txn(txn, current)
        if not wake_conditions_met(current, now, deps):
            return False
        if (
            await self._pending_count_txn(txn, assignment.assignee_graph_id)
            >= self._config.max_pending_per_graph
        ):
            return False
        version = recovery_admission_version(
            current, assignment, deliveries, deps, superseding, now
        )
        return version == token.version and token.admits

    async def _dependency_statuses_txn(
        self, txn, record: DriveRecord
    ) -> dict[str, DriveStatus]:
        """Read dependency states from the open transaction.

        Missing dependencies are treated as active so recovery remains gated
        instead of firing against vanished state.
        """
        out: dict[str, DriveStatus] = {}
        for dep_id in record.dependency_ids:
            dep = await txn.get_drive(dep_id)
            out[dep_id] = dep.status if dep is not None else DriveStatus.ACTIVE
        return out

    async def _pending_count_txn(self, txn, graph_id: str) -> int:
        """Count transaction-current pending deliveries for graph backpressure."""
        in_graph_ids: set[str] = set()
        for record in await txn.all_drives():
            assignment = await txn.get_assignment(record.drive_id)
            if in_graph(record, assignment, graph_id):
                in_graph_ids.add(record.drive_id)
        count = 0
        for delivery in await txn.all_deliveries():
            if (
                delivery.state in _PENDING_DELIVERY_STATES
                and delivery.drive_id in in_graph_ids
            ):
                count += 1
        return count

    async def orphan_and_block(
        self, record: DriveRecord, *, reason: str = "assignee_removed"
    ) -> DriveRecord:
        """Orphan and block a drive whose creature assignment is no longer valid.

        The drive is never silently reassigned; ``reason`` records whether removal
        or ambiguous resume identity caused the orphaning.
        """

        async def build(txn, now):
            current = await txn.get_drive(record.drive_id)
            if current is None:
                raise DriveNotFoundError(f"no Drive {record.drive_id!r}")
            prev = await txn.get_assignment(record.drive_id)
            new_status = (
                DriveStatus.BLOCKED
                if current.status in BLOCKABLE_STATUSES
                else current.status
            )
            updated = replace(
                current,
                revision=current.revision + 1,
                status=new_status,
                status_reason=reason,
                updated_by=SYSTEM_ACTOR,
                updated_at=now,
            )
            audit = new_audit(
                updated,
                SYSTEM_ACTOR,
                "orphan",
                now,
                self._mint(),
                before=current.status,
                after=updated.status,
                details={"reason": reason},
            )
            outbox = new_outbox(record.drive_id, "drive_orphaned", now, self._mint())
            mutation = Mutation(drives=[updated], audit=[audit], outbox=[outbox])
            if prev is not None:
                mutation.assignments.append(
                    replace(
                        prev,
                        revision=updated.revision,
                        assignment_state="orphaned",
                        lease_owner=None,
                        lease_expires_at=None,
                        updated_at=now,
                    )
                )
            return mutation, updated

        result = await self._run_mutation(
            "orphan", SYSTEM_ACTOR, None, {"drive_id": record.drive_id}, build
        )
        self._emit("drive_orphaned", record.drive_id, {"status": result.status.value})
        return result

    async def remap_assignee(
        self, drive_id: str, new_creature_id: str, *, graph_id: str | None = None
    ) -> DriveRecord:
        """Restore a persisted assignment after runtime identifiers change.

        This preserves the logical assignment and its ID rather than performing a
        semantic reassignment. The revision bump fences pre-resume deliveries,
        while ``graph_id`` places the assignment in the newly identified graph.
        Creature-scoped drives update their matching scope identifier as well.
        """

        async def build(txn, now):
            current = await txn.get_drive(drive_id)
            if current is None:
                raise DriveNotFoundError(f"no Drive {drive_id!r}")
            prev = await txn.get_assignment(drive_id)
            if prev is None:
                raise DriveValidationError(f"Drive {drive_id!r} has no assignment")
            old_id = prev.assignee_creature_id
            fields: dict[str, Any] = {
                "revision": current.revision + 1,
                "updated_by": SYSTEM_ACTOR,
                "updated_at": now,
            }
            if current.scope_type == "creature" and current.scope_id == old_id:
                fields["scope_id"] = new_creature_id
            updated = replace(current, **fields)
            assignment = replace(
                prev,
                assignee_creature_id=new_creature_id,
                assignee_graph_id=graph_id or prev.assignee_graph_id,
                revision=updated.revision,
                lease_owner=None,
                lease_expires_at=None,
                updated_at=now,
            )
            audit = new_audit(
                updated,
                SYSTEM_ACTOR,
                "resume_remap_assignee",
                now,
                self._mint(),
                details={"old": old_id, "new": new_creature_id},
            )
            outbox = new_outbox(drive_id, "drive_reassigned", now, self._mint())
            return (
                Mutation(
                    drives=[updated],
                    assignments=[assignment],
                    audit=[audit],
                    outbox=[outbox],
                ),
                updated,
            )

        result = await self._run_mutation(
            "resume_remap", SYSTEM_ACTOR, None, {"drive_id": drive_id}, build
        )
        self._emit(
            "drive_reassigned",
            drive_id,
            {"assignee": new_creature_id, "revision": result.revision},
        )
        return result
