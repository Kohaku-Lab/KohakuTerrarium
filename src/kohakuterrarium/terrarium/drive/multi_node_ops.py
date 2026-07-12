"""Routed Drive methods for :class:`MultiNodeTerrariumService` (design §9.2, §10).

The host process runs no agents; every Drive lives on the worker that owns its
graph's repository (its **home**). This mixin enforces the single-canonical-writer
rule (design §10.1):

- per-Drive **writes** route only to the Drive's home, repairing a stale route
  once on ``not_found``;
- **reads** either target a known graph's home or fan out and dedupe by Drive id;
- **cross-node assignment** (a Drive homed on node A assigned to a creature on
  node B) is refused with :class:`CrossNodeDriveNotSupportedError` in v1, because
  the same-writer prerequisite only holds when assignee and home colocate;
- runtime status / registration reads aggregate truthfully across workers.

It relies on the host service exposing ``_remotes``, ``service_for``,
``_resolve_graph_home``, and a :class:`~terrarium.drive.multi_node.DriveRouteCache`
at ``_drive_routes``.
"""

from typing import Any

from kohakuterrarium.terrarium.drive.errors import (
    CrossNodeDriveNotSupportedError,
    DriveNotFoundError,
)
from kohakuterrarium.terrarium.drive.models import (
    ActorRef,
    DriveDelivery,
    DriveProgress,
    DriveStatus,
)
from kohakuterrarium.terrarium.drive.multi_node import (
    ROUTE_ACTOR,
    DriveHomeUnavailableError,
    fanout_list_views,
    resolve_delivery_home,
    resolve_drive_home,
    resolve_proposal_home,
    route_drive_write,
)
from kohakuterrarium.terrarium.drive.requests import CreateDriveRequest, DrivePatch
from kohakuterrarium.terrarium.drive.wire_service import (
    DriveRuntimeStatus,
    DriveView,
)


