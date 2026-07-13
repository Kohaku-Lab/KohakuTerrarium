"""Storage-agnostic Drive repository orchestration (design §4, §7).

:class:`BaseDriveRepository` turns the pure builders in
:mod:`drive.repository` into atomic transactions: it owns the serialization
lock, idempotency ledger, and the canonical Drive mutations, all against the
:class:`DriveTransaction` seam. The delivery-plane / read / export surface is
mixed in from :mod:`drive.repository_ops`. The two backends
(:mod:`drive.memory`, :mod:`drive.store`) implement only the transaction seam,
so every backend is behaviourally identical. This layer owns no asyncio
background task and imports no Agent/engine code (impl-plan Phase B rules).
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import kohakuterrarium.terrarium.drive.wire as drive_wire
from kohakuterrarium.terrarium.drive.errors import (
    DriveIdempotencyConflictError,
    DriveNotFoundError,
)
from kohakuterrarium.terrarium.drive.models import (
    ActorRef,
    DriveProgress,
    DriveRecord,
    DriveStatus,
)
from kohakuterrarium.terrarium.drive.repository import (
    SUPERSEDABLE_DELIVERY_STATES,
    DriveTransaction,
    IdempotencyRecord,
    Mutation,
    build_create,
    build_progress,
    build_reassign,
    build_transition,
    build_update,
    op_hash,
    require_revision,
)
from kohakuterrarium.terrarium.drive.repository_ops import DeliveryOpsMixin
from kohakuterrarium.terrarium.drive.requests import CreateDriveRequest, DrivePatch
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_DRIVE_ID_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
_DRIVE_ID_LENGTH = 6
_DRIVE_ID_ATTEMPTS = 32


class BaseDriveRepository(DeliveryOpsMixin):
    """Shared Drive orchestration over a :class:`DriveTransaction` backend."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._lock = asyncio.Lock()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._mint = id_factory or (lambda: uuid.uuid4().hex)

    # -- backend seam --------------------------------------------------------

    def _new_transaction(self) -> DriveTransaction:
        raise NotImplementedError

    @property
    def durability(self) -> str:
        raise NotImplementedError

    async def close(self) -> None:
        return None

    # -- transaction plumbing ------------------------------------------------

    @asynccontextmanager
    async def _txn(self) -> AsyncIterator[DriveTransaction]:
        async with self._lock:
            txn = self._new_transaction()
            await txn.begin()
            committed = False
            try:
                yield txn
                await txn.commit()
                committed = True
            finally:
                if not committed:
                    await txn.rollback()

    def transaction(self):
        """Public low-level transaction (Phase D dispatcher + crash tests)."""
        return self._txn()

    # -- idempotency helpers -------------------------------------------------

    async def _begin_idem(
        self,
        txn: DriveTransaction,
        actor: ActorRef,
        key: str | None,
        operation: str,
        payload: dict[str, Any],
    ) -> tuple[str | None, Any]:
        """Return ``(op_hash, replay_or_None)``; raise on hash mismatch."""
        if not key:
            return None, None
        digest = op_hash(operation, payload)
        existing = await txn.get_idempotency(actor.format(), key)
        if existing is not None:
            if existing.operation_hash != digest:
                raise DriveIdempotencyConflictError(
                    f"idempotency key {key!r} reused with a different operation",
                    idempotency_key=key,
                )
            return digest, drive_wire.unpack(existing.result)
        return digest, None

    def _stamp_idem(
        self,
        mutation: Mutation,
        actor: ActorRef,
        key: str | None,
        digest: str | None,
        result: Any,
    ) -> Mutation:
        if key and digest is not None:
            mutation.idempotency.append(
                IdempotencyRecord(
                    actor=actor.format(),
                    key=key,
                    operation_hash=digest,
                    result=drive_wire.pack(result),
                    created_at=self._clock(),
                )
            )
        return mutation

    async def _require(self, txn: DriveTransaction, drive_id: str) -> DriveRecord:
        record = await txn.get_drive(drive_id)
        if record is None:
            raise DriveNotFoundError(f"no Drive {drive_id!r}")
        return record

    def _new_drive_id(self, kind: str) -> str:
        token = "".join(
            _DRIVE_ID_ALPHABET[byte % len(_DRIVE_ID_ALPHABET)]
            for byte in uuid.uuid4().bytes[:_DRIVE_ID_LENGTH]
        )
        return f"{kind}-{token}"

    async def _mint_drive_id(self, txn: DriveTransaction, kind: str) -> str:
        for _ in range(_DRIVE_ID_ATTEMPTS):
            drive_id = self._new_drive_id(kind)
            if await txn.get_drive(drive_id) is None:
                return drive_id
        raise DriveIdempotencyConflictError(
            "could not mint a unique Drive ID after repeated collisions"
        )

    # -- canonical mutations -------------------------------------------------

    async def create_drive(
        self,
        request: CreateDriveRequest,
        *,
        actor: ActorRef,
        graph_id: str,
        initial_status: DriveStatus = DriveStatus.ACTIVE,
        operator_grant: dict[str, Any] | None = None,
    ) -> DriveRecord:
        payload = {
            k: v
            for k, v in drive_wire.pack_create_request(request)["data"].items()
            if k != "idempotency_key"
        }
        async with self._txn() as txn:
            digest, replay = await self._begin_idem(
                txn, actor, request.idempotency_key, "create", payload
            )
            if replay is not None:
                return replay
            drive_id = await self._mint_drive_id(txn, request.kind)
            mutation, record = build_create(
                request,
                actor=actor,
                graph_id=graph_id,
                status=initial_status,
                now=self._clock(),
                mint=self._mint,
                drive_id=drive_id,
                operator_grant=operator_grant,
            )
            await txn.apply(
                self._stamp_idem(
                    mutation, actor, request.idempotency_key, digest, record
                )
            )
            return record

    async def update_drive(
        self,
        drive_id: str,
        patch: DrivePatch,
        *,
        expected_revision: int,
        actor: ActorRef,
        idempotency_key: str | None = None,
    ) -> DriveRecord:
        payload = {"drive_id": drive_id, "changes": drive_wire.pack_drive_patch(patch)}
        async with self._txn() as txn:
            digest, replay = await self._begin_idem(
                txn, actor, idempotency_key, "update", payload
            )
            if replay is not None:
                return replay
            current = await self._require(txn, drive_id)
            require_revision(current, expected_revision)
            mutation, record = build_update(
                current, patch, actor=actor, now=self._clock(), mint=self._mint
            )
            await txn.apply(
                self._stamp_idem(mutation, actor, idempotency_key, digest, record)
            )
            return record

    async def assign_drive(
        self,
        drive_id: str,
        *,
        assignee_creature_id: str,
        assignee_graph_id: str,
        expected_revision: int,
        actor: ActorRef,
        idempotency_key: str | None = None,
        operator_grant: dict[str, Any] | None = None,
    ) -> DriveRecord:
        return await self._reassign(
            drive_id,
            assignee_creature_id,
            assignee_graph_id,
            expected_revision,
            actor,
            idempotency_key,
            "assign",
            operator_grant=operator_grant,
        )

    async def unassign_drive(
        self,
        drive_id: str,
        *,
        expected_revision: int,
        actor: ActorRef,
        idempotency_key: str | None = None,
    ) -> DriveRecord:
        return await self._reassign(
            drive_id, None, None, expected_revision, actor, idempotency_key, "unassign"
        )

    async def _reassign(
        self,
        drive_id: str,
        assignee: str | None,
        graph: str | None,
        expected_revision: int,
        actor: ActorRef,
        key: str | None,
        operation: str,
        operator_grant: dict[str, Any] | None = None,
    ) -> DriveRecord:
        payload = {"drive_id": drive_id, "assignee": assignee, "graph": graph}
        async with self._txn() as txn:
            digest, replay = await self._begin_idem(txn, actor, key, operation, payload)
            if replay is not None:
                return replay
            current = await self._require(txn, drive_id)
            require_revision(current, expected_revision)
            prev = await txn.get_assignment(drive_id)
            superseded = [
                d
                for d in await txn.deliveries_for_drive(drive_id)
                if d.state in SUPERSEDABLE_DELIVERY_STATES
            ]
            mutation, record = build_reassign(
                current,
                prev,
                superseded,
                assignee=assignee,
                graph=graph,
                actor=actor,
                operation=operation,
                now=self._clock(),
                mint=self._mint,
                operator_grant=operator_grant,
            )
            await txn.apply(self._stamp_idem(mutation, actor, key, digest, record))
            return record

    async def transition_drive(
        self,
        drive_id: str,
        target_status: DriveStatus,
        *,
        expected_revision: int,
        actor: ActorRef,
        terminal_evidence: dict[str, Any] | None = None,
        status_reason: str | None = None,
        extra_transitions: frozenset[tuple[DriveStatus, DriveStatus]] = frozenset(),
        idempotency_key: str | None = None,
        operation: str = "transition",
        operator_grant: dict[str, Any] | None = None,
        delete_proposal_id: str | None = None,
    ) -> DriveRecord:
        payload = {"drive_id": drive_id, "target": target_status.value}
        async with self._txn() as txn:
            digest, replay = await self._begin_idem(
                txn, actor, idempotency_key, operation, payload
            )
            if replay is not None:
                return replay
            current = await self._require(txn, drive_id)
            require_revision(current, expected_revision)
            mutation, record = build_transition(
                current,
                target_status,
                actor=actor,
                terminal_evidence=terminal_evidence,
                status_reason=status_reason,
                extra_transitions=extra_transitions,
                operation=operation,
                now=self._clock(),
                mint=self._mint,
                operator_grant=operator_grant,
            )
            # A finalized terminal proposal is removed in the SAME transaction as
            # its transition, so completion and removal are atomic (R1-08).
            if delete_proposal_id is not None:
                mutation.deleted_proposals.append(delete_proposal_id)
            await txn.apply(
                self._stamp_idem(mutation, actor, idempotency_key, digest, record)
            )
            return record

    async def retire_drive(
        self,
        drive_id: str,
        *,
        expected_revision: int,
        actor: ActorRef,
        idempotency_key: str | None = None,
    ) -> DriveRecord:
        """Move a terminal Drive to RETIRED. Rows are never deleted (§7.4)."""
        return await self.transition_drive(
            drive_id,
            DriveStatus.RETIRED,
            expected_revision=expected_revision,
            actor=actor,
            idempotency_key=idempotency_key,
            operation="retire",
        )

    async def report_progress(
        self,
        drive_id: str,
        *,
        summary: str,
        evidence: dict[str, Any] | None,
        actor: ActorRef,
        idempotency_key: str | None = None,
    ) -> DriveProgress:
        """Append-only progress (design §4.3): NO revision change, no CAS."""
        payload = {"drive_id": drive_id, "summary": summary, "evidence": evidence or {}}
        async with self._txn() as txn:
            digest, replay = await self._begin_idem(
                txn, actor, idempotency_key, "report_progress", payload
            )
            if replay is not None:
                return replay
            current = await self._require(txn, drive_id)
            mutation, progress = build_progress(
                current,
                summary=summary,
                evidence=evidence,
                actor=actor,
                now=self._clock(),
                mint=self._mint,
            )
            await txn.apply(
                self._stamp_idem(mutation, actor, idempotency_key, digest, progress)
            )
            return progress
