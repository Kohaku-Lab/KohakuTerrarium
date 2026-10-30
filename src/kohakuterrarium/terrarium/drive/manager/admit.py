"""Recovery-admission readiness-token machinery (design §6.1; round-3c gap).

Recovery admission tokens shared by external preflight and transactional checks.

A token binds a readiness verdict to the drive, assignment, delivery, and
dependency state used to compute it. Any drift before commit invalidates the
verdict, deferring recovery rather than superseding an uncertain attempt from
stale state. This module is pure and owns neither storage nor time.
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
    """Short-lived recovery verdict computed outside the repository transaction.

    ``version`` fingerprints the inputs behind ``admits``. Transactional
    admission recomputes the fingerprint and defers on drift, allowing readiness
    callbacks to remain outside the SQLite lock without trusting stale state.
    """

    admits: bool
    version: tuple
    evaluated_at: datetime

    def valid_at(
        self, now: datetime, *, tolerance: timedelta = timedelta(seconds=1)
    ) -> bool:
        """Return whether the preflight verdict remains within its validity lease."""
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
    """Fingerprint state that must remain stable between preflight and commit.

    Acknowledged turns exclude the uncertain rows being superseded because those
    attempts never consumed a completed turn grant.
    """
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
