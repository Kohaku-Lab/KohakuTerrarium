"""The five self-service Drive tools injected into Drive-enabled creatures.

``drive_create`` / ``drive_status`` / ``drive_update`` / ``drive_report`` /
``drive_transition`` are thin wrappers over the engine's :class:`DriveManager`
(design §9.3). Each derives its :class:`ActorRef` as ``creature:<caller_id>`` from
the trusted :class:`ToolContext` — NEVER from tool arguments — and forces caller
ownership/scope on create. Tool presence is not authorization: the manager
re-checks owner/assignee/scope/registration on every call (rule §4.15), so these
are safe on non-privileged creatures. ``group_drive`` (privileged) is Phase G.

Conflicts (stale ``expected_revision``), permission denials, and disabled/unknown
registrations surface as distinct, model-shaped errors. Output is bounded JSON.
"""

import json
from typing import Any

from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolContext,
    ToolResult,
)
from kohakuterrarium.terrarium.channels import DRIVE_SERVICE_KEY
from kohakuterrarium.terrarium.drive.errors import (
    DriveConflictError,
    DriveError,
    DriveIdempotencyConflictError,
    DriveNotFoundError,
    DrivePermissionError,
    DriveRegistrationDisabledError,
    DriveRegistrationIncompatibleError,
    DriveRegistrationNotFoundError,
)
from kohakuterrarium.terrarium.drive.models import ActorRef, DriveRecord, DriveStatus
from kohakuterrarium.terrarium.drive.requests import (
    CreateDriveRequest,
    DrivePatch,
    DriveQuery,
)
from kohakuterrarium.terrarium.group_tool_context import (
    GroupToolError,
    resolve_group_context,
)

# Terminal targets go through the propose/verify pipeline; the rest are direct
# control transitions (design §4.2). RETIRED is admin-only, not exposed here.
_TERMINAL_TARGETS = frozenset({DriveStatus.COMPLETED, DriveStatus.FAILED})
_TRANSITION_TARGETS = frozenset(
    {
        DriveStatus.ACTIVE,
        DriveStatus.WAITING,
        DriveStatus.BLOCKED,
        DriveStatus.PAUSED,
        DriveStatus.CANCELLED,
        DriveStatus.DRAFT,
    }
)


class _DriveCall:
    """Resolved, trusted context for one Drive tool call."""

    __slots__ = (
        "manager",
        "runtime",
        "engine",
        "caller",
        "actor",
        "graph_id",
        "is_privileged",
    )

    def __init__(self, manager, runtime, engine, caller) -> None:
        self.manager = manager
        self.runtime = runtime
        self.engine = engine
        self.caller = caller
        self.actor = ActorRef("creature", caller.creature_id)
        self.graph_id = caller.graph_id
        self.is_privileged = bool(getattr(caller, "is_privileged", False))


def _resolve_call(ctx: ToolContext | None) -> _DriveCall:
    """Resolve the caller creature + engine + DriveManager, or raise
    :class:`GroupToolError` with a model-shaped message."""
    gctx = resolve_group_context(ctx, require_privileged=False)
    runtime = ctx.environment.get(DRIVE_SERVICE_KEY) if ctx.environment else None
    if runtime is None:
        raise GroupToolError("the Drive runtime is not enabled on this terrarium")
    manager = runtime.manager_for(gctx.caller.graph_id)
    if manager is None:
        raise GroupToolError("no Drive manager is available for this graph")
    return _DriveCall(manager, runtime, gctx.engine, gctx.caller)


def _err(message: str) -> ToolResult:
    return ToolResult(error=message)


def _ok(payload: dict[str, Any]) -> ToolResult:
    return ToolResult(output=json.dumps(payload, default=str), exit_code=0)


