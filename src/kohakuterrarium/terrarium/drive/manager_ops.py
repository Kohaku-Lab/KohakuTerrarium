"""Heavier DriveManager operations split out to respect the file-size cap.

:class:`DriveManagerOps` is mixed into
:class:`~kohakuterrarium.terrarium.drive.manager.DriveManager`. It carries the
operations that need a hand-built transaction rather than a ready-made repository
mutation: the §4.2 terminal-proposal pipeline (validate -> verifier fail-closed
-> atomic commit), owner transfer, dead-letter replay with lineage, the §6.1
reconcile (resume + uncertain-attempt recovery), and the §6.2 orphan-and-block.

It relies on the concrete manager for ``_repo`` / ``_clock`` / ``_mint`` /
``_snapshot`` / ``_emit`` / ``_require`` / ``_has_live_delivery`` /
``_enqueue_reason`` / ``_current_readiness_generation`` / ``_dependency_statuses``.
No LLM, no spec interpretation.
"""

from dataclasses import replace
from typing import Any

import kohakuterrarium.terrarium.drive.wire as drive_wire
from kohakuterrarium.terrarium.drive.acl import DriveOperation, authorize
from kohakuterrarium.terrarium.drive.errors import (
    DriveConflictError,
    DriveError,
    DriveIdempotencyConflictError,
    DriveNotFoundError,
    DrivePermissionError,
    DriveTransitionError,
    DriveValidationError,
)
from kohakuterrarium.terrarium.drive.models import (
    ActorRef,
    DriveRecord,
    DriveStatus,
)
from kohakuterrarium.terrarium.drive.repository import (
    IdempotencyRecord,
    Mutation,
    new_audit,
    new_outbox,
    op_hash,
    require_revision,
)
from kohakuterrarium.terrarium.drive.requests import (
    DriveTransitionProposal,
)

_TERMINAL_PROPOSAL_TARGETS = frozenset({DriveStatus.COMPLETED, DriveStatus.FAILED})


