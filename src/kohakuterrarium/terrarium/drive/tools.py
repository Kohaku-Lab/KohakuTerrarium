"""Expose identity-safe self-service Drive tools to creatures.

Each tool derives its actor from trusted execution context and delegates
authorization to the graph's Drive manager. Concise text is returned to the
model while structured payloads remain available in :class:`ToolResult` metadata.
"""

from typing import Any

from kohakuterrarium.modules.tool.base import BaseTool, ToolContext, ToolResult
from kohakuterrarium.terrarium.drive.errors import (
    DriveError,
    DrivePermissionError,
    DriveValidationError,
)
from kohakuterrarium.terrarium.drive.models import DriveRecord, DriveStatus
from kohakuterrarium.terrarium.drive.requests import (
    CreateDriveRequest,
    DrivePatch,
    DriveQuery,
)
from kohakuterrarium.terrarium.drive.tools_common import (
    _BaseDriveTool,
    _DriveCall,
    _drive_error_result,
    _err,
    _ok,
    _summary_with_actions,
)

# Terminal states require proposal verification; administrative retirement is
# intentionally absent from the self-service surface.
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


class DriveCreateTool(_BaseDriveTool):
    """Create a caller-owned, caller-scoped Drive of an enabled kind."""

    @property
    def tool_name(self) -> str:
        return "drive_create"

    @property
    def description(self) -> str:
        return (
            "Create a durable drive you own (kind, title, spec). Use kind='goal': "
            "it is the only kind with a user-facing /goal command surface, so "
            "prefer it for anything a human should track. For 'goal', spec must "
            "include a non-empty 'objective'; set "
            "spec.autonomy='continue_when_ready' (the default is 'manual') so "
            "the goal keeps driving you automatically. Other kinds (e.g. "
            "'generic') have no dedicated command surface and are managed via "
            "the drive tools."
        )

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "description": (
                        "Drive kind; prefer 'goal' — the only kind with a "
                        "user-facing /goal command surface. Other kinds "
                        "(e.g. 'generic') have no dedicated command surface and "
                        "are managed via the drive tools. For 'goal', see the "
                        "spec description."
                    ),
                },
                "title": {"type": "string"},
                "spec": {
                    "type": "object",
                    "description": (
                        "Kind-specific spec. For kind='goal', use the properties "
                        "below ('objective' is required); other kinds treat spec "
                        "as opaque."
                    ),
                    "properties": {
                        "objective": {
                            "type": "string",
                            "description": (
                                "Required for 'goal': the durable objective to pursue."
                            ),
                        },
                        "autonomy": {
                            "type": "string",
                            "enum": ["manual", "continue_when_ready"],
                            "description": (
                                "Optional; defaults to 'manual'. Set "
                                "'continue_when_ready' so the goal keeps "
                                "driving you automatically."
                            ),
                        },
                        "success_criteria": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Optional criteria that would demonstrate completion."
                            ),
                        },
                        "constraints": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional constraints the goal must respect.",
                        },
                        "completion_policy": {
                            "type": "string",
                            "enum": ["self_propose", "user_confirm", "verifier"],
                            "description": "Optional; default 'self_propose'.",
                        },
                        "budgets": {
                            "type": "object",
                            "description": (
                                "Optional pursuit limits; continuation stops when "
                                "exhausted."
                            ),
                            "properties": {
                                "max_turns": {"type": "integer"},
                                "max_tool_calls": {"type": "integer"},
                                "max_walltime_s": {"type": "integer"},
                            },
                        },
                    },
                },
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
        # Ownership, scope, and assignment come only from trusted caller context;
        # tool arguments cannot claim another identity.
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
        except DriveValidationError as exc:
            if kind == "goal":
                return _err(
                    f"invalid goal spec: {exc}; kind='goal' requires a spec with "
                    "a non-empty 'objective' — e.g. spec={'objective': '...', "
                    "'autonomy': 'continue_when_ready'}"
                )
            return _drive_error_result(exc)
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
        return (
            "Transition a drive you own, or as the assignee of a foreign-owned "
            "drive set it to 'waiting'/'blocked' (use 'blocked' when you need "
            "user intervention; 'paused'/'cancelled' are owner-only). Control "
            "transitions require 'expected_revision' (see drive_status); "
            "completed/failed go through proposal with evidence."
        )

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "drive_id": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": [
                        "draft",
                        "active",
                        "waiting",
                        "blocked",
                        "paused",
                        "cancelled",
                        "completed",
                        "failed",
                    ],
                    "description": (
                        "Target status. As an assignee you may only set "
                        "'waiting' or 'blocked'; 'paused'/'cancelled' require "
                        "the owner; completed/failed go through proposal with "
                        "evidence."
                    ),
                },
                "expected_revision": {
                    "type": "integer",
                    "description": (
                        "Current revision from drive_status; required for control "
                        "transitions, optional for completed/failed proposals."
                    ),
                },
                "reason": {"type": "string"},
                "evidence": {
                    "type": "object",
                    "description": "Evidence for completed/failed proposals.",
                },
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
                f"unknown status {status_raw!r}; use one of draft, active, "
                "waiting, blocked, paused, cancelled, completed, failed"
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
        except DrivePermissionError as exc:
            return _err(
                f"permission denied: {exc}; as an assignee you may only "
                "transition to 'waiting' or 'blocked' (use 'blocked' when you "
                "need user intervention); 'paused' and 'cancelled' require the "
                "owner"
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
        return _ok(
            {
                "proposal_id": result.proposal_id,
                "drive_id": drive_id,
                "target_status": target.value,
                "proposal": "pending",
            }
        )


def build_self_service_tools() -> list[BaseTool]:
    """Create the self-service Drive tools in prompt injection order."""
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
