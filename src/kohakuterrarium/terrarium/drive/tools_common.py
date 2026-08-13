"""Shared helpers and base tool for the self-service Drive tools."""

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
from kohakuterrarium.terrarium.drive.models import ActorRef, DriveRecord
from kohakuterrarium.terrarium.group_tool_context import (
    GroupToolError,
    resolve_group_context,
)


class _DriveCall:
    """Hold the trusted runtime context for one Drive tool invocation."""

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
    """Resolve the caller, engine, and graph-scoped Drive manager."""
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


def _format_value(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value) or "none"
    return str(value)


def _format_drive(summary: dict[str, Any]) -> str:
    lines = [
        f"Drive {summary['drive_id']}: {summary['title']}",
        f"Status: {summary['status']} | Kind: {summary['kind']} | Revision: {summary['revision']}",
        f"Owner: {summary['owner']} | Assignee: {_format_value(summary.get('assignee'))}",
        f"Scope: {summary['scope_type']}:{summary['scope_id']} | Priority: {summary['priority']}",
    ]
    if summary.get("availability") is not None:
        lines.append(f"Availability: {summary['availability']}")
    if summary.get("durability") is not None:
        lines.append(f"Durability: {summary['durability']}")
    if summary.get("proposal") is not None:
        lines.append(f"Proposal: {summary['proposal']}")
    actions = summary.get("allowed_actions") or []
    if actions:
        lines.append(f"Allowed actions: {_format_value(actions)}")
    return "\n".join(lines)


def _format_payload(payload: dict[str, Any]) -> str:
    drives = payload.get("drives")
    if isinstance(drives, list):
        if not drives:
            return "No matching drives."
        return "\n\n".join(_format_drive(item) for item in drives)
    if {"drive_id", "title", "status", "kind"}.issubset(payload):
        return _format_drive(payload)
    if "progress_id" in payload:
        return (
            f"Progress recorded for drive {payload['drive_id']}.\n"
            f"Progress ID: {payload['progress_id']}"
        )
    if "proposal_id" in payload:
        return (
            f"Transition proposed for drive {payload['drive_id']}: "
            f"{payload['target_status']} ({payload.get('proposal', 'pending')}).\n"
            f"Proposal ID: {payload['proposal_id']}"
        )
    if "delivery_id" in payload:
        return (
            f"Delivery {payload['delivery_id']} replayed for drive "
            f"{payload['drive_id']}."
        )
    return "\n".join(f"{key}: {_format_value(value)}" for key, value in payload.items())


def _ok(payload: dict[str, Any]) -> ToolResult:
    return ToolResult(
        output=_format_payload(payload),
        exit_code=0,
        metadata={"drive": payload},
    )


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
    """Build a bounded authorized summary without exposing the raw Drive spec."""
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
        # Durability must describe the record's graph rather than a possibly
        # mixed aggregate across the engine.
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
