"""DriveManager: the public Drive orchestration façade (design §4, §5, §8.8).

Mutations accept a trusted ``ActorRef`` rather than deriving identity from
payload data. Authorization and registration validation fail closed before the
atomic repository commit; observers run afterward and fail open. The owned
:class:`~kohakuterrarium.terrarium.drive.delivery.DriveDispatcher` schedules
work only at readiness events such as creation, wake, reassignment,
reconciliation, and scanning. Plain updates never enqueue, which prevents a
generic drive from looping within one readiness generation.
"""

import random
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from kohakuterrarium.terrarium.drive.acl import (
    OPERATOR_GRANTS,
    DriveCapability,
    DriveOperation,
    allowed_actions as acl_allowed_actions,
    authorize,
)
from kohakuterrarium.terrarium.drive.config import DriveRuntimeConfig
from kohakuterrarium.terrarium.drive.delivery import DriveDispatcher
from kohakuterrarium.terrarium.drive.sink import (
    DriveDeliverySink,
    DriveObserver,
    emit_observation,
)
from kohakuterrarium.terrarium.drive import lifecycle
from kohakuterrarium.terrarium.drive.errors import (
    DriveNotFoundError,
    DriveValidationError,
)
from kohakuterrarium.terrarium.drive.manager_ops import DriveManagerOps
from kohakuterrarium.terrarium.drive.manager_readiness import ManagerReadinessMixin
from kohakuterrarium.terrarium.drive.manager_reconcile import (
    DriveManagerReconcileMixin,
)
from kohakuterrarium.terrarium.drive.models import (
    ActorRef,
    DriveAssignment,
    DriveDelivery,
    DriveProgress,
    DriveRecord,
    DriveStatus,
)
from kohakuterrarium.terrarium.drive.requests import (
    CreateDriveRequest,
    DrivePatch,
    DriveQuery,
    DriveTransitionProposal,
)
from kohakuterrarium.terrarium.drive.snapshot import EnabledRegistrySnapshot

# Direct transition is limited to control states. Completion and failure require
# verifier approval, while retirement uses its separately authorized operation.
_TRANSITION_FORBIDDEN_TARGETS = frozenset(
    {DriveStatus.COMPLETED, DriveStatus.FAILED, DriveStatus.RETIRED}
)


