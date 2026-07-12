"""In-memory Drive repository (design §7.1 "no session" mode).

:class:`MemoryDriveRepository` is the ephemeral backend used by engines with no
attached session/store. It has the SAME transactional semantics as the SQLite
backend — buffered writes applied atomically at commit, discarded on rollback —
so both pass the identical behaviour suite. State survives creature stop but not
process shutdown; :attr:`durability` reports ``"ephemeral"``.
"""

import copy
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime

from kohakuterrarium.terrarium.drive.models import (
    DriveAssignment,
    DriveAuditRecord,
    DriveDelivery,
    DriveProgress,
    DriveRecord,
)
from kohakuterrarium.terrarium.drive.repository import (
    DriveDeadLetter,
    DriveOutboxEntry,
    IdempotencyRecord,
    Mutation,
)
from kohakuterrarium.terrarium.drive.repository_base import BaseDriveRepository
from kohakuterrarium.terrarium.drive.requests import DriveTransitionProposal


class _MemoryDriveTransaction:
    """Buffers mutations against the repository's dicts; applies on commit."""

    def __init__(self, repo: "MemoryDriveRepository") -> None:
        self._repo = repo
        self._pending: list[Mutation] = []

    async def begin(self) -> None:
        self._pending = []

    async def commit(self) -> None:
        # _apply mutates the canonical dicts/lists incrementally, so a mid-apply
        # failure must restore the pre-commit snapshot (commit is all-or-nothing).
        snapshot = self._repo._snapshot_state()
        try:
            for mutation in self._pending:
                self._repo._apply(mutation)
        except BaseException:
            self._repo._restore_state(snapshot)
            raise
        self._pending = []

    async def rollback(self) -> None:
        self._pending = []

    async def apply(self, mutation: Mutation) -> None:
        self._pending.append(mutation)

    # -- reads (committed state; ops read before they stage) -----------------
    #
    # Records with mutable dicts (spec/metadata/presentation/policy_options/
    # terminal_evidence, progress/audit evidence, outbox/dead-letter payloads)
    # are deep-copied out so a caller mutating a fetched record cannot reach
    # canonical state without a revision/audit (R1-07). The SQLite backend
    # deserializes a fresh copy per read, so both backends isolate identically.

    async def get_drive(self, drive_id: str) -> DriveRecord | None:
        return copy.deepcopy(self._repo._drives.get(drive_id))

    async def get_assignment(self, drive_id: str) -> DriveAssignment | None:
        return self._repo._assignments.get(drive_id)

    async def get_delivery(self, delivery_id: str) -> DriveDelivery | None:
        return self._repo._deliveries.get(delivery_id)

    async def get_idempotency(self, actor: str, key: str) -> IdempotencyRecord | None:
        return self._repo._idempotency.get((actor, key))

    async def get_proposal(self, proposal_id: str) -> DriveTransitionProposal | None:
        return copy.deepcopy(self._repo._proposals.get(proposal_id))

    async def all_proposals(self) -> list[DriveTransitionProposal]:
        return [copy.deepcopy(p) for p in self._repo._proposals.values()]

    async def all_idempotency(self) -> list[IdempotencyRecord]:
        return [copy.deepcopy(r) for r in self._repo._idempotency.values()]

    async def all_drives(self) -> list[DriveRecord]:
        return [copy.deepcopy(r) for r in self._repo._drives.values()]

    async def all_deliveries(self) -> list[DriveDelivery]:
        return list(self._repo._deliveries.values())

    async def all_dead_letters(self) -> list[DriveDeadLetter]:
        return [copy.deepcopy(d) for d in self._repo._dead_letters.values()]

    async def deliveries_for_drive(self, drive_id: str) -> list[DriveDelivery]:
        return [d for d in self._repo._deliveries.values() if d.drive_id == drive_id]

    async def progress_for_drive(self, drive_id: str) -> list[DriveProgress]:
        return [
            copy.deepcopy(p) for p in self._repo._progress if p.drive_id == drive_id
        ]

    async def audit_for_drive(self, drive_id: str) -> list[DriveAuditRecord]:
        return [copy.deepcopy(a) for a in self._repo._audit if a.drive_id == drive_id]

    async def outbox_entries(
        self, *, include_dispatched: bool
    ) -> list[DriveOutboxEntry]:
        return [
            copy.deepcopy(e)
            for e in self._repo._outbox.values()
            if include_dispatched or not e.dispatched
        ]