class DriveManagerOps:
    """Transaction-building DriveManager operations (see module docstring)."""

    # -- generic custom-mutation helper (idempotency + atomic commit) --------

    async def _run_mutation(
        self,
        operation: str,
        actor: ActorRef,
        idempotency_key: str | None,
        payload: dict[str, Any],
        build,
    ) -> Any:
        """Open a repository transaction, honour the idempotency ledger, run
        ``build(txn, now) -> (Mutation, result)``, stamp the ledger, and commit
        atomically — the same contract the repository's own mutations use."""
        async with self._repo.transaction() as txn:
            digest: str | None = None
            if idempotency_key:
                digest = op_hash(operation, payload)
                existing = await txn.get_idempotency(actor.format(), idempotency_key)
                if existing is not None:
                    if existing.operation_hash != digest:
                        raise DriveIdempotencyConflictError(
                            f"idempotency key {idempotency_key!r} reused with a "
                            "different operation",
                            idempotency_key=idempotency_key,
                        )
                    return drive_wire.unpack(existing.result)
            now = self._clock()
            mutation, result = await build(txn, now)
            if idempotency_key and digest is not None:
                mutation.idempotency.append(
                    IdempotencyRecord(
                        actor=actor.format(),
                        key=idempotency_key,
                        operation_hash=digest,
                        result=drive_wire.pack(result),
                        created_at=now,
                    )
                )
            await txn.apply(mutation)
            return result

    async def _write_audit(
        self,
        record: DriveRecord,
        actor: ActorRef,
        operation: str,
        *,
        summary: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        async with self._repo.transaction() as txn:
            current = await txn.get_drive(record.drive_id)
            if current is None:
                return
            audit = new_audit(
                current,
                actor,
                operation,
                self._clock(),
                self._mint(),
                summary=summary,
                details=details or {},
            )
            await txn.apply(Mutation(audit=[audit]))

    # -- owner transfer ------------------------------------------------------

    async def transfer_owner(
        self,
        drive_id: str,
        new_owner: ActorRef,
        *,
        expected_revision: int,
        actor: ActorRef,
        idempotency_key: str | None = None,
        is_privileged: bool = False,
    ) -> DriveRecord:
        """Move the ownership boundary (design §3.2); ``created_by`` is unchanged."""
        current = await self._require(drive_id)
        assignment = await self._repo.get_assignment(drive_id)
        authorize(
            DriveOperation.TRANSFER_OWNER,
            actor,
            current,
            assignment,
            self._snapshot,
            is_privileged=is_privileged,
        )

        async def build(txn, now):
            record = await txn.get_drive(drive_id)
            if record is None:
                raise DriveNotFoundError(f"no Drive {drive_id!r}")
            require_revision(record, expected_revision)
            updated = replace(
                record,
                owner=new_owner,
                revision=record.revision + 1,
                updated_by=actor,
                updated_at=now,
            )
            audit = new_audit(
                updated,
                actor,
                "transfer_owner",
                now,
                self._mint(),
                details={
                    "old_owner": record.owner.format(),
                    "new_owner": new_owner.format(),
                },
            )
            outbox = new_outbox(drive_id, "drive_owner_transferred", now, self._mint())
            return Mutation(drives=[updated], audit=[audit], outbox=[outbox]), updated

        result = await self._run_mutation(
            "transfer_owner",
            actor,
            idempotency_key,
            {"drive_id": drive_id, "new_owner": new_owner.format()},
            build,
        )
        self._emit(
            "drive_owner_transferred",
            drive_id,
            {"new_owner": new_owner.format(), "revision": result.revision},
        )
        return result

    # -- terminal proposal pipeline (§4.2) -----------------------------------

    async def propose_transition(
        self,
        drive_id: str,
        target_status: DriveStatus,
        *,
        actor: ActorRef,
        evidence: dict[str, Any] | None = None,
        reason: str | None = None,
        expected_revision: int | None = None,
        is_privileged: bool = False,
    ) -> DriveTransitionProposal | DriveRecord:
        """Propose a terminal transition. Verifier ``none`` auto-accepts and
        returns the committed record; ``extension`` runs the registration
        verifier fail-closed; ``actor``/``two_party`` return a pending proposal
        awaiting :meth:`approve_proposal`."""
        if target_status not in _TERMINAL_PROPOSAL_TARGETS:
            raise DriveValidationError(
                "propose_transition is for terminal completed/failed only"
            )
        current = await self._require(drive_id)
        assignment = await self._repo.get_assignment(drive_id)
        authorize(
            DriveOperation.PROPOSE_TERMINAL,
            actor,
            current,
            assignment,
            self._snapshot,
            is_privileged=is_privileged,
        )
        self._check_evidence_size(evidence)
        self._validate_registration_transition(
            current, target_status, {"terminal": True, "evidence": evidence or {}}
        )
        # Pin the proposal to the revision/epoch at CREATION even when the caller
        # omitted a revision, so a later canonical change conflicts at approval
        # rather than silently finalizing the newer Drive (R1-08).
        pinned_revision = (
            expected_revision if expected_revision is not None else current.revision
        )
        proposal = DriveTransitionProposal(
            proposal_id=self._mint(),
            drive_id=drive_id,
            target_status=target_status,
            proposed_by=actor,
            created_at=self._clock(),
            reason=reason,
            evidence=evidence or {},
            expected_revision=pinned_revision,
            lifecycle_epoch=current.lifecycle_epoch,
        )
        mode = self._verifier_mode(current.kind)
        match mode:
            case "none":
                return await self._finalize_proposal(proposal, actor)
            case "extension":
                await self._run_extension_verifier(current, proposal, actor)
                return await self._finalize_proposal(proposal, actor)
            case _:  # actor / two_party — wait for an authorized distinct approver
                await self._persist_proposal(proposal)
                self._emit(
                    "drive_proposal_pending",
                    drive_id,
                    {
                        "proposal_id": proposal.proposal_id,
                        "target": target_status.value,
                    },
                )
                return proposal

    async def _persist_proposal(self, proposal: DriveTransitionProposal) -> None:
        """Durably record a pending two-party/actor proposal (R1-08) and mirror
        it in the in-memory index (rebuilt from the store on resume)."""
        async with self._repo.transaction() as txn:
            await txn.apply(Mutation(proposals=[proposal]))
        self._pending_proposals[proposal.proposal_id] = proposal

    async def _load_proposal(self, proposal_id: str) -> DriveTransitionProposal | None:
        """The authoritative pending proposal from the store, falling back to the
        in-memory mirror only for a not-yet-persisted immediate proposal."""
        stored = await self._repo.get_proposal(proposal_id)
        if stored is not None:
            return stored
        return self._pending_proposals.get(proposal_id)

    async def approve_proposal(
        self,
        proposal_id: str,
        *,
        actor: ActorRef,
        is_privileged: bool = False,
        operator: bool = False,
    ) -> DriveRecord:
        """Finalize a pending actor/two_party proposal (§4.2)."""
        proposal = await self._load_proposal(proposal_id)
        if proposal is None:
            raise DriveValidationError(f"no pending proposal {proposal_id!r}")
        current = await self._require(proposal.drive_id)
        assignment = await self._repo.get_assignment(proposal.drive_id)
        authorize(
            DriveOperation.VERIFY_TERMINAL,
            actor,
            current,
            assignment,
            self._snapshot,
            is_privileged=is_privileged,
            extra_grants=self._operator_grants(operator),
        )
        if (
            self._verifier_mode(current.kind) == "two_party"
            and actor == proposal.proposed_by
        ):
            raise DrivePermissionError(
                "two_party completion requires a distinct approver"
            )
        # The proposal was pinned at creation: reject if the canonical Drive has
        # since changed revision/epoch (update / owner transfer / reassignment),
        # so a stale proposal cannot finalize the newer Drive (R1-08).
        self._require_proposal_current(proposal, current)
        # The operator-grant audit and the proposal removal both ride the
        # terminal-transition mutation (R1-40, R1-08): all atomic with finalize.
        record = await self._finalize_proposal(
            proposal,
            actor,
            operator_grant=self._operator_grant_details(operator, "verify_terminal"),
            delete_proposal_id=proposal.proposal_id,
        )
        self._pending_proposals.pop(proposal_id, None)
        return record

    @staticmethod
    def _require_proposal_current(
        proposal: DriveTransitionProposal, current: DriveRecord
    ) -> None:
        if (
            proposal.expected_revision is not None
            and proposal.expected_revision != current.revision
        ) or proposal.lifecycle_epoch != current.lifecycle_epoch:
            raise DriveConflictError(
                f"proposal {proposal.proposal_id!r} was pinned to revision "
                f"{proposal.expected_revision}/epoch {proposal.lifecycle_epoch} but "
                f"the Drive is now revision {current.revision}/epoch "
                f"{current.lifecycle_epoch}",
                expected_revision=proposal.expected_revision,
                actual_revision=current.revision,
            )

    async def _finalize_proposal(
        self,
        proposal: DriveTransitionProposal,
        actor: ActorRef,
        *,
        operator_grant: dict[str, Any] | None = None,
        delete_proposal_id: str | None = None,
    ) -> DriveRecord:
        current = await self._require(proposal.drive_id)
        revision = (
            proposal.expected_revision
            if proposal.expected_revision is not None
            else current.revision
        )
        record = await self._repo.transition_drive(
            proposal.drive_id,
            proposal.target_status,
            expected_revision=revision,
            actor=actor,
            terminal_evidence=proposal.evidence or None,
            extra_transitions=self._registration_extra_transitions(current.kind),
            operation="propose_terminal",
            operator_grant=operator_grant,
            delete_proposal_id=delete_proposal_id,
        )
        self._emit(
            "drive_status_changed",
            proposal.drive_id,
            {"status": record.status.value, "revision": record.revision},
        )
        return record

    async def _run_extension_verifier(
        self,
        current: DriveRecord,
        proposal: DriveTransitionProposal,
        actor: ActorRef,
    ) -> None:
        entry = self._snapshot.for_kind(current.kind) if self._snapshot else None
        verify = getattr(entry.registration, "verify_terminal", None) if entry else None
        if not callable(verify):
            await self._write_audit(
                current,
                actor,
                "verify_rejected",
                summary="required terminal verifier is missing",
                details={"proposal_id": proposal.proposal_id},
            )
            raise DriveTransitionError(
                "required terminal verifier is missing",
                from_status=current.status.value,
                to_status=proposal.target_status.value,
            )
        try:
            result = verify(proposal, {"record": current})
        except Exception as exc:  # verifier raising fails closed (§4.2, §8.8)
            await self._write_audit(
                current,
                actor,
                "verify_rejected",
                summary=str(exc),
                details={"proposal_id": proposal.proposal_id},
            )
            raise DriveTransitionError(
                f"terminal verifier failed: {exc}",
                from_status=current.status.value,
                to_status=proposal.target_status.value,
            ) from exc
        if result is None or not getattr(result, "approved", False):
            reason = getattr(result, "reason", None) if result is not None else None
            await self._write_audit(
                current,
                actor,
                "verify_rejected",
                summary=reason or "",
                details={"proposal_id": proposal.proposal_id},
            )
            raise DriveTransitionError(
                "terminal verifier rejected the proposal",
                from_status=current.status.value,
                to_status=proposal.target_status.value,
            )

    def _verifier_mode(self, kind: str) -> str:
        entry = self._snapshot.for_kind(kind) if self._snapshot else None
        return entry.descriptor.verifier_mode if entry is not None else "none"

    def _registration_extra_transitions(
        self, kind: str
    ) -> frozenset[tuple[DriveStatus, DriveStatus]]:
        """Extra transition edges the enabled registration approves beyond the
        generic graph (design §3.3, R1-25), e.g. a controlled terminal reopen.

        Duck-typed ``extra_transitions()`` on the registration; an unavailable
        registration or a missing method declares none. A malformed edge fails
        closed (a registration must not smuggle a non-status transition)."""
        entry = self._snapshot.for_kind(kind) if self._snapshot else None
        if entry is None or not entry.available:
            return frozenset()
        fn = getattr(entry.registration, "extra_transitions", None)
        if not callable(fn):
            return frozenset()
        edges: set[tuple[DriveStatus, DriveStatus]] = set()
        for edge in fn() or ():
            if (
                isinstance(edge, tuple)
                and len(edge) == 2
                and isinstance(edge[0], DriveStatus)
                and isinstance(edge[1], DriveStatus)
            ):
                edges.add(edge)
            else:
                raise DriveValidationError(
                    "extra_transitions must yield (DriveStatus, DriveStatus) tuples, "
                    f"got {edge!r}"
                )
        return frozenset(edges)

    def _validate_registration_transition(
        self, current: DriveRecord, target: DriveStatus, context: dict[str, Any]
    ) -> None:
        entry = self._snapshot.for_kind(current.kind) if self._snapshot else None
        if entry is None or not entry.available:
            return
        validate = getattr(entry.registration, "validate_transition", None)
        if not callable(validate):
            return
        try:
            validate(current, target, context)
        except DriveError:
            raise
        except Exception as exc:  # a broken registration validator fails closed
            raise DriveTransitionError(
                f"registration rejected transition: {exc}",
                from_status=current.status.value,
                to_status=target.value,
            ) from exc