class DriveManager(DriveManagerOps, DriveManagerReconcileMixin, ManagerReadinessMixin):
    """Coordinate authorized drive mutations, readiness, and delivery."""

    def __init__(
        self,
        repository: Any,
        snapshot: EnabledRegistrySnapshot | None,
        config: DriveRuntimeConfig,
        sink: DriveDeliverySink,
        *,
        observer: DriveObserver | None = None,
        clock: Callable[[], datetime] | None = None,
        rng: Any = None,
        id_factory: Callable[[], str] | None = None,
        owner: str = "drive-dispatcher",
        lease_seconds: float = 30.0,
        scan_interval: float = 5.0,
        topology_validator: "Callable[[DriveAssignment], bool] | None" = None,
    ) -> None:
        self._repo = repository
        self._snapshot = snapshot
        self._config = config
        self._sink = sink
        self._observer = observer
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._rng = rng or random.Random()
        self._mint = id_factory or (lambda: uuid.uuid4().hex)
        # Persisted assignments are valid only while their assignee remains in
        # live topology. Reconciliation orphans invalid assignments when enabled.
        self._topology_validator = topology_validator
        self._readiness_gen: dict[str, int] = {}
        self._pending_proposals: dict[str, DriveTransitionProposal] = {}
        self._dispatcher = DriveDispatcher(
            repository=repository,
            snapshot_provider=lambda: self._snapshot,
            config=config,
            sink=sink,
            clock=self._clock,
            rng=self._rng,
            observer=observer,
            owner=owner,
            readiness_generation=self._current_readiness_generation,
            scan_callback=self._scan_ready,
            ack_callback=self._on_delivery_acknowledged,
            interrupt_callback=self._on_delivery_user_interrupted,
            lease_seconds=lease_seconds,
            scan_interval=scan_interval,
        )

    async def start(self) -> None:
        await self._rebuild_readiness_generations()
        await self._rebuild_pending_proposals()
        await self._dispatcher.start()

    async def _rebuild_pending_proposals(self) -> None:
        """Restore pending proposals needed for approval after cold resume.

        Retaining the original proposer also preserves distinct-actor checks for
        two-party verification.
        """
        self._pending_proposals = {
            proposal.proposal_id: proposal
            for proposal in await self._repo.list_proposals()
        }

    async def _on_delivery_acknowledged(self, drive_id: str) -> None:
        """Re-evaluate a settled drive immediately after acknowledgement.

        Re-arming registrations can continue without waiting for the periodic
        readiness scan.
        """
        record = await self._repo.get(drive_id)
        if record is not None:
            await self._reevaluate_readiness(record)

    async def stop(self) -> None:
        await self._dispatcher.stop()

    @property
    def repository(self) -> Any:
        return self._repo

    @property
    def snapshot(self) -> EnabledRegistrySnapshot | None:
        return self._snapshot

    @property
    def dispatcher(self) -> DriveDispatcher:
        return self._dispatcher

    def now(self) -> datetime:
        return self._clock()

    def set_snapshot(self, snapshot: EnabledRegistrySnapshot | None) -> None:
        """Publish registry changes for the dispatcher to observe on its next pass."""
        self._snapshot = snapshot

    async def get_drive(self, drive_id: str) -> DriveRecord | None:
        return await self._repo.get(drive_id)

    async def list_drives(self, query: DriveQuery) -> tuple[DriveRecord, ...]:
        return await self._repo.list_drives(query)

    async def get_assignment(self, drive_id: str) -> DriveAssignment | None:
        return await self._repo.get_assignment(drive_id)

    async def list_deliveries(self, drive_id: str) -> tuple[DriveDelivery, ...]:
        return await self._repo.list_deliveries(drive_id)

    async def list_progress(self, drive_id: str) -> tuple[DriveProgress, ...]:
        return await self._repo.list_progress(drive_id)

    def allowed_actions(
        self,
        actor: ActorRef,
        record: DriveRecord | None,
        assignment: DriveAssignment | None,
        *,
        is_privileged: bool = False,
        extra_grants: frozenset[DriveCapability] = frozenset(),
    ) -> tuple[str, ...]:
        return acl_allowed_actions(
            actor,
            record,
            assignment,
            self._snapshot,
            is_privileged=is_privileged,
            extra_grants=extra_grants,
        )

    async def create_drive(
        self,
        request: CreateDriveRequest,
        *,
        actor: ActorRef,
        graph_id: str,
        is_privileged: bool = False,
        operator: bool = False,
        initial_status: DriveStatus = DriveStatus.ACTIVE,
    ) -> DriveRecord:
        operation = self._create_operation(request, actor)
        authorize(
            operation,
            actor,
            None,
            None,
            self._snapshot,
            is_privileged=is_privileged,
            kind=request.kind,
            schema_version=request.schema_version,
            extra_grants=self._operator_grants(operator),
        )
        self._validate_payloads(
            spec=request.spec,
            presentation=request.presentation,
            metadata=request.metadata,
            policy_options=request.policy_options,
        )
        self._validate_spec(request.kind, request.spec)
        await self._check_create_backpressure(request, graph_id)
        record = await self._repo.create_drive(
            request,
            actor=actor,
            graph_id=graph_id,
            initial_status=initial_status,
            operator_grant=self._operator_grant_details(operator, operation.value),
        )
        self._emit(
            "drive_created",
            record.drive_id,
            {
                "kind": record.kind,
                "status": record.status.value,
                "revision": record.revision,
            },
        )
        await self._maybe_enqueue(record, "activated")
        return record

    async def update_drive(
        self,
        drive_id: str,
        patch: DrivePatch,
        *,
        expected_revision: int,
        actor: ActorRef,
        idempotency_key: str | None = None,
        is_privileged: bool = False,
    ) -> DriveRecord:
        current = await self._require(drive_id)
        assignment = await self._repo.get_assignment(drive_id)
        authorize(
            DriveOperation.UPDATE,
            actor,
            current,
            assignment,
            self._snapshot,
            is_privileged=is_privileged,
        )
        changes = patch.changes()
        self._validate_payloads(
            spec=changes.get("spec"),
            presentation=changes.get("presentation"),
            metadata=changes.get("metadata"),
            policy_options=changes.get("policy_options"),
        )
        if "spec" in changes:
            self._validate_spec(current.kind, changes["spec"])
        record = await self._repo.update_drive(
            drive_id,
            patch,
            expected_revision=expected_revision,
            actor=actor,
            idempotency_key=idempotency_key,
        )
        self._emit("drive_updated", drive_id, {"revision": record.revision})
        return record

    async def assign(
        self,
        drive_id: str,
        assignee_creature_id: str,
        assignee_graph_id: str,
        *,
        expected_revision: int,
        actor: ActorRef,
        idempotency_key: str | None = None,
        is_privileged: bool = False,
        operator: bool = False,
    ) -> DriveRecord:
        current = await self._require(drive_id)
        assignment = await self._repo.get_assignment(drive_id)
        authorize(
            DriveOperation.ASSIGN,
            actor,
            current,
            assignment,
            self._snapshot,
            is_privileged=is_privileged,
            extra_grants=self._operator_grants(operator),
        )
        record = await self._repo.assign_drive(
            drive_id,
            assignee_creature_id=assignee_creature_id,
            assignee_graph_id=assignee_graph_id,
            expected_revision=expected_revision,
            actor=actor,
            idempotency_key=idempotency_key,
            operator_grant=self._operator_grant_details(operator, "assign"),
        )
        self._emit(
            "drive_assigned",
            drive_id,
            {
                "assignee": assignee_creature_id,
                "revision": record.revision,
                "epoch": record.lifecycle_epoch,
            },
        )
        await self._maybe_enqueue(record, "reassigned")
        return record

    async def reassign(
        self,
        drive_id: str,
        assignee_creature_id: str,
        assignee_graph_id: str,
        *,
        expected_revision: int,
        actor: ActorRef,
        idempotency_key: str | None = None,
        is_privileged: bool = False,
        operator: bool = False,
    ) -> DriveRecord:
        """Reassign to a creature, fencing all deliveries from the prior epoch."""
        return await self.assign(
            drive_id,
            assignee_creature_id,
            assignee_graph_id,
            expected_revision=expected_revision,
            actor=actor,
            idempotency_key=idempotency_key,
            is_privileged=is_privileged,
            operator=operator,
        )

    async def unassign(
        self,
        drive_id: str,
        *,
        expected_revision: int,
        actor: ActorRef,
        idempotency_key: str | None = None,
        is_privileged: bool = False,
    ) -> DriveRecord:
        current = await self._require(drive_id)
        assignment = await self._repo.get_assignment(drive_id)
        authorize(
            DriveOperation.UNASSIGN,
            actor,
            current,
            assignment,
            self._snapshot,
            is_privileged=is_privileged,
        )
        record = await self._repo.unassign_drive(
            drive_id,
            expected_revision=expected_revision,
            actor=actor,
            idempotency_key=idempotency_key,
        )
        self._emit("drive_unassigned", drive_id, {"revision": record.revision})
        return record

    async def transition(
        self,
        drive_id: str,
        target_status: DriveStatus,
        *,
        expected_revision: int,
        actor: ActorRef,
        status_reason: str | None = None,
        idempotency_key: str | None = None,
        is_privileged: bool = False,
    ) -> DriveRecord:
        if target_status in _TRANSITION_FORBIDDEN_TARGETS:
            raise DriveValidationError(
                f"transition to {target_status.value!r} is not allowed here; use "
                "propose_transition (completion/failure) or retire_drive"
            )
        current = await self._require(drive_id)
        assignment = await self._repo.get_assignment(drive_id)
        authorize(
            DriveOperation.TRANSITION,
            actor,
            current,
            assignment,
            self._snapshot,
            is_privileged=is_privileged,
            target_status=target_status,
        )
        self._validate_registration_transition(
            current, target_status, {"operation": "transition"}
        )
        record = await self._repo.transition_drive(
            drive_id,
            target_status,
            expected_revision=expected_revision,
            actor=actor,
            status_reason=status_reason,
            extra_transitions=self._registration_extra_transitions(current.kind),
            idempotency_key=idempotency_key,
            operation="transition",
        )
        self._emit(
            "drive_status_changed",
            drive_id,
            {"status": record.status.value, "revision": record.revision},
        )
        if record.status is DriveStatus.ACTIVE:
            # A resumed drive whose generation already settled needs a fresh
            # logical key, or dispatch supersedes the resume as a duplicate.
            await self._ensure_wake_generation(record)
            await self._maybe_enqueue(record, "ready")
        return record

    async def wake_drive(
        self,
        drive_id: str,
        *,
        actor: ActorRef,
        expected_revision: int | None = None,
        is_privileged: bool = False,
    ) -> DriveRecord:
        """Wake a waiting drive or re-enqueue an active drive with no live work.

        Manual wake is an authorized readiness override.
        """
        current = await self._require(drive_id)
        assignment = await self._repo.get_assignment(drive_id)
        authorize(
            DriveOperation.TRANSITION,
            actor,
            current,
            assignment,
            self._snapshot,
            is_privileged=is_privileged,
            target_status=DriveStatus.ACTIVE,
        )
        record = current
        if current.status is DriveStatus.WAITING:
            revision = (
                expected_revision if expected_revision is not None else current.revision
            )
            record = await self._repo.transition_drive(
                drive_id,
                DriveStatus.ACTIVE,
                expected_revision=revision,
                actor=actor,
                operation="wake",
            )
            self._emit(
                "drive_status_changed",
                drive_id,
                {"status": record.status.value, "revision": record.revision},
            )
        await self._ensure_wake_generation(record)
        await self._maybe_enqueue(record, "manual_wake", manual=True)
        return record

    async def _ensure_wake_generation(self, record: DriveRecord) -> None:
        """Advance generation when manual wake follows acknowledged work.

        Reusing the acknowledged generation would duplicate its logical key and
        cause dispatch to supersede the new delivery. In-flight work still blocks
        admission and therefore must not skip a generation.
        """
        gen = self._current_readiness_generation(record.drive_id)
        for delivery in await self._repo.list_deliveries(record.drive_id):
            if (
                delivery.readiness_generation == gen
                and delivery.lifecycle_epoch == record.lifecycle_epoch
                and delivery.state == "acknowledged"
            ):
                self._readiness_gen[record.drive_id] = gen + 1
                return

    async def retire_drive(
        self,
        drive_id: str,
        *,
        expected_revision: int,
        actor: ActorRef,
        idempotency_key: str | None = None,
        is_privileged: bool = False,
    ) -> DriveRecord:
        current = await self._require(drive_id)
        assignment = await self._repo.get_assignment(drive_id)
        authorize(
            DriveOperation.RETIRE,
            actor,
            current,
            assignment,
            self._snapshot,
            is_privileged=is_privileged,
        )
        record = await self._repo.retire_drive(
            drive_id,
            expected_revision=expected_revision,
            actor=actor,
            idempotency_key=idempotency_key,
        )
        self._emit("drive_retired", drive_id, {"revision": record.revision})
        return record

    async def report_progress(
        self,
        drive_id: str,
        *,
        summary: str,
        evidence: dict[str, Any] | None,
        actor: ActorRef,
        idempotency_key: str | None = None,
        is_privileged: bool = False,
    ) -> DriveProgress:
        current = await self._require(drive_id)
        assignment = await self._repo.get_assignment(drive_id)
        authorize(
            DriveOperation.REPORT_PROGRESS,
            actor,
            current,
            assignment,
            self._snapshot,
            is_privileged=is_privileged,
        )
        self._check_evidence_size(evidence)
        progress = await self._repo.report_progress(
            drive_id,
            summary=summary,
            evidence=evidence,
            actor=actor,
            idempotency_key=idempotency_key,
        )
        self._emit("drive_progress", drive_id, {"progress_id": progress.progress_id})
        return progress

    async def on_creature_stopped(self, creature_id: str) -> None:
        await lifecycle.on_creature_stopped(self, creature_id)

    async def on_creature_removed(
        self, creature_id: str, **kwargs: Any
    ) -> tuple[str, ...]:
        return await lifecycle.on_creature_removed(self, creature_id, **kwargs)

    async def on_creature_restoration_ready(self, creature_id: str) -> None:
        await lifecycle.on_creature_restoration_ready(self, creature_id)

    @staticmethod
    def _operator_grants(operator: bool) -> frozenset[DriveCapability]:
        """Return explicit grants supplied by a trusted operator context.

        Elevation is caller-provided and audited; drive payload fields can never
        request these capabilities.
        """
        return OPERATOR_GRANTS if operator else frozenset()

    @staticmethod
    def _operator_grant_details(
        operator: bool, operation: str
    ) -> dict[str, Any] | None:
        """Build audit details for an operator-elevated mutation.

        The repository commits these details in the same transaction as the
        elevated change so authorization evidence cannot be omitted.
        """
        if not operator:
            return None
        return {
            "operation": operation,
            "grants": sorted(g.value for g in OPERATOR_GRANTS),
        }

    def _emit(self, kind: str, drive_id: str | None, payload: dict[str, Any]) -> None:
        emit_observation(self._observer, kind, drive_id, payload)

    async def _require(self, drive_id: str) -> DriveRecord:
        record = await self._repo.get(drive_id)
        if record is None:
            raise DriveNotFoundError(f"no Drive {drive_id!r}")
        return record