def _drive_error_result(exc: DriveError) -> ToolResult:
    """Map a typed Drive error to a distinct, model-shaped tool error."""
    if isinstance(exc, (DriveConflictError, DriveIdempotencyConflictError)):
        return _err(f"conflict: {exc}")
    if isinstance(exc, DrivePermissionError):
        return _err(f"permission denied: {exc}")
    if isinstance(
        exc,
        (
            DriveRegistrationDisabledError,
            DriveRegistrationIncompatibleError,
            DriveRegistrationNotFoundError,
        ),
    ):
        return _err(f"registration unavailable: {exc}")
    if isinstance(exc, DriveNotFoundError):
        return _err(f"not found: {exc}")
    return _err(f"invalid: {exc}")


def _record_summary(call: _DriveCall, record: DriveRecord) -> dict[str, Any]:
    """Bounded, authorized-view summary of a Drive record (no raw spec dump).

    The assignee is added by :func:`_summary_with_actions`, which resolves the
    live assignment."""
    return {
        "drive_id": record.drive_id,
        "kind": record.kind,
        "title": record.title,
        "status": record.status.value,
        "revision": record.revision,
        "scope_type": record.scope_type,
        "scope_id": record.scope_id,
        "owner": record.owner.format(),
        "priority": record.priority,
        # Per-record durability for THIS record's graph, not the mixed-engine
        # aggregate (R1-41): the manager is graph-scoped, so every record it
        # returns belongs to ``call.graph_id``.
        "durability": call.runtime.durability_for(call.graph_id),
    }


async def _summary_with_actions(
    call: _DriveCall, record: DriveRecord
) -> dict[str, Any]:
    assignment = await call.manager.get_assignment(record.drive_id)
    summary = _record_summary(call, record)
    summary["assignee"] = (
        assignment.assignee_creature_id if assignment is not None else None
    )
    summary["allowed_actions"] = list(
        call.manager.allowed_actions(
            call.actor, record, assignment, is_privileged=call.is_privileged
        )
    )
    return summary


class _BaseDriveTool(BaseTool):
    needs_context = True

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _resolve_or_error(
        self, ctx: ToolContext | None
    ) -> tuple[_DriveCall | None, ToolResult | None]:
        try:
            return _resolve_call(ctx), None
        except GroupToolError as exc:
            return None, _err(str(exc))


