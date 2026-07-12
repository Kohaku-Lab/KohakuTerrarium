"""Delivery-plane + read + export/import Drive repository ops (design §5, §7).

:class:`DeliveryOpsMixin` carries the non-canonical-mutation surface of the
repository — delivery leases/claims, delivery state marks, retention purge,
reads/queries, the outbox, and the Phase-F export/import move seam. It is mixed
into :class:`~kohakuterrarium.terrarium.drive.repository_base.BaseDriveRepository`
and relies on that class for ``_txn`` / ``_clock`` / ``_mint`` / ``_require``.
Split out of ``drive.repository_base`` only to respect the file-size cap.
"""

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

import kohakuterrarium.terrarium.drive.wire as drive_wire
from kohakuterrarium.terrarium.drive.errors import (
    DriveDeliveryError,
    DriveIdempotencyConflictError,
    DriveProposalConflictError,
    DriveValidationError,
)
from kohakuterrarium.terrarium.drive.models import (
    DriveAssignment,
    DriveAuditRecord,
    DriveDelivery,
    DriveProgress,
    DriveRecord,
)
from kohakuterrarium.terrarium.drive.policy import is_terminal
from kohakuterrarium.terrarium.drive.repository import (
    DriveDeadLetter,
    DriveOutboxEntry,
    DriveTransaction,
    Mutation,
    dead_letter_from_wire,
    dead_letter_to_wire,
    idempotency_from_wire,
    idempotency_to_wire,
    in_graph,
    matches_query,
    new_outbox,
    outbox_from_wire,
    outbox_to_wire,
)
from kohakuterrarium.terrarium.drive.requests import DriveQuery


