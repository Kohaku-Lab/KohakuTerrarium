"""Lifecycle reconciliation + recovery DriveManager operations (design §6, §13).

:class:`DriveManagerReconcileMixin` is mixed into
:class:`~kohakuterrarium.terrarium.drive.manager.DriveManager` alongside
:class:`~kohakuterrarium.terrarium.drive.manager_ops.DriveManagerOps`. It owns
the operations that recover or re-home a Drive rather than mutate its intent:
the §13 dead-letter replay, the §6.1 reconcile (resume + uncertain-attempt
recovery), the §6.2 orphan-and-block on creature removal, and the §6.5 resume
assignee remap that restores a persisted assignment after a cold resume
re-minted creature ids (R1-43).

It relies on the concrete manager for ``_repo`` / ``_clock`` / ``_mint`` /
``_snapshot`` / ``_emit`` / ``_require`` / ``_run_mutation`` / ``_dispatcher`` /
``_maybe_enqueue`` / ``_topology_validator`` / ``_current_readiness_generation`` /
``_rebuild_readiness_generations``. Reconcile admission is readiness-gated through
``_maybe_enqueue`` (R1-05) and revalidates the assignment against live topology
(R1-06). No LLM, no spec interpretation. Split out of ``drive.manager_ops`` to
respect the file-size cap.
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
    """Recovery / reconciliation DriveManager operations (see module docstring)."""

    # -- dead-letter replay (§13) --------------------------------------------

    async def replay_dead_letter(
        self, delivery_id: str, *, actor: ActorRef, is_privileged: bool = False
    ) -> DriveDelivery:
        """Admin replay: mint a NEW delivery id with lineage to the dead-lettered
        one (design §13). The Drive's status is unchanged — an admin reactivates
        a blocked Drive separately before the new delivery can be admitted."""
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

    # -- reconcile (§6.1) ----------------------------------------------------

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
        # R1-06: revalidate the persisted assignment against LIVE topology before
        # any recovery/resume admission. An invalid target (assignee gone from the
        # graph, cross-graph, or stale canonical graph) is explicitly orphaned and
        # blocked, never delivered (design §6.1/§6.2).
        if (
            assignment is not None
            and assignment.assignee_creature_id is not None
            and self._topology_validator is not None
            and not self._topology_validator(assignment)
        ):
            # Already-orphaned invalid targets are left as-is — re-orphaning on
            # every reconcile would churn the revision and audit log for nothing.
            if assignment.assignment_state != "orphaned":
                await self.orphan_and_block(record, reason="reconcile_invalid_topology")
            return
        now = self._clock()
        deliveries = await self._repo.list_deliveries(record.drive_id)
        uncertain = [d for d in deliveries if classify_uncertain(d)]
        if uncertain:
            # §6.1: when recovery is admittable the uncertain prior attempt is
            # superseded AND its recovery delivery enqueued in ONE transaction,
            # so a crash between the two cannot lose the recovery intent. When
            # recovery is only DEFERRED (readiness / dependency / backpressure)
            # the uncertain row is LEFT INTACT so a later reconcile re-classifies
            # and recovers it with the warning — never a warning-less resume.
            # Recovery stays readiness-gated (R1-05).
            claimed = [d for d in deliveries if d.state == "claimed"]
            await self._recover_uncertain(record, assignment, uncertain, claimed)
            return
        for delivery in deliveries:
            if delivery.state == "claimed":
                await self._repo.mark_delivery(delivery.delivery_id, "pending", now=now)
        # R1-05: resume re-admission goes through the same readiness-gated path as
        # create/scan — a now-unready Drive is not redelivered, and a readiness
        # that raises fails closed. Only a manual wake overrides readiness, and
        # reconcile never issues one.
        reason = "resume"
        # A continuation-style Drive whose CURRENT generation already settled
        # (acknowledged) must re-arm to the NEXT generation on restart — exactly as
        # the live post-ack path does — not re-admit a stale-generation duplicate
        # that dispatch would supersede (R1-05). NOT_APPLICABLE means the
        # registration does not re-arm, so fall back to an ordinary resume;
        # DEFERRED means the re-arm is supported but held off (cooldown / dependency
        # / backpressure), so enqueue no stale resume and let the next scan retry.
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
        """Recover uncertain attempts (§6.1 crash-atomic recovery). When recovery
        is admittable the uncertain attempt(s) are superseded AND their recovery
        delivery enqueued in ONE transaction (readiness-gated, R1-05). When
        recovery is only DEFERRED (readiness / dependency / backpressure blocks
        admission now) the uncertain rows are LEFT INTACT — superseding a
        deferred attempt would downgrade its eventual restart to a warning-less
        resume — so a later reconcile re-classifies and recovers them once
        admittable, and the superseded rows never consume the one-per-epoch
        initial grant they never completed.

        Admission is decided INSIDE the ``build`` transaction against txn-current
        state (round-3c concurrent gap): readiness is computed externally into a
        versioned token (its registration callback must not run under the SQLite
        lock), and the in-txn re-check re-reads status / assignment / live rows /
        the unique recovery key / backpressure and re-validates the token before
        anything supersedes the uncertain attempt."""
        superseding = frozenset(d.delivery_id for d in uncertain)
        token = await self._recovery_admitted(record, assignment, superseding)

        async def build(txn, moment):
            mutation = Mutation()
            # Claimed rows are dispatcher-lease artifacts: reset to pending on
            # every reconcile so a crashed claim can redeliver, independent of
            # whether recovery admits.
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
                # Build from txn-current rows.  Admission already proved every
                # named row is still uncertain; never overwrite an acknowledgement
                # (or any other concurrent state change) with a stale preflight row.
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
        """Re-validate §6.1 recovery admission against TXN-CURRENT state under the
        repository lock (round-3c concurrent gap). Admits only when the txn-current
        status is deliverable, the assignment is live, NO other live delivery and
        NO non-superseded recovery already exist for this drive+epoch (the unique
        recovery key), pending backpressure allows, and the external readiness
        token is still valid (its versioned inputs are unchanged) AND admits. Any
        other verdict defers: the caller supersedes nothing, leaving the uncertain
        intent intact for a later reconcile (round-3c sequential-deferred
        behaviour)."""
        current = await txn.get_drive(record.drive_id)
        if current is None or not is_deliverable_status(current.status):
            return False
        # Readiness is evaluated before entering the repository lock. The short
        # lease prevents a time-sensitive verdict from being reused indefinitely.
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
        # Unique logical recovery key: at most one non-superseded recovery per
        # drive+epoch, so a concurrent reconcile cannot enqueue a duplicate.
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
        """Dependency statuses read through the open transaction (txn-current),
        the §6.1 in-txn counterpart of ``_dependency_statuses``. A missing
        dependency reads as ACTIVE so admission stays gated rather than firing
        against a vanished dependency."""
        out: dict[str, DriveStatus] = {}
        for dep_id in record.dependency_ids:
            dep = await txn.get_drive(dep_id)
            out[dep_id] = dep.status if dep is not None else DriveStatus.ACTIVE
        return out

    async def _pending_count_txn(self, txn, graph_id: str) -> int:
        """Per-graph pending-delivery count read through the open transaction, the
        §6.1 in-txn counterpart of ``_pending_count`` (backpressure re-check)."""
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

    # -- orphan-and-block (§6.2 creature-scoped removal) ---------------------

    async def orphan_and_block(
        self, record: DriveRecord, *, reason: str = "assignee_removed"
    ) -> DriveRecord:
        """A creature-scoped Drive whose creature is removed becomes orphaned and
        blocked (never silently reassigned). ``reason`` also serves the resume
        remap's ambiguous/missing-assignee case (R1-43)."""

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

    # -- resume assignee remap (§6.5, R1-43) ---------------------------------

    async def remap_assignee(
        self, drive_id: str, new_creature_id: str, *, graph_id: str | None = None
    ) -> DriveRecord:
        """Rebind a persisted assignment (and a creature-scoped ``scope_id``) from
        a stale old-runtime creature id to the resumed creature's current id.

        This restores the SAME logical assignment — it is not a semantic
        reassignment, so the assignment id is preserved and no delivery is
        superseded; the revision bump merely fences any pre-resume delivery.
        ``graph_id`` rehomes the assignment onto the resumed graph so it is
        queryable again (the resumed graph gets a fresh id too). An audited,
        deterministic identity restoration (design §6.5, R1-43)."""

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
