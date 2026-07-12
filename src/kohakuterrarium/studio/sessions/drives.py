"""Studio session/graph-scoped Drive record façade (design §9.2, §12).

A *session* is a Terrarium graph, so ``session_id`` is the ``graph_id`` the
record operations pass to :class:`~kohakuterrarium.terrarium.service.TerrariumService`.
This module is the single Python surface the HTTP routes, ``kt drive`` CLI, and
generic ``/drives`` command all delegate to: it builds the typed request DTOs
from primitive input, calls the service, and folds the returned
:class:`DriveView` / progress / delivery records into JSON-safe dicts.

Two shapes are produced (design §12.1): a **row** (list view, spec/evidence
redacted) and a **detail** (full spec/presentation/metadata, returned only to a
caller authorized for the record). ``allowed_actions`` rides on both so a UI can
render permission-aware controls; the manager still re-checks every mutation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kohakuterrarium.studio.sessions.drives_serde import (
    audit_to_dict,
    build_create_request,
    build_patch,
    delivery_to_dict,
    detail,
    parse_actor,
    parse_kinds,
    parse_status,
    parse_statuses,
    progress_to_dict,
    row,
)
from kohakuterrarium.terrarium.drive.errors import DriveValidationError
from kohakuterrarium.terrarium.drive.models import ActorRef
from kohakuterrarium.terrarium.drive.wire_service import DriveView

if TYPE_CHECKING:
    # Type-only: importing the service module at runtime would pull the whole
    # engine and cycle back through the built-in command that consumes this
    # façade. The functions only use ``TerrariumService`` as an annotation.
    from kohakuterrarium.terrarium.service import TerrariumService


# ---------------------------------------------------------------------------
# record operations — thin over TerrariumService (design §9.2)
# ---------------------------------------------------------------------------


async def list_records(
    service: TerrariumService,
    *,
    graph_id: str,
    actor: ActorRef,
    is_privileged: bool = False,
    statuses: object = None,
    kinds: object = None,
    owner: object = None,
    assignee_creature_id: str | None = None,
    mine: bool = False,
    include_terminal: bool = True,
) -> list[dict[str, Any]]:
    """Redacted rows for a graph, filtered by status/kind/owner/assignee (§12.1)."""
    owner_ref = actor if mine else parse_actor(owner)
    views = await service.list_drives(
        actor=actor,
        graph_id=graph_id,
        statuses=parse_statuses(statuses),
        kinds=parse_kinds(kinds),
        assignee_creature_id=assignee_creature_id,
        owner=owner_ref,
        include_terminal=include_terminal,
        is_privileged=is_privileged,
    )
    return [row(v) for v in views]


async def _bind_graph(
    service: TerrariumService,
    drive_id: str,
    graph_id: str | None,
    *,
    actor: ActorRef,
    is_privileged: bool,
) -> None:
    """Enforce that ``drive_id`` lives in ``graph_id`` before an ID-addressed op.

    The single graph-scope gate every façade adapter (HTTP / CLI / command)
    inherits (R1-02). ``graph_id is None`` means the caller is not graph-scoped
    (a globally-ID-addressed programmatic caller) and skips the bind.
    """
    if graph_id is None:
        return
    await service.assert_drive_in_graph(
        drive_id, graph_id, actor=actor, is_privileged=is_privileged
    )


async def get_record(
    service: TerrariumService,
    drive_id: str,
    *,
    actor: ActorRef,
    is_privileged: bool = False,
    graph_id: str | None = None,
) -> dict[str, Any] | None:
    """The detail (or redacted row for an unauthorized caller); ``None`` if absent."""
    await _bind_graph(
        service, drive_id, graph_id, actor=actor, is_privileged=is_privileged
    )
    view = await service.get_drive(drive_id, actor=actor, is_privileged=is_privileged)
    if view is None:
        return None
    return detail(view, actor=actor, is_privileged=is_privileged)


async def create_record(
    service: TerrariumService,
    *,
    graph_id: str,
    actor: ActorRef,
    body: dict[str, Any],
    is_privileged: bool = False,
    operator: bool = False,
) -> dict[str, Any]:
    request = build_create_request(graph_id=graph_id, actor=actor, body=body)
    view = await service.create_drive(
        request,
        graph_id=graph_id,
        actor=actor,
        is_privileged=is_privileged,
        operator=operator,
    )
    return detail(view, actor=actor, is_privileged=is_privileged)


async def update_record(
    service: TerrariumService,
    drive_id: str,
    *,
    expected_revision: int,
    actor: ActorRef,
    body: dict[str, Any],
    idempotency_key: str | None = None,
    is_privileged: bool = False,
    graph_id: str | None = None,
) -> dict[str, Any]:
    await _bind_graph(
        service, drive_id, graph_id, actor=actor, is_privileged=is_privileged
    )
    view = await service.update_drive(
        drive_id,
        build_patch(body),
        expected_revision=expected_revision,
        actor=actor,
        idempotency_key=idempotency_key,
        is_privileged=is_privileged,
    )
    return detail(view, actor=actor, is_privileged=is_privileged)


async def assign_record(
    service: TerrariumService,
    drive_id: str,
    *,
    assignee_creature_id: str,
    assignee_graph_id: str,
    expected_revision: int,
    actor: ActorRef,
    idempotency_key: str | None = None,
    is_privileged: bool = False,
    operator: bool = False,
    graph_id: str | None = None,
) -> dict[str, Any]:
    await _bind_graph(
        service, drive_id, graph_id, actor=actor, is_privileged=is_privileged
    )
    view = await service.assign_drive(
        drive_id,
        assignee_creature_id,
        assignee_graph_id,
        expected_revision=expected_revision,
        actor=actor,
        idempotency_key=idempotency_key,
        is_privileged=is_privileged,
        operator=operator,
    )
    return detail(view, actor=actor, is_privileged=is_privileged)


async def unassign_record(
    service: TerrariumService,
    drive_id: str,
    *,
    expected_revision: int,
    actor: ActorRef,
    idempotency_key: str | None = None,
    is_privileged: bool = False,
    graph_id: str | None = None,
) -> dict[str, Any]:
    await _bind_graph(
        service, drive_id, graph_id, actor=actor, is_privileged=is_privileged
    )
    view = await service.unassign_drive(
        drive_id,
        expected_revision=expected_revision,
        actor=actor,
        idempotency_key=idempotency_key,
        is_privileged=is_privileged,
    )
    return detail(view, actor=actor, is_privileged=is_privileged)


async def propose_terminal_record(
    service: TerrariumService,
    drive_id: str,
    *,
    target_status: str,
    actor: ActorRef,
    evidence: dict[str, Any] | None = None,
    reason: str | None = None,
    expected_revision: int | None = None,
    is_privileged: bool = False,
    graph_id: str | None = None,
) -> dict[str, Any]:
    """Propose a terminal transition (design §4.2).

    Returns a detail dict when policy accepts immediately, or a small
    ``{proposal_id, drive_id, target_status, pending: True}`` envelope when a
    verifier must still approve.
    """
    await _bind_graph(
        service, drive_id, graph_id, actor=actor, is_privileged=is_privileged
    )
    result = await service.propose_drive_transition(
        drive_id,
        parse_status(target_status),
        actor=actor,
        evidence=evidence,
        reason=reason,
        expected_revision=expected_revision,
        is_privileged=is_privileged,
    )
    if isinstance(result, DriveView):
        return detail(result, actor=actor, is_privileged=is_privileged)
    return dict(result)


async def approve_proposal_record(
    service: TerrariumService,
    proposal_id: str,
    *,
    actor: ActorRef,
    is_privileged: bool = False,
    operator: bool = False,
    drive_id: str | None = None,
    graph_id: str | None = None,
) -> dict[str, Any]:
    # The service binds the proposal to the URL's Drive/graph before the terminal
    # write (R1-02): a proposal whose parent Drive is not the path Drive, or whose
    # Drive is not in the path graph, is rejected as not-found regardless of what
    # other Drive the caller can reach. Binding the *proposal's* parent (not just
    # the path Drive against the graph) is what closes the confused-deputy gap.
    view = await service.approve_drive_proposal(
        proposal_id,
        actor=actor,
        is_privileged=is_privileged,
        operator=operator,
        drive_id=drive_id,
        graph_id=graph_id,
    )
    return detail(view, actor=actor, is_privileged=is_privileged)


async def transfer_owner_record(
    service: TerrariumService,
    drive_id: str,
    *,
    new_owner: str,
    expected_revision: int,
    actor: ActorRef,
    idempotency_key: str | None = None,
    is_privileged: bool = False,
    graph_id: str | None = None,
) -> dict[str, Any]:
    owner_ref = parse_actor(new_owner)
    if owner_ref is None:
        raise DriveValidationError("new_owner is required")
    await _bind_graph(
        service, drive_id, graph_id, actor=actor, is_privileged=is_privileged
    )
    view = await service.transfer_drive_owner(
        drive_id,
        owner_ref,
        expected_revision=expected_revision,
        actor=actor,
        idempotency_key=idempotency_key,
        is_privileged=is_privileged,
    )
    return detail(view, actor=actor, is_privileged=is_privileged)


async def transition_record(
    service: TerrariumService,
    drive_id: str,
    *,
    target_status: str,
    expected_revision: int,
    actor: ActorRef,
    status_reason: str | None = None,
    idempotency_key: str | None = None,
    is_privileged: bool = False,
    graph_id: str | None = None,
) -> dict[str, Any]:
    await _bind_graph(
        service, drive_id, graph_id, actor=actor, is_privileged=is_privileged
    )
    view = await service.transition_drive(
        drive_id,
        parse_status(target_status),
        expected_revision=expected_revision,
        actor=actor,
        status_reason=status_reason,
        idempotency_key=idempotency_key,
        is_privileged=is_privileged,
    )
    return detail(view, actor=actor, is_privileged=is_privileged)


async def wake_record(
    service: TerrariumService,
    drive_id: str,
    *,
    actor: ActorRef,
    expected_revision: int | None = None,
    is_privileged: bool = False,
    graph_id: str | None = None,
) -> dict[str, Any]:
    await _bind_graph(
        service, drive_id, graph_id, actor=actor, is_privileged=is_privileged
    )
    view = await service.wake_drive(
        drive_id,
        actor=actor,
        expected_revision=expected_revision,
        is_privileged=is_privileged,
    )
    return detail(view, actor=actor, is_privileged=is_privileged)


async def report_progress_record(
    service: TerrariumService,
    drive_id: str,
    *,
    summary: str,
    evidence: dict[str, Any] | None,
    actor: ActorRef,
    idempotency_key: str | None = None,
    is_privileged: bool = False,
    graph_id: str | None = None,
) -> dict[str, Any]:
    await _bind_graph(
        service, drive_id, graph_id, actor=actor, is_privileged=is_privileged
    )
    progress = await service.report_drive_progress(
        drive_id,
        summary=summary,
        evidence=evidence,
        actor=actor,
        idempotency_key=idempotency_key,
        is_privileged=is_privileged,
    )
    return progress_to_dict(progress)


async def retire_record(
    service: TerrariumService,
    drive_id: str,
    *,
    expected_revision: int,
    actor: ActorRef,
    idempotency_key: str | None = None,
    is_privileged: bool = False,
    graph_id: str | None = None,
) -> dict[str, Any]:
    await _bind_graph(
        service, drive_id, graph_id, actor=actor, is_privileged=is_privileged
    )
    view = await service.retire_drive(
        drive_id,
        expected_revision=expected_revision,
        actor=actor,
        idempotency_key=idempotency_key,
        is_privileged=is_privileged,
    )
    return detail(view, actor=actor, is_privileged=is_privileged)


async def list_deliveries_records(
    service: TerrariumService,
    drive_id: str,
    *,
    actor: ActorRef,
    is_privileged: bool = False,
    graph_id: str | None = None,
) -> list[dict[str, Any]]:
    await _bind_graph(
        service, drive_id, graph_id, actor=actor, is_privileged=is_privileged
    )
    deliveries = await service.list_drive_deliveries(
        drive_id, actor=actor, is_privileged=is_privileged
    )
    return [delivery_to_dict(d) for d in deliveries]


async def list_progress_records(
    service: TerrariumService,
    drive_id: str,
    *,
    actor: ActorRef,
    is_privileged: bool = False,
    graph_id: str | None = None,
) -> list[dict[str, Any]]:
    await _bind_graph(
        service, drive_id, graph_id, actor=actor, is_privileged=is_privileged
    )
    entries = await service.list_drive_progress(
        drive_id, actor=actor, is_privileged=is_privileged
    )
    return [progress_to_dict(p) for p in entries]


async def list_audit_records(
    service: TerrariumService,
    drive_id: str,
    *,
    actor: ActorRef,
    is_privileged: bool = False,
    graph_id: str | None = None,
) -> list[dict[str, Any]]:
    """Canonical-mutation audit rows for a Drive (graph-bound + ACL-gated, §7.3)."""
    await _bind_graph(
        service, drive_id, graph_id, actor=actor, is_privileged=is_privileged
    )
    entries = await service.list_drive_audit(
        drive_id, actor=actor, is_privileged=is_privileged
    )
    return [audit_to_dict(a) for a in entries]


async def replay_delivery_record(
    service: TerrariumService,
    delivery_id: str,
    *,
    actor: ActorRef,
    is_privileged: bool = False,
    graph_id: str | None = None,
) -> dict[str, Any]:
    delivery = await service.replay_drive_delivery(
        delivery_id,
        actor=actor,
        is_privileged=is_privileged,
        graph_id=graph_id,
    )
    return delivery_to_dict(delivery)


__all__ = [
    "audit_to_dict",
    "build_create_request",
    "build_patch",
    "create_record",
    "delivery_to_dict",
    "detail",
    "get_record",
    "list_audit_records",
    "list_deliveries_records",
    "list_progress_records",
    "list_records",
    "parse_actor",
    "parse_kinds",
    "parse_status",
    "parse_statuses",
    "progress_to_dict",
    "approve_proposal_record",
    "assign_record",
    "propose_terminal_record",
    "replay_delivery_record",
    "report_progress_record",
    "retire_record",
    "row",
    "transfer_owner_record",
    "transition_record",
    "unassign_record",
    "update_record",
    "wake_record",
]