class DeliveryOpsMixin:
    """Delivery, read, and export/import operations (needs the base's plumbing)."""

    # -- deliveries ----------------------------------------------------------

    async def enqueue_delivery(
        self,
        drive_id: str,
        *,
        reason: str,
        available_at: datetime | None = None,
        readiness_generation: int = 0,
    ) -> DriveDelivery:
        async with self._txn() as txn:
            current = await self._require(txn, drive_id)
            assignment = await txn.get_assignment(drive_id)
            if assignment is None or assignment.assignee_creature_id is None:
                raise DriveValidationError(
                    f"cannot enqueue delivery for unassigned Drive {drive_id!r}"
                )
            now = self._clock()
            delivery = DriveDelivery(
                delivery_id=self._mint(),
                drive_id=drive_id,
                drive_revision=current.revision,
                lifecycle_epoch=current.lifecycle_epoch,
                assignment_id=assignment.assignment_id,
                assignee_creature_id=assignment.assignee_creature_id,
                reason=reason,
                state="pending",
                attempt=0,
                available_at=available_at or now,
                created_at=now,
                readiness_generation=readiness_generation,
            )
            outbox = new_outbox(
                drive_id,
                "drive_ready",
                now,
                self._mint(),
                delivery_id=delivery.delivery_id,
            )
            await txn.apply(Mutation(deliveries=[delivery], outbox=[outbox]))
            return delivery

    async def claim_deliveries(
        self,
        owner: str,
        limit: int,
        *,
        lease_seconds: float,
        now: datetime | None = None,
    ) -> tuple[DriveDelivery, ...]:
        """Claim due deliveries under a dispatcher lease. The lease lives on the
        Drive's assignment (design §3.4): a delivery whose assignment lease is
        held-and-unexpired by another owner is skipped; an expired lease is
        reclaimable. Claiming does not change the assignment's canonical
        revision — the lease is a runtime writer token, not a mutation."""
        async with self._txn() as txn:
            moment = now or self._clock()
            candidates = [
                d
                for d in await txn.all_deliveries()
                if d.state in ("pending", "retry_wait", "claimed")
                and d.available_at <= moment
            ]
            candidates.sort(key=lambda d: (d.available_at, d.created_at, d.delivery_id))
            expiry = moment + timedelta(seconds=lease_seconds)
            mutation = Mutation()
            claimed: list[DriveDelivery] = []
            for delivery in candidates:
                if len(claimed) >= limit:
                    break
                assignment = await txn.get_assignment(delivery.drive_id)
                if assignment is None:
                    continue
                held = (
                    assignment.lease_expires_at is not None
                    and assignment.lease_expires_at > moment
                    and assignment.lease_owner != owner
                )
                if held:
                    continue
                mutation.deliveries.append(
                    replace(delivery, state="claimed", claimed_at=moment)
                )
                mutation.assignments.append(
                    replace(
                        assignment,
                        lease_owner=owner,
                        lease_expires_at=expiry,
                        updated_at=moment,
                    )
                )
                claimed.append(mutation.deliveries[-1])
            if claimed:
                await txn.apply(mutation)
            return tuple(claimed)

    async def mark_delivery(
        self,
        delivery_id: str,
        state: str,
        *,
        error: str | None = None,
        available_at: datetime | None = None,
        attempt: int | None = None,
        reason: str | None = None,
        detail: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> DriveDelivery:
        """Advance a delivery's state (admitted / acknowledged / retry_wait /
        dead_letter / superseded). Dead-letter also writes a snapshot + outbox."""
        async with self._txn() as txn:
            delivery = await txn.get_delivery(delivery_id)
            if delivery is None:
                raise DriveDeliveryError(f"no delivery {delivery_id!r}")
            moment = now or self._clock()
            fields: dict[str, Any] = {"state": state}
            mutation = Mutation()
            match state:
                case "admitted":
                    fields["admitted_at"] = moment
                case "acknowledged":
                    fields["acknowledged_at"] = moment
                case "retry_wait":
                    fields["available_at"] = available_at or moment
                    fields["last_error"] = error
                    fields["attempt"] = (
                        delivery.attempt + 1 if attempt is None else attempt
                    )
                case "dead_letter":
                    fields["last_error"] = error
            updated = replace(delivery, **fields)
            mutation.deliveries.append(updated)
            if state == "dead_letter":
                mutation.dead_letters.append(
                    DriveDeadLetter(
                        delivery_id=delivery_id,
                        drive_id=delivery.drive_id,
                        reason=reason or error or "dead_letter",
                        attempt=updated.attempt,
                        created_at=moment,
                        detail=detail or {},
                    )
                )
                mutation.outbox.append(
                    new_outbox(
                        delivery.drive_id,
                        "drive_dead_lettered",
                        moment,
                        self._mint(),
                        delivery_id=delivery_id,
                    )
                )
            await txn.apply(mutation)
            return updated

    async def purge_deliveries(
        self,
        *,
        acknowledged_seconds: float,
        superseded_seconds: float,
        dead_letter_seconds: float | None = None,
        now: datetime | None = None,
    ) -> int:
        """Delete terminal-safe delivery rows past retention. Never touches a
        delivery whose Drive is still nonterminal (design §7.4)."""
        async with self._txn() as txn:
            moment = now or self._clock()
            live = {
                r.drive_id for r in await txn.all_drives() if not is_terminal(r.status)
            }
            cutoffs: dict[str, float | None] = {
                "acknowledged": acknowledged_seconds,
                "superseded": superseded_seconds,
                "dead_letter": dead_letter_seconds,
            }
            removed: list[str] = []
            for d in await txn.all_deliveries():
                if d.drive_id in live:
                    continue
                cutoff = cutoffs.get(d.state)
                if cutoff is None:
                    continue
                ts = d.acknowledged_at or d.admitted_at or d.created_at
                if (moment - ts).total_seconds() >= cutoff:
                    removed.append(d.delivery_id)
            if removed:
                await txn.apply(Mutation(deleted_deliveries=removed))
            return len(removed)

    # -- reads ---------------------------------------------------------------

    async def get(self, drive_id: str) -> DriveRecord | None:
        async with self._txn() as txn:
            return await txn.get_drive(drive_id)

    async def get_assignment(self, drive_id: str) -> DriveAssignment | None:
        async with self._txn() as txn:
            return await txn.get_assignment(drive_id)

    async def get_proposal(self, proposal_id: str):
        async with self._txn() as txn:
            return await txn.get_proposal(proposal_id)

    async def list_proposals(self) -> tuple:
        async with self._txn() as txn:
            return tuple(await txn.all_proposals())

    async def list_drives(self, query: DriveQuery) -> tuple[DriveRecord, ...]:
        async with self._txn() as txn:
            out: list[DriveRecord] = []
            for record in await txn.all_drives():
                assignment = await txn.get_assignment(record.drive_id)
                if matches_query(query, record, assignment):
                    out.append(record)
            out.sort(key=lambda r: (r.created_at, r.drive_id))
            if query.limit is not None:
                out = out[: query.limit]
            return tuple(out)

    async def list_progress(self, drive_id: str) -> tuple[DriveProgress, ...]:
        async with self._txn() as txn:
            return tuple(await txn.progress_for_drive(drive_id))

    async def list_deliveries(self, drive_id: str) -> tuple[DriveDelivery, ...]:
        async with self._txn() as txn:
            return tuple(await txn.deliveries_for_drive(drive_id))

    async def list_audit(self, drive_id: str) -> tuple[DriveAuditRecord, ...]:
        async with self._txn() as txn:
            return tuple(await txn.audit_for_drive(drive_id))

    async def list_outbox(
        self, *, include_dispatched: bool = False
    ) -> tuple[DriveOutboxEntry, ...]:
        async with self._txn() as txn:
            return tuple(
                await txn.outbox_entries(include_dispatched=include_dispatched)
            )

    async def mark_outbox_dispatched(self, outbox_id: str) -> None:
        async with self._txn() as txn:
            await txn.apply(Mutation(outbox_dispatched=[outbox_id]))

    async def watch_outbox(self):
        """Yield currently-undispatched outbox entries then stop. The dispatcher
        (Phase D) owns the wake loop; this owns no background task (§7.2)."""
        for entry in await self.list_outbox(include_dispatched=False):
            yield entry

    # -- export / import (Phase F logical move seam) -------------------------

    async def export_rows(
        self,
        *,
        drive_ids: frozenset[str] | None = None,
        graph_id: str | None = None,
    ) -> dict[str, Any]:
        """Pack every row for the selected Drives into a portable dict."""
        async with self._txn() as txn:
            selected = await self._select_for_export(txn, drive_ids, graph_id)
            ids = {r.drive_id for r, _ in selected}
            payload: dict[str, Any] = {
                "wire_schema": drive_wire.WIRE_SCHEMA_VERSION,
                "drives": [],
                "assignments": [],
                "deliveries": [],
                "audit": [],
                "progress": [],
                "outbox": [],
                "dead_letters": [],
                # Idempotency is keyed by (actor, key), not drive_id, so the whole
                # ledger travels with the move; a retry after topology movement
                # still returns the original result (R1-11).
                "idempotency": [
                    idempotency_to_wire(r) for r in await txn.all_idempotency()
                ],
                # Pending terminal proposals travel WITH their parent Drive so a
                # merge/split does not silently drop an awaiting approval (R1-08).
                "proposals": [],
            }
            for record, assignment in selected:
                payload["drives"].append(drive_wire.pack_drive_record(record))
                if assignment is not None:
                    payload["assignments"].append(
                        drive_wire.pack_drive_assignment(assignment)
                    )
                for d in await txn.deliveries_for_drive(record.drive_id):
                    payload["deliveries"].append(drive_wire.pack_drive_delivery(d))
                for p in await txn.progress_for_drive(record.drive_id):
                    payload["progress"].append(drive_wire.pack_drive_progress(p))
                for a in await txn.audit_for_drive(record.drive_id):
                    payload["audit"].append(drive_wire.pack_drive_audit(a))
            for e in await txn.outbox_entries(include_dispatched=True):
                if e.drive_id in ids:
                    payload["outbox"].append(outbox_to_wire(e))
            for dl in await txn.all_dead_letters():
                if dl.drive_id in ids:
                    payload["dead_letters"].append(dead_letter_to_wire(dl))
            for prop in await txn.all_proposals():
                if prop.drive_id in ids:
                    payload["proposals"].append(
                        drive_wire.pack_transition_proposal(prop)
                    )
            return payload

    async def _select_for_export(
        self,
        txn: DriveTransaction,
        drive_ids: frozenset[str] | None,
        graph_id: str | None,
    ) -> list[tuple[DriveRecord, DriveAssignment | None]]:
        selected: list[tuple[DriveRecord, DriveAssignment | None]] = []
        for record in await txn.all_drives():
            assignment = await txn.get_assignment(record.drive_id)
            if drive_ids is not None and record.drive_id not in drive_ids:
                continue
            if graph_id is not None and not in_graph(record, assignment, graph_id):
                continue
            selected.append((record, assignment))
        return selected

    async def import_rows(self, payload: dict[str, Any]) -> None:
        """Insert exported rows into this repository (logical copy, no remap).

        Idempotent for the append-only tables (audit / progress) and the
        idempotency ledger: re-importing rows already present — as happens when a
        merge combines a payload that includes the survivor's own rows into the
        survivor's existing store — is deduplicated by stable id, so history is
        never multiplied (R1-11). An idempotency ``(actor, key)`` reused with an
        incompatible operation hash is a transactional conflict. Upsert-only: rows
        already in this repo but absent from ``payload`` are LEFT in place (merge
        is additive)."""
        self._require_import_schema(payload)
        async with self._txn() as txn:
            await txn.apply(
                await self._build_import_mutation(txn, payload, prune=False)
            )

    async def replace_rows(self, payload: dict[str, Any]) -> None:
        """Import ``payload`` AND prune, so this repository ends up holding EXACTLY
        the payload's Drives (plus the graph-global idempotency ledger).

        Split uses this for a retained child that keeps the parent's provider
        repository: that repo still holds every sibling's rows, and an upsert-only
        import would leave the moved Drives duplicated in both graphs. Pruning the
        Drives (and their per-drive rows) that are not in this child's subset —
        atomically, in the same transaction as the import — restores canonical
        single ownership and exact cardinality (R1-10/R1-11)."""
        self._require_import_schema(payload)
        async with self._txn() as txn:
            await txn.apply(await self._build_import_mutation(txn, payload, prune=True))

    @staticmethod
    def _require_import_schema(payload: dict[str, Any]) -> None:
        version = payload.get("wire_schema")
        if version != drive_wire.WIRE_SCHEMA_VERSION:
            raise DriveValidationError(f"unsupported export wire schema {version!r}")

    async def _build_import_mutation(
        self, txn: DriveTransaction, payload: dict[str, Any], *, prune: bool
    ) -> Mutation:
        audit_in = [drive_wire.unpack_drive_audit(p) for p in payload.get("audit", [])]
        progress_in = [
            drive_wire.unpack_drive_progress(p) for p in payload.get("progress", [])
        ]
        idem_in = self._dedupe_idempotency_payload(
            [idempotency_from_wire(p) for p in payload.get("idempotency", [])]
        )
        proposals_in = self._dedupe_proposals_payload(
            [
                drive_wire.unpack_transition_proposal(p)
                for p in payload.get("proposals", [])
            ]
        )
        seen_audit = await self._existing_audit_ids(txn, {a.drive_id for a in audit_in})
        seen_progress = await self._existing_progress_ids(
            txn, {p.drive_id for p in progress_in}
        )
        idem_out = await self._reconcile_idempotency(txn, idem_in)
        proposals_out = await self._reconcile_proposals(txn, proposals_in)
        drives_in = [
            drive_wire.unpack_drive_record(p) for p in payload.get("drives", [])
        ]
        mutation = Mutation(
            drives=drives_in,
            assignments=[
                drive_wire.unpack_drive_assignment(p)
                for p in payload.get("assignments", [])
            ],
            deliveries=[
                drive_wire.unpack_drive_delivery(p)
                for p in payload.get("deliveries", [])
            ],
            audit=[a for a in audit_in if a.audit_id not in seen_audit],
            progress=[p for p in progress_in if p.progress_id not in seen_progress],
            outbox=[outbox_from_wire(p) for p in payload.get("outbox", [])],
            dead_letters=[
                dead_letter_from_wire(p) for p in payload.get("dead_letters", [])
            ],
            idempotency=idem_out,
            proposals=proposals_out,
        )
        if prune:
            keep = {d.drive_id for d in drives_in}
            existing = {r.drive_id for r in await txn.all_drives()}
            mutation.deleted_drives = sorted(existing - keep)
        return mutation

    @staticmethod
    def _dedupe_idempotency_payload(rows: list) -> list:
        """Collapse a payload's own duplicate (actor, key) rows, raising if the
        same key appears with an incompatible operation hash (R1-11)."""
        by_key: dict[tuple[str, str], Any] = {}
        for rec in rows:
            existing = by_key.get((rec.actor, rec.key))
            if existing is not None and existing.operation_hash != rec.operation_hash:
                raise DriveIdempotencyConflictError(
                    f"idempotency key {rec.key!r} collides across merged sources",
                    idempotency_key=rec.key,
                )
            by_key[(rec.actor, rec.key)] = rec
        return list(by_key.values())

    async def _reconcile_idempotency(self, txn: DriveTransaction, rows: list) -> list:
        out = []
        for rec in rows:
            existing = await txn.get_idempotency(rec.actor, rec.key)
            if existing is None:
                out.append(rec)
            elif existing.operation_hash != rec.operation_hash:
                raise DriveIdempotencyConflictError(
                    f"idempotency key {rec.key!r} reused with a different operation "
                    "during topology movement",
                    idempotency_key=rec.key,
                )
        return out

    @staticmethod
    def _dedupe_proposals_payload(rows: list) -> list:
        """Collapse a payload's own duplicate proposal ids, raising if the same
        proposal_id names a DIFFERENT parent Drive across merged sources — a
        genuine identity clash, not a survivor re-import (R1-08)."""
        by_id: dict[str, Any] = {}
        for prop in rows:
            existing = by_id.get(prop.proposal_id)
            if existing is not None and existing.drive_id != prop.drive_id:
                raise DriveProposalConflictError(
                    f"proposal id {prop.proposal_id!r} collides across merged "
                    "sources with a different Drive",
                    proposal_id=prop.proposal_id,
                )
            by_id[prop.proposal_id] = prop
        return list(by_id.values())

    async def _reconcile_proposals(self, txn: DriveTransaction, rows: list) -> list:
        """Import proposals, re-importing a survivor's own row idempotently but
        failing closed when a proposal_id already names a different Drive in the
        destination (a real id collision during topology movement, R1-08)."""
        out = []
        for prop in rows:
            existing = await txn.get_proposal(prop.proposal_id)
            if existing is not None and existing.drive_id != prop.drive_id:
                raise DriveProposalConflictError(
                    f"proposal id {prop.proposal_id!r} reused for a different Drive "
                    "during topology movement",
                    proposal_id=prop.proposal_id,
                )
            out.append(prop)
        return out

    @staticmethod
    async def _existing_audit_ids(
        txn: DriveTransaction, drive_ids: set[str]
    ) -> set[str]:
        seen: set[str] = set()
        for did in drive_ids:
            for row in await txn.audit_for_drive(did):
                seen.add(row.audit_id)
        return seen

    @staticmethod
    async def _existing_progress_ids(
        txn: DriveTransaction, drive_ids: set[str]
    ) -> set[str]:
        seen: set[str] = set()
        for did in drive_ids:
            for row in await txn.progress_for_drive(did):
                seen.add(row.progress_id)
        return seen