class MultiNodeDriveServiceMixin:
    """Home-routing Drive surface for the lab-host composite service."""

    # -- reads ---------------------------------------------------------------

    async def get_drive(
        self, drive_id: str, *, actor: ActorRef, is_privileged: bool = False
    ) -> DriveView | None:
        try:
            node_id = await resolve_drive_home(
                self, drive_id, actor=actor, use_cache=False
            )
        except DriveNotFoundError:
            return None
        return await self.service_for(node_id).get_drive(
            drive_id, actor=actor, is_privileged=is_privileged
        )

    async def list_drives(
        self,
        *,
        actor: ActorRef,
        graph_id: str | None = None,
        statuses: "frozenset[DriveStatus] | None" = None,
        kinds: frozenset[str] | None = None,
        assignee_creature_id: str | None = None,
        owner: ActorRef | None = None,
        include_terminal: bool = True,
        is_privileged: bool = False,
    ) -> tuple[DriveView, ...]:
        def call(svc: Any) -> Any:
            return svc.list_drives(
                actor=actor,
                graph_id=graph_id,
                statuses=statuses,
                kinds=kinds,
                assignee_creature_id=assignee_creature_id,
                owner=owner,
                include_terminal=include_terminal,
                is_privileged=is_privileged,
            )

        if graph_id is not None:
            try:
                node_id = await self._resolve_graph_home(graph_id)
            except KeyError:
                return ()
            return await self.service_for(node_id).list_drives(
                actor=actor,
                graph_id=graph_id,
                statuses=statuses,
                kinds=kinds,
                assignee_creature_id=assignee_creature_id,
                owner=owner,
                include_terminal=include_terminal,
                is_privileged=is_privileged,
            )
        return await fanout_list_views(self, call)

    async def assert_drive_in_graph(
        self,
        drive_id: str,
        graph_id: str,
        *,
        actor: ActorRef,
        is_privileged: bool = False,
    ) -> None:
        try:
            node_id = await self._resolve_graph_home(graph_id)
        except KeyError as exc:
            raise DriveNotFoundError(
                f"no Drive {drive_id!r} in graph {graph_id!r}"
            ) from exc
        await self.service_for(node_id).assert_drive_in_graph(
            drive_id, graph_id, actor=actor, is_privileged=is_privileged
        )

    async def list_drive_progress(
        self,
        drive_id: str,
        *,
        actor: ActorRef | None = None,
        is_privileged: bool = False,
    ) -> tuple[DriveProgress, ...]:
        node_id = await resolve_drive_home(self, drive_id, actor=actor or ROUTE_ACTOR)
        return await self.service_for(node_id).list_drive_progress(
            drive_id, actor=actor, is_privileged=is_privileged
        )

    async def list_drive_deliveries(
        self,
        drive_id: str,
        *,
        actor: ActorRef | None = None,
        is_privileged: bool = False,
    ) -> tuple[DriveDelivery, ...]:
        node_id = await resolve_drive_home(self, drive_id, actor=actor or ROUTE_ACTOR)
        return await self.service_for(node_id).list_drive_deliveries(
            drive_id, actor=actor, is_privileged=is_privileged
        )

    async def list_drive_audit(
        self,
        drive_id: str,
        *,
        actor: ActorRef | None = None,
        is_privileged: bool = False,
    ) -> tuple[Any, ...]:
        node_id = await resolve_drive_home(self, drive_id, actor=actor or ROUTE_ACTOR)
        return await self.service_for(node_id).list_drive_audit(
            drive_id, actor=actor, is_privileged=is_privileged
        )

    async def drive_runtime_status(self) -> DriveRuntimeStatus:
        """Aggregate every worker's running Drive status (truthful cluster view)."""
        enabled = False
        durability: str | None = None
        counts: dict[str, int] = {}
        registrations: list[dict[str, Any]] = []
        seen_regs: set[str] = set()
        for node_id, svc in list(self._remotes.items()):
            try:
                status = await svc.drive_runtime_status()
            except Exception:
                continue
            enabled = enabled or status.enabled
            durability = durability or status.durability
            for key, value in status.counts.items():
                counts[key] = counts.get(key, 0) + value
            for reg in status.registrations:
                name = reg.get("name")
                if name not in seen_regs:
                    seen_regs.add(name)
                    registrations.append(dict(reg))
        # A cluster has no single running revision; per-node revisions are read
        # through the node-targeted settings surface.
        return DriveRuntimeStatus(
            enabled=enabled,
            durability=durability,
            registrations=tuple(registrations),
            counts=counts,
            running_revision=None,
        )

    async def list_drive_registrations(self) -> tuple[dict[str, Any], ...]:
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for node_id, svc in list(self._remotes.items()):
            try:
                regs = await svc.list_drive_registrations()
            except Exception:
                continue
            for reg in regs:
                name = reg.get("name")
                if name not in seen:
                    seen.add(name)
                    merged.append(dict(reg))
        return tuple(merged)

    # -- writes --------------------------------------------------------------

    async def create_drive(
        self,
        request: CreateDriveRequest,
        *,
        graph_id: str,
        actor: ActorRef,
        is_privileged: bool = False,
        operator: bool = False,
    ) -> DriveView:
        node_id = await self._resolve_graph_home(graph_id)
        return await self.service_for(node_id).create_drive(
            request,
            graph_id=graph_id,
            actor=actor,
            is_privileged=is_privileged,
            operator=operator,
        )

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
        return await route_drive_write(
            self,
            drive_id,
            lambda svc: svc.update_drive(
                drive_id,
                patch,
                expected_revision=expected_revision,
                actor=actor,
                idempotency_key=idempotency_key,
                is_privileged=is_privileged,
            ),
            actor=actor,
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
        home = await resolve_drive_home(self, drive_id, actor=actor, use_cache=False)
        try:
            assignee_home = await self._resolve_graph_home(assignee_graph_id)
        except KeyError:
            # Unknown assignee graph — let the home worker reject it in-scope
            # rather than mislabel it a cross-node case.
            assignee_home = home
        if assignee_home != home:
            raise CrossNodeDriveNotSupportedError(
                f"Drive {drive_id!r} is homed on {home!r} but assignee graph "
                f"{assignee_graph_id!r} lives on {assignee_home!r}; cross-node Drive "
                "assignment is not supported in v1 (design §10.1)"
            )
        return await self.service_for(home).assign_drive(
            drive_id,
            assignee_creature_id,
            assignee_graph_id,
            expected_revision=expected_revision,
            actor=actor,
            idempotency_key=idempotency_key,
            is_privileged=is_privileged,
            operator=operator,
        )

    async def unassign_drive(
        self,
        drive_id: str,
        *,
        expected_revision: int,
        actor: ActorRef,
        idempotency_key: str | None = None,
        is_privileged: bool = False,
    ) -> DriveView:
        return await route_drive_write(
            self,
            drive_id,
            lambda svc: svc.unassign_drive(
                drive_id,
                expected_revision=expected_revision,
                actor=actor,
                idempotency_key=idempotency_key,
                is_privileged=is_privileged,
            ),
            actor=actor,
        )

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
        return await route_drive_write(
            self,
            drive_id,
            lambda svc: svc.transfer_drive_owner(
                drive_id,
                new_owner,
                expected_revision=expected_revision,
                actor=actor,
                idempotency_key=idempotency_key,
                is_privileged=is_privileged,
            ),
            actor=actor,
        )

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
        return await route_drive_write(
            self,
            drive_id,
            lambda svc: svc.transition_drive(
                drive_id,
                target_status,
                expected_revision=expected_revision,
                actor=actor,
                status_reason=status_reason,
                idempotency_key=idempotency_key,
                is_privileged=is_privileged,
            ),
            actor=actor,
        )

    async def wake_drive(
        self,
        drive_id: str,
        *,
        actor: ActorRef,
        expected_revision: int | None = None,
        is_privileged: bool = False,
    ) -> DriveView:
        return await route_drive_write(
            self,
            drive_id,
            lambda svc: svc.wake_drive(
                drive_id,
                actor=actor,
                expected_revision=expected_revision,
                is_privileged=is_privileged,
            ),
            actor=actor,
        )

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
        return await route_drive_write(
            self,
            drive_id,
            lambda svc: svc.report_drive_progress(
                drive_id,
                summary=summary,
                evidence=evidence,
                actor=actor,
                idempotency_key=idempotency_key,
                is_privileged=is_privileged,
            ),
            actor=actor,
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
        node_id = await resolve_drive_home(self, drive_id, actor=actor, use_cache=False)
        result = await self.service_for(node_id).propose_drive_transition(
            drive_id,
            target_status,
            actor=actor,
            evidence=evidence,
            reason=reason,
            expected_revision=expected_revision,
            is_privileged=is_privileged,
        )
        # This is only a hint. Approval always performs a fresh complete proposal
        # probe, because another worker can acquire the same claim without a join.
        if isinstance(result, dict) and result.get("proposal_id"):
            self._drive_routes.bind_home(
                "proposal",
                result["proposal_id"],
                node_id,
                generation=getattr(self, "_membership_epoch", 0),
            )
        return result

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
        # Locate read-only on every worker before granting mutation authority.
        # The worker still binds Drive/graph to the proposal before finalizing.
        node_id = await resolve_proposal_home(self, proposal_id)
        return await self.service_for(node_id).approve_drive_proposal(
            proposal_id,
            actor=actor,
            is_privileged=is_privileged,
            operator=operator,
            drive_id=drive_id,
            graph_id=graph_id,
        )

    async def retire_drive(
        self,
        drive_id: str,
        *,
        expected_revision: int,
        actor: ActorRef,
        idempotency_key: str | None = None,
        is_privileged: bool = False,
    ) -> DriveView:
        return await route_drive_write(
            self,
            drive_id,
            lambda svc: svc.retire_drive(
                drive_id,
                expected_revision=expected_revision,
                actor=actor,
                idempotency_key=idempotency_key,
                is_privileged=is_privileged,
            ),
            actor=actor,
        )

    async def replay_drive_delivery(
        self,
        delivery_id: str,
        *,
        actor: ActorRef,
        is_privileged: bool = False,
        graph_id: str | None = None,
    ) -> DriveDelivery:
        node_id = await self._resolve_delivery_home(delivery_id)
        return await self.service_for(node_id).replay_drive_delivery(
            delivery_id,
            actor=actor,
            is_privileged=is_privileged,
            graph_id=graph_id,
        )

    async def _resolve_delivery_home(self, delivery_id: str) -> str:
        """Locate the worker hosting ``delivery_id`` (admin replay carries no graph).

        Delegates to the shared uniqueness resolver: two workers claiming one
        delivery id is a quarantined integrity error, never a first-wins accept.
        """
        return await resolve_delivery_home(self, delivery_id)

    async def reconfigure_drive_runtime(
        self, registrations: Any, *, actor: ActorRef
    ) -> str:
        # Registration *instances* are not serializable; a worker is reconfigured
        # through its node-targeted ``studio.settings`` apply (design §8.6).
        raise CrossNodeDriveNotSupportedError(
            "reconfigure_drive_runtime with registration instances is not routable "
            "cross-node; apply per-node Drive settings via "
            "Studio.nodes[node].settings.drives.apply"
        )


__all__ = ["MultiNodeDriveServiceMixin", "DriveHomeUnavailableError"]
