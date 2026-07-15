"""Local, in-process Drive surface for ``LocalTerrariumService`` (design §9.2).

:class:`DriveServiceMixin` delegates to the engine's per-graph
:class:`DriveManager`\\ s, folding each record into a :class:`DriveView` (record +
assignment + derived availability + actor-scoped ``allowed_actions``). The DTO
contract (:class:`DriveServiceProtocol`) and the cross-node-unsupported stubs live
in :mod:`drive.service_protocol`.

The actor is always supplied by the trusted service caller (Studio / API derive
it from authenticated context, design §9.1); the manager re-checks ACL on every
mutation, so a bare tool/route registration is never authorization.
"""

from typing import Any

from kohakuterrarium.terrarium.drive.errors import (
    DriveDeliveryError,
    DriveError,
    DriveNotFoundError,
    DriveValidationError,
)
from kohakuterrarium.terrarium.drive.models import (
    ActorRef,
    DriveDelivery,
    DriveProgress,
    DriveRecord,
    DriveStatus,
)
from kohakuterrarium.terrarium.drive.requests import (
    CreateDriveRequest,
    DrivePatch,
    DriveQuery,
)
from kohakuterrarium.terrarium.drive.service_reads import DriveHistoryReadMixin
from kohakuterrarium.terrarium.drive.wire_service import (
    DriveRuntimeStatus,
    DriveView,
    registration_dtos,
    running_revision,
)