class MemoryDriveRepository(BaseDriveRepository):
    """Ephemeral Drive repository backed by process-local dicts."""

    # Canonical containers snapshotted for all-or-nothing commit (see commit()).
    _STATE_COLLECTIONS = (
        "_drives",
        "_assignments",
        "_deliveries",
        "_audit",
        "_progress",
        "_outbox",
        "_dead_letters",
        "_idempotency",
        "_proposals",
    )

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        super().__init__(clock=clock, id_factory=id_factory)
        self._drives: dict[str, DriveRecord] = {}
        self._assignments: dict[str, DriveAssignment] = {}
        self._deliveries: dict[str, DriveDelivery] = {}
        self._audit: list[DriveAuditRecord] = []
        self._progress: list[DriveProgress] = []
        self._outbox: dict[str, DriveOutboxEntry] = {}
        self._dead_letters: dict[str, DriveDeadLetter] = {}
        self._idempotency: dict[tuple[str, str], IdempotencyRecord] = {}
        self._proposals: dict[str, DriveTransitionProposal] = {}

    def _new_transaction(self) -> _MemoryDriveTransaction:
        return _MemoryDriveTransaction(self)

    @property
    def durability(self) -> str:
        return "ephemeral"

    def _snapshot_state(self) -> dict[str, object]:
        # Shallow copy each container: _apply never mutates a stored record in
        # place (it replaces entries / appends), so restoring these references
        # recovers the pre-commit state without regressing R1-07 isolation.
        return {
            name: copy.copy(getattr(self, name)) for name in self._STATE_COLLECTIONS
        }

    def _restore_state(self, snapshot: dict[str, object]) -> None:
        for name, value in snapshot.items():
            setattr(self, name, value)

    def _apply(self, mutation: Mutation) -> None:
        # Deep-copy the mutable-dict-bearing rows on ingress so a caller that
        # keeps a reference to a record it staged cannot mutate committed state
        # after the fact (R1-07); the SQLite backend serializes on write for the
        # same isolation.
        for record in mutation.drives:
            self._drives[record.drive_id] = copy.deepcopy(record)
        for assignment in mutation.assignments:
            self._assignments[assignment.drive_id] = assignment
        for delivery in mutation.deliveries:
            self._deliveries[delivery.delivery_id] = delivery
        self._audit.extend(copy.deepcopy(a) for a in mutation.audit)
        self._progress.extend(copy.deepcopy(p) for p in mutation.progress)
        for entry in mutation.outbox:
            self._outbox[entry.outbox_id] = copy.deepcopy(entry)
        for letter in mutation.dead_letters:
            self._dead_letters[letter.delivery_id] = copy.deepcopy(letter)
        for idem in mutation.idempotency:
            self._idempotency[(idem.actor, idem.key)] = idem
        for proposal in mutation.proposals:
            self._proposals[proposal.proposal_id] = copy.deepcopy(proposal)
        for outbox_id in mutation.outbox_dispatched:
            entry = self._outbox.get(outbox_id)
            if entry is not None:
                self._outbox[outbox_id] = replace(entry, dispatched=True)
        for delivery_id in mutation.deleted_deliveries:
            self._deliveries.pop(delivery_id, None)
        for proposal_id in mutation.deleted_proposals:
            self._proposals.pop(proposal_id, None)
        for drive_id in mutation.deleted_drives:
            self._delete_drive_cascade(drive_id)

    def _delete_drive_cascade(self, drive_id: str) -> None:
        # Remove a Drive and every per-drive row so a split's retained repo keeps
        # only its child subset (R1-10); idempotency is (actor,key)-keyed, not
        # per-drive, so it is intentionally untouched (replicated to every child).
        self._drives.pop(drive_id, None)
        self._assignments.pop(drive_id, None)
        for did in [d for d, v in self._deliveries.items() if v.drive_id == drive_id]:
            self._deliveries.pop(did, None)
        for oid in [o for o, e in self._outbox.items() if e.drive_id == drive_id]:
            self._outbox.pop(oid, None)
        for dlid in [
            k for k, dl in self._dead_letters.items() if dl.drive_id == drive_id
        ]:
            self._dead_letters.pop(dlid, None)
        for pid in [p for p, pr in self._proposals.items() if pr.drive_id == drive_id]:
            self._proposals.pop(pid, None)
        self._progress = [p for p in self._progress if p.drive_id != drive_id]
        self._audit = [a for a in self._audit if a.drive_id != drive_id]
