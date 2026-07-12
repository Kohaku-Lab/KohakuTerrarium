"""Recovery-admission readiness-token machinery (design §6.1; round-3c gap).

Extracted from :mod:`drive.manager_readiness` so both the external preflight
(:meth:`ManagerReadinessMixin._recovery_admitted`) and the in-txn re-check
(:meth:`DriveManagerReconcileMixin._recovery_admitted_in_txn`) share ONE
fingerprint definition. Pure module: it computes a versioned token that binds a
recovery-admission verdict to the dependency/readiness inputs it was decided
against, so a drift between preflight and commit defers admission rather than
superseding an uncertain attempt on a stale verdict. No storage, no clock.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from kohakuterrarium.terrarium.drive.models import (
    DriveAssignment,
    DriveDelivery,
    DriveRecord,
    DriveStatus,
)


@dataclass(frozen=True)
class RecoveryAdmission:
    """Externally-computed §6.1 recovery-admission token (round-3c concurrent gap).

    ``admits`` is the readiness/backpressure verdict computed BEFORE the
    repository transaction, so the registration readiness callback never runs
    under the SQLite lock. ``version`` fingerprints the dependency/readiness
    inputs it saw; the in-txn admission re-derives it from txn-current reads and
    defers on any drift rather than superseding on a stale verdict.
    """

    admits: bool
    version: tuple
    evaluated_at: datetime

    def valid_at(
        self, now: datetime, *, tolerance: timedelta = timedelta(seconds=1)
    ) -> bool:
        """Whether the preflight verdict remains inside its short validity lease."""
        drift = now - self.evaluated_at
        return timedelta(0) <= drift <= tolerance


def recovery_admission_version(
    record: DriveRecord,
    assignment: DriveAssignment | None,
    deliveries: list[DriveDelivery],
    deps: dict[str, DriveStatus],
    superseding: frozenset[str],
    evaluated_at: datetime,
) -> tuple:
    """Fingerprint every input a §6.1 recovery admission depends on: the Drive's
    status/revision/epoch, the live assignment identity, the settled-turn count
    (excluding the superseded uncertain rows), and the dependency states."""
    turns_used = sum(
        1
        for d in deliveries
        if d.state == "acknowledged" and d.delivery_id not in superseding
    )
    deps_fingerprint = tuple(sorted((k, v.value) for k, v in deps.items()))
    return (
        record.status.value,
        record.revision,
        record.lifecycle_epoch,
        assignment.drive_id if assignment is not None else None,
        assignment.assignment_id if assignment is not None else None,
        assignment.revision if assignment is not None else None,
        assignment.lifecycle_epoch if assignment is not None else None,
        assignment.assignment_state if assignment is not None else None,
        assignment.assignee_graph_id if assignment is not None else None,
        assignment.assignee_creature_id if assignment is not None else None,
        turns_used,
        deps_fingerprint,
    )