class DriveServiceMixin(DriveHistoryReadMixin):
    """Implement local Drive service operations over graph-scoped managers."""

    _engine: Any

    def _drive_runtime(self) -> Any:
        drives = getattr(self._engine, "drives", None)
        if drives is None:
            raise DriveError("the Drive runtime is not enabled on this terrarium")
        return drives

    def _all_managers(self, runtime: Any) -> list[Any]:
        return list(runtime.registry.all_managers())

    async def _find_manager(
        self, runtime: Any, drive_id: str
    ) -> tuple[Any, DriveRecord] | None:
        """Find the graph manager and record that own a Drive ID."""
        for manager in self._all_managers(runtime):
            record = await manager.get_drive(drive_id)
            if record is not None:
                return manager, record
        return None

    async def _require_manager(self, runtime: Any, drive_id: str) -> Any:
        found = await self._find_manager(runtime, drive_id)
        if found is None:
            raise DriveNotFoundError(f"no Drive {drive_id!r}")
        return found[0]

    async def _require_manager_in_graph(
        self, runtime: Any, drive_id: str, graph_id: str
    ) -> tuple[Any, DriveRecord]:
        """Resolve a Drive only when it belongs to the addressed graph."""
        found = await self._find_manager(runtime, drive_id)
        if found is None:
            raise DriveNotFoundError(f"no Drive {drive_id!r}")
        manager, record = found
        graph_manager = runtime.peek_manager(graph_id)
        if graph_manager is None or manager is not graph_manager:
            raise DriveNotFoundError(f"no Drive {drive_id!r} in graph {graph_id!r}")
        return manager, record

    async def assert_drive_in_graph(
        self,
        drive_id: str,
        graph_id: str,
        *,
        actor: ActorRef,
        is_privileged: bool = False,
    ) -> None:
        """Require a Drive to belong to the addressed graph without leaking others."""
        runtime = self._drive_runtime()
        await self._require_manager_in_graph(runtime, drive_id, graph_id)

    async def _proposal_for(self, runtime: Any, proposal_id: str) -> tuple[Any, Any]:
        """Find the manager and persisted pending proposal for a proposal ID."""
        for manager in self._all_managers(runtime):
            getter = getattr(manager, "get_proposal", None)
            if callable(getter):
                proposal = await getter(proposal_id)
            else:
                proposal = getattr(manager, "_pending_proposals", {}).get(proposal_id)
            if proposal is not None:
                return manager, proposal
        raise DriveValidationError(f"no pending proposal {proposal_id!r}")

    async def _manager_for_delivery(self, runtime: Any, delivery_id: str) -> Any:
        """Find the manager whose repository holds a delivery ID."""
        for manager in self._all_managers(runtime):
            async with manager.repository.transaction() as txn:
                if await txn.get_delivery(delivery_id) is not None:
                    return manager
        raise DriveDeliveryError(f"no delivery {delivery_id!r}")

    async def _drive_view(
        self,
        runtime: Any,
        manager: Any,
        record: DriveRecord,
        actor: ActorRef,
        is_privileged: bool,
    ) -> DriveView:
        assignment = await manager.get_assignment(record.drive_id)
        actions = manager.allowed_actions(
            actor, record, assignment, is_privileged=is_privileged
        )
        availability = runtime.snapshot.availability_for(record)
        return DriveView(
            record=record,
            assignee_creature_id=(
                assignment.assignee_creature_id if assignment is not None else None
            ),
            assignment_state=(
                assignment.assignment_state if assignment is not None else None
            ),
            availability=availability.value,
            durability=manager.repository.durability,
            allowed_actions=tuple(actions),
        )

    async def get_drive(
        self, drive_id: str, *, actor: ActorRef, is_privileged: bool = False
    ) -> DriveView | None:
        runtime = self._drive_runtime()
        found = await self._find_manager(runtime, drive_id)
        if found is None:
            return None
        manager, record = found
        return await self._drive_view(runtime, manager, record, actor, is_privileged)

    async def list_drives(
        self,
        *,
        actor: ActorRef,
        graph_id: str | None = None,
        statuses: frozenset[DriveStatus] | None = None,
        kinds: frozenset[str] | None = None,
        assignee_creature_id: str | None = None,
        owner: ActorRef | None = None,
        include_terminal: bool = True,
        is_privileged: bool = False,
    ) -> tuple[DriveView, ...]:
        runtime = self._drive_runtime()
        query = DriveQuery(
            graph_id=graph_id,
            statuses=statuses,
            kinds=kinds,
            assignee_creature_id=assignee_creature_id,
            owner=owner,
            include_terminal=include_terminal,
        )
        if graph_id is not None:
            peeked = runtime.peek_manager(graph_id)
            managers = [peeked] if peeked is not None else []
        else:
            managers = self._all_managers(runtime)
        views: list[DriveView] = []
        for manager in managers:
            for record in await manager.list_drives(query):
                views.append(
                    await self._drive_view(
                        runtime, manager, record, actor, is_privileged
                    )
                )
        return tuple(views)

    async def drive_runtime_status(self) -> DriveRuntimeStatus:
        drives = getattr(self._engine, "drives", None)
        if drives is None:
            return DriveRuntimeStatus(enabled=False)
        counts: dict[str, int] = {}
        for manager in self._all_managers(drives):
            for record in await manager.list_drives(DriveQuery(include_terminal=True)):
                counts[record.status.value] = counts.get(record.status.value, 0) + 1
        return DriveRuntimeStatus(
            enabled=True,
            durability=drives.durability,
            registrations=registration_dtos(drives),
            counts=counts,
            running_revision=running_revision(drives),
        )

    async def list_drive_registrations(self) -> tuple[dict[str, Any], ...]:
        drives = getattr(self._engine, "drives", None)
        if drives is None:
            return ()
        return registration_dtos(drives)

    async def create_drive(
        self,
        request: CreateDriveRequest,
        *,
        graph_id: str,
        actor: ActorRef,
        is_privileged: bool = False,
        operator: bool = False,
    ) -> DriveView:
        runtime = self._drive_runtime()
        manager = runtime.manager_for(graph_id)
        # Create-time assignment must satisfy the same live topology constraints
        # as a later explicit assignment.
        if request.assignee_creature_id is not None:
            self._require_creature_in_graph(request.assignee_creature_id, graph_id)
        record = await manager.create_drive(
            request,
            actor=actor,
            graph_id=graph_id,
            is_privileged=is_privileged,
            operator=operator,
        )
        return await self._drive_view(runtime, manager, record, actor, is_privileged)

    async def update_drive(
        self,
        drive_id: str,
        patch: DrivePatch,
        *,
        expected_revision: int,
        actor: ActorRef,
        idempotency_key: str | None = None,
        is_privileged: bool = False,
    ) -> DriveView:
        runtime = self._drive_runtime()
        manager = await self._require_manager(runtime, drive_id)
        record = await manager.update_drive(
            drive_id,
            patch,
            expected_revision=expected_revision,
            actor=actor,
            idempotency_key=idempotency_key,
            is_privileged=is_privileged,
        )
        return await self._drive_view(runtime, manager, record, actor, is_privileged)

    def _require_creature_in_graph(
        self, assignee_creature_id: str, graph_id: str
    ) -> None:
        """Require an assignee to exist in the specified live graph."""
        engine = self._engine
        if assignee_creature_id not in engine:
            raise DriveValidationError(
                f"assignee creature {assignee_creature_id!r} does not exist"
            )
        creature_graph = engine.get_creature(assignee_creature_id).graph_id
        if creature_graph != graph_id:
            raise DriveValidationError(
                f"creature {assignee_creature_id!r} is in graph {creature_graph!r}, "
                f"not the requested {graph_id!r}"
            )

    def _validate_assignment_target(
        self,
        runtime: Any,
        manager: Any,
        drive_id: str,
        assignee_creature_id: str,
        assignee_graph_id: str,
    ) -> None:
        """Validate an assignee and require the Drive's canonical graph."""
        self._require_creature_in_graph(assignee_creature_id, assignee_graph_id)
        graph_manager = runtime.peek_manager(assignee_graph_id)
        if graph_manager is None or manager is not graph_manager:
            raise DriveValidationError(
                f"cannot assign Drive {drive_id!r} to graph {assignee_graph_id!r}: "
                "the Drive lives in another graph (cross-graph assignment is "
                "not supported)"
            )

    async def assign_drive(
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
    ) -> DriveView:
        runtime = self._drive_runtime()
        manager = await self._require_manager(runtime, drive_id)
        self._validate_assignment_target(
            runtime, manager, drive_id, assignee_creature_id, assignee_graph_id
        )
        record = await manager.assign(
            drive_id,
            assignee_creature_id,
            assignee_graph_id,
            expected_revision=expected_revision,
            actor=actor,
            idempotency_key=idempotency_key,
            is_privileged=is_privileged,
            operator=operator,
        )
        return await self._drive_view(runtime, manager, record, actor, is_privileged)

    async def unassign_drive(
        self,
        drive_id: str,
        *,
        expected_revision: int,
        actor: ActorRef,
        idempotency_key: str | None = None,
        is_privileged: bool = False,
    ) -> DriveView:
        runtime = self._drive_runtime()
        manager = await self._require_manager(runtime, drive_id)
        record = await manager.unassign(
            drive_id,
            expected_revision=expected_revision,
            actor=actor,
            idempotency_key=idempotency_key,
            is_privileged=is_privileged,
        )
        return await self._drive_view(runtime, manager, record, actor, is_privileged)

    async def transfer_drive_owner(
        self,
        drive_id: str,
        new_owner: ActorRef,
        *,
        expected_revision: int,
        actor: ActorRef,
        idempotency_key: str | None = None,
        is_privileged: bool = False,
    ) -> DriveView:
        runtime = self._drive_runtime()
        manager = await self._require_manager(runtime, drive_id)
        record = await manager.transfer_owner(
            drive_id,
            new_owner,
            expected_revision=expected_revision,
            actor=actor,
            idempotency_key=idempotency_key,
            is_privileged=is_privileged,
        )
        return await self._drive_view(runtime, manager, record, actor, is_privileged)

    async def transition_drive(
        self,
        drive_id: str,
        target_status: DriveStatus,
        *,
        expected_revision: int,
        actor: ActorRef,
        status_reason: str | None = None,
        idempotency_key: str | None = None,
        is_privileged: bool = False,
    ) -> DriveView:
        runtime = self._drive_runtime()
        manager = await self._require_manager(runtime, drive_id)
        record = await manager.transition(
            drive_id,
            target_status,
            expected_revision=expected_revision,
            actor=actor,
            status_reason=status_reason,
            idempotency_key=idempotency_key,
            is_privileged=is_privileged,
        )
        return await self._drive_view(runtime, manager, record, actor, is_privileged)

    async def wake_drive(
        self,
        drive_id: str,
        *,
        actor: ActorRef,
        expected_revision: int | None = None,
        is_privileged: bool = False,
    ) -> DriveView:
        runtime = self._drive_runtime()
        manager = await self._require_manager(runtime, drive_id)
        record = await manager.wake_drive(
            drive_id,
            actor=actor,
            expected_revision=expected_revision,
            is_privileged=is_privileged,
        )
        return await self._drive_view(runtime, manager, record, actor, is_privileged)

    async def report_drive_progress(
        self,
        drive_id: str,
        *,
        summary: str,
        evidence: dict[str, Any] | None,
        actor: ActorRef,
        idempotency_key: str | None = None,
        is_privileged: bool = False,
    ) -> DriveProgress:
        runtime = self._drive_runtime()
        manager = await self._require_manager(runtime, drive_id)
        return await manager.report_progress(
            drive_id,
            summary=summary,
            evidence=evidence,
            actor=actor,
            idempotency_key=idempotency_key,
            is_privileged=is_privileged,
        )

    async def propose_drive_transition(
        self,
        drive_id: str,
        target_status: DriveStatus,
        *,
        actor: ActorRef,
        evidence: dict[str, Any] | None = None,
        reason: str | None = None,
        expected_revision: int | None = None,
        is_privileged: bool = False,
    ) -> DriveView | dict[str, Any]:
        runtime = self._drive_runtime()
        manager = await self._require_manager(runtime, drive_id)
        result = await manager.propose_transition(
            drive_id,
            target_status,
            actor=actor,
            evidence=evidence,
            reason=reason,
            expected_revision=expected_revision,
            is_privileged=is_privileged,
        )
        if isinstance(result, DriveRecord):
            return await self._drive_view(
                runtime, manager, result, actor, is_privileged
            )
        return {
            "proposal_id": result.proposal_id,
            "drive_id": drive_id,
            "target_status": target_status.value,
            "pending": True,
        }

    def _bind_proposal(
        self,
        runtime: Any,
        manager: Any,
        proposal: Any,
        drive_id: str | None,
        graph_id: str | None,
    ) -> None:
        """Bind a proposal to the addressed Drive and graph before approval."""
        if drive_id is not None and proposal.drive_id != drive_id:
            raise DriveNotFoundError(
                f"no proposal {proposal.proposal_id!r} for Drive {drive_id!r}"
            )
        if graph_id is not None:
            graph_manager = runtime.peek_manager(graph_id)
            if graph_manager is None or manager is not graph_manager:
                raise DriveNotFoundError(
                    f"no proposal {proposal.proposal_id!r} in graph {graph_id!r}"
                )

    async def approve_drive_proposal(
        self,
        proposal_id: str,
        *,
        actor: ActorRef,
        is_privileged: bool = False,
        operator: bool = False,
        drive_id: str | None = None,
        graph_id: str | None = None,
    ) -> DriveView:
        runtime = self._drive_runtime()
        manager, proposal = await self._proposal_for(runtime, proposal_id)
        self._bind_proposal(runtime, manager, proposal, drive_id, graph_id)
        record = await manager.approve_proposal(
            proposal_id, actor=actor, is_privileged=is_privileged, operator=operator
        )
        return await self._drive_view(runtime, manager, record, actor, is_privileged)

    async def retire_drive(
        self,
        drive_id: str,
        *,
        expected_revision: int,
        actor: ActorRef,
        idempotency_key: str | None = None,
        is_privileged: bool = False,
    ) -> DriveView:
        runtime = self._drive_runtime()
        manager = await self._require_manager(runtime, drive_id)
        record = await manager.retire_drive(
            drive_id,
            expected_revision=expected_revision,
            actor=actor,
            idempotency_key=idempotency_key,
            is_privileged=is_privileged,
        )
        return await self._drive_view(runtime, manager, record, actor, is_privileged)

    async def replay_drive_delivery(
        self,
        delivery_id: str,
        *,
        actor: ActorRef,
        is_privileged: bool = False,
        graph_id: str | None = None,
    ) -> DriveDelivery:
        runtime = self._drive_runtime()
        manager = await self._manager_for_delivery(runtime, delivery_id)
        if graph_id is not None:
            # A graph-scoped replay must not reveal or mutate another graph's
            # delivery.
            graph_manager = runtime.peek_manager(graph_id)
            if graph_manager is None or manager is not graph_manager:
                raise DriveDeliveryError(
                    f"no delivery {delivery_id!r} in graph {graph_id!r}"
                )
        return await manager.replay_dead_letter(
            delivery_id, actor=actor, is_privileged=is_privileged
        )

    async def reconfigure_drive_runtime(
        self, registrations: Any, *, actor: ActorRef
    ) -> str:
        # Registration instances are process-local; remote settings resolve
        # registration names on their target worker.
        return self._engine.reconfigure_drives(registrations)


__all__ = ["DriveServiceMixin"]