class DriveCreateTool(_BaseDriveTool):
    """Create a caller-owned, caller-scoped Drive of an enabled kind."""

    @property
    def tool_name(self) -> str:
        return "drive_create"

    @property
    def description(self) -> str:
        return "Create a durable drive you own (kind, title, spec)"

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
                "title": {"type": "string"},
                "spec": {"type": "object"},
                "priority": {"type": "integer"},
                "idempotency_key": {"type": "string"},
            },
            "required": ["title"],
        }

    async def _execute(
        self, args: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        call, err = await self._resolve_or_error(context)
        if err is not None:
            return err
        title = (args.get("title") or "").strip()
        if not title:
            return _err("'title' is required")
        kind = (args.get("kind") or "generic").strip()
        spec = args.get("spec") or {}
        if not isinstance(spec, dict):
            return _err("'spec' must be an object")
        priority = args.get("priority", 0)
        if not isinstance(priority, int) or isinstance(priority, bool):
            return _err("'priority' must be an integer")
        # Force caller ownership/scope/assignment — never trust actor/owner/scope
        # from tool arguments (design §9.3, rule §4.15).
        request = CreateDriveRequest(
            kind=kind,
            title=title,
            scope_type="creature",
            scope_id=call.caller.creature_id,
            owner=call.actor,
            owner_scope="creature",
            created_by=call.actor,
            spec=spec,
            priority=priority,
            assignee_creature_id=call.caller.creature_id,
            idempotency_key=(args.get("idempotency_key") or None),
        )
        try:
            record = await call.manager.create_drive(
                request,
                actor=call.actor,
                graph_id=call.graph_id,
                is_privileged=call.is_privileged,
            )
        except DriveError as exc:
            return _drive_error_result(exc)
        return _ok(await _summary_with_actions(call, record))


class DriveStatusTool(_BaseDriveTool):
    """List Drives the caller owns / is assigned, or get one by id."""

    @property
    def tool_name(self) -> str:
        return "drive_status"

    @property
    def description(self) -> str:
        return "List your drives, or get one by drive_id"

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {"drive_id": {"type": "string"}},
        }

    async def _execute(
        self, args: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        call, err = await self._resolve_or_error(context)
        if err is not None:
            return err
        drive_id = (args.get("drive_id") or "").strip()
        if drive_id:
            record = await call.manager.get_drive(drive_id)
            if record is None:
                return _err(f"not found: no drive {drive_id!r}")
            return _ok(await _summary_with_actions(call, record))
        owned = await call.manager.list_drives(DriveQuery(owner=call.actor))
        assigned = await call.manager.list_drives(
            DriveQuery(assignee_creature_id=call.caller.creature_id)
        )
        seen: dict[str, DriveRecord] = {}
        for record in (*owned, *assigned):
            seen[record.drive_id] = record
        summaries = [await _summary_with_actions(call, r) for r in seen.values()]
        return _ok({"drives": summaries, "count": len(summaries)})


class DriveUpdateTool(_BaseDriveTool):
    """Update a caller-owned Drive under optimistic concurrency."""

    @property
    def tool_name(self) -> str:
        return "drive_update"

    @property
    def description(self) -> str:
        return "Update a drive you own (needs expected_revision)"

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "drive_id": {"type": "string"},
                "expected_revision": {"type": "integer"},
                "title": {"type": "string"},
                "spec": {"type": "object"},
                "priority": {"type": "integer"},
                "idempotency_key": {"type": "string"},
            },
            "required": ["drive_id", "expected_revision"],
        }

    async def _execute(
        self, args: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        call, err = await self._resolve_or_error(context)
        if err is not None:
            return err
        drive_id = (args.get("drive_id") or "").strip()
        expected_revision = args.get("expected_revision")
        if (
            not drive_id
            or not isinstance(expected_revision, int)
            or isinstance(expected_revision, bool)
        ):
            return _err("'drive_id' and integer 'expected_revision' are required")
        patch_kwargs: dict[str, Any] = {}
        if "title" in args:
            patch_kwargs["title"] = args["title"]
        if "spec" in args:
            patch_kwargs["spec"] = args["spec"]
        if "priority" in args:
            patch_kwargs["priority"] = args["priority"]
        try:
            patch = DrivePatch(**patch_kwargs)
        except DriveError as exc:
            return _drive_error_result(exc)
        if patch.is_empty():
            return _err("no updatable fields supplied (title / spec / priority)")
        try:
            record = await call.manager.update_drive(
                drive_id,
                patch,
                expected_revision=expected_revision,
                actor=call.actor,
                idempotency_key=(args.get("idempotency_key") or None),
                is_privileged=call.is_privileged,
            )
        except DriveError as exc:
            return _drive_error_result(exc)
        return _ok(await _summary_with_actions(call, record))


class DriveReportTool(_BaseDriveTool):
    """Append progress/evidence to an owned or assigned Drive."""

    @property
    def tool_name(self) -> str:
        return "drive_report"

    @property
    def description(self) -> str:
        return "Report progress/evidence on a drive you own or are assigned"

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "drive_id": {"type": "string"},
                "summary": {"type": "string"},
                "evidence": {"type": "object"},
                "idempotency_key": {"type": "string"},
            },
            "required": ["drive_id", "summary"],
        }

    async def _execute(
        self, args: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        call, err = await self._resolve_or_error(context)
        if err is not None:
            return err
        drive_id = (args.get("drive_id") or "").strip()
        summary = args.get("summary")
        if not drive_id or not isinstance(summary, str):
            return _err("'drive_id' and string 'summary' are required")
        evidence = args.get("evidence")
        if evidence is not None and not isinstance(evidence, dict):
            return _err("'evidence' must be an object")
        try:
            progress = await call.manager.report_progress(
                drive_id,
                summary=summary,
                evidence=evidence,
                actor=call.actor,
                idempotency_key=(args.get("idempotency_key") or None),
                is_privileged=call.is_privileged,
            )
        except DriveError as exc:
            return _drive_error_result(exc)
        return _ok({"progress_id": progress.progress_id, "drive_id": drive_id})


class DriveTransitionTool(_BaseDriveTool):
    """Drive a control transition, or propose a terminal one for verification."""

    @property
    def tool_name(self) -> str:
        return "drive_transition"

    @property
    def description(self) -> str:
        return "Transition a drive (pause/resume/…) or propose completed/failed"

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "drive_id": {"type": "string"},
                "status": {"type": "string"},
                "expected_revision": {"type": "integer"},
                "reason": {"type": "string"},
                "evidence": {"type": "object"},
            },
            "required": ["drive_id", "status"],
        }

    async def _execute(
        self, args: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        call, err = await self._resolve_or_error(context)
        if err is not None:
            return err
        drive_id = (args.get("drive_id") or "").strip()
        status_raw = (args.get("status") or "").strip().lower()
        if not drive_id or not status_raw:
            return _err("'drive_id' and 'status' are required")
        try:
            target = DriveStatus(status_raw)
        except ValueError:
            return _err(
                f"unknown status {status_raw!r}; use one of active, waiting, "
                "blocked, paused, cancelled, completed, failed"
            )
        expected_revision = args.get("expected_revision")
        if expected_revision is not None and (
            not isinstance(expected_revision, int)
            or isinstance(expected_revision, bool)
        ):
            return _err("'expected_revision' must be an integer")
        if target in _TERMINAL_TARGETS:
            return await self._propose(call, drive_id, target, args, expected_revision)
        if target not in _TRANSITION_TARGETS:
            return _err(
                f"status {status_raw!r} is not a self-service transition target"
            )
        if expected_revision is None:
            return _err("'expected_revision' is required for a control transition")
        try:
            record = await call.manager.transition(
                drive_id,
                target,
                expected_revision=expected_revision,
                actor=call.actor,
                status_reason=(args.get("reason") or None),
                is_privileged=call.is_privileged,
            )
        except DriveError as exc:
            return _drive_error_result(exc)
        return _ok(await _summary_with_actions(call, record))

    async def _propose(
        self,
        call: _DriveCall,
        drive_id: str,
        target: DriveStatus,
        args: dict[str, Any],
        expected_revision: int | None,
    ) -> ToolResult:
        evidence = args.get("evidence")
        if evidence is not None and not isinstance(evidence, dict):
            return _err("'evidence' must be an object")
        try:
            result = await call.manager.propose_transition(
                drive_id,
                target,
                actor=call.actor,
                evidence=evidence,
                reason=(args.get("reason") or None),
                expected_revision=expected_revision,
                is_privileged=call.is_privileged,
            )
        except DriveError as exc:
            return _drive_error_result(exc)
        if isinstance(result, DriveRecord):
            summary = await _summary_with_actions(call, result)
            summary["proposal"] = "accepted"
            return _ok(summary)
        # A pending proposal awaiting a distinct approver (actor/two_party).
        return _ok(
            {
                "proposal_id": result.proposal_id,
                "drive_id": drive_id,
                "target_status": target.value,
                "proposal": "pending",
            }
        )


def build_self_service_tools() -> list[BaseTool]:
    """Instantiate the five self-service Drive tools (injection order)."""
    return [
        DriveCreateTool(),
        DriveStatusTool(),
        DriveUpdateTool(),
        DriveReportTool(),
        DriveTransitionTool(),
    ]


SELF_SERVICE_TOOL_NAMES: tuple[str, ...] = (
    "drive_create",
    "drive_status",
    "drive_update",
    "drive_report",
    "drive_transition",
)


__all__ = [
    "SELF_SERVICE_TOOL_NAMES",
    "DriveCreateTool",
    "DriveReportTool",
    "DriveStatusTool",
    "DriveTransitionTool",
    "DriveUpdateTool",
    "build_self_service_tools",
]
