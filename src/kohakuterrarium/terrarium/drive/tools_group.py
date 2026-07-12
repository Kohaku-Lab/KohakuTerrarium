"""The privileged ``group_drive`` administration tool (design §9.3).

Injected ONLY onto privileged creatures of a Drive-enabled Terrarium (see
:mod:`drive.injection`). It is the graph-scoped counterpart to the five
self-service tools: a privileged creature uses it to create graph-owned Drives
and to inspect / assign / reassign / unassign / transfer / wake / retire / replay
Drives **within its own graph**.

Like every ``group_*`` tool it resolves the caller from the trusted
:class:`GroupContext` (never from arguments) and requires a privileged caller.
Tool presence is not authorization (rule §4.15): the ``DriveManager`` re-checks
graph-privilege ACL on every call, so even a mis-registered non-privileged caller
who reached the manager would be denied.
"""

from typing import Any

from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolContext,
    ToolResult,
)
from kohakuterrarium.terrarium.channels import DRIVE_SERVICE_KEY
from kohakuterrarium.terrarium.drive.errors import DriveError
from kohakuterrarium.terrarium.drive.models import ActorRef, DriveRecord
from kohakuterrarium.terrarium.drive.requests import CreateDriveRequest, DriveQuery
from kohakuterrarium.terrarium.drive.tools import (
    _drive_error_result,
    _err,
    _ok,
)
from kohakuterrarium.terrarium.group_tool_context import (
    GroupContext,
    GroupToolError,
    resolve_group_context,
    resolve_group_target,
)

_ACTIONS = frozenset(
    {
        "create",
        "list",
        "assign",
        "unassign",
        "transfer_owner",
        "wake",
        "retire",
        "replay",
    }
)


class _GroupDriveCall:
    """Resolved, trusted context for one ``group_drive`` call."""

    __slots__ = ("manager", "runtime", "gctx", "caller", "actor", "graph_id")

    def __init__(self, manager: Any, runtime: Any, gctx: GroupContext) -> None:
        self.manager = manager
        self.runtime = runtime
        self.gctx = gctx
        self.caller = gctx.caller
        self.actor = ActorRef("creature", gctx.caller.creature_id)
        self.graph_id = gctx.caller.graph_id


def _resolve_call(ctx: ToolContext | None) -> _GroupDriveCall:
    """Resolve the privileged caller + engine + DriveManager, or raise
    :class:`GroupToolError` with a model-shaped message."""
    gctx = resolve_group_context(ctx, require_privileged=True)
    runtime = ctx.environment.get(DRIVE_SERVICE_KEY) if ctx.environment else None
    if runtime is None:
        raise GroupToolError("the Drive runtime is not enabled on this terrarium")
    manager = runtime.manager_for(gctx.caller.graph_id)
    if manager is None:
        raise GroupToolError("no Drive manager is available for this graph")
    return _GroupDriveCall(manager, runtime, gctx)


async def _summary(call: _GroupDriveCall, record: DriveRecord) -> dict[str, Any]:
    assignment = await call.manager.get_assignment(record.drive_id)
    availability = call.runtime.snapshot.availability_for(record)
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
        # aggregate (R1-41): group_drive only administers its own graph's rows.
        "durability": call.runtime.durability_for(call.graph_id),
        "availability": availability.value,
        "assignee": (
            assignment.assignee_creature_id if assignment is not None else None
        ),
        "allowed_actions": list(
            call.manager.allowed_actions(
                call.actor, record, assignment, is_privileged=True
            )
        ),
    }


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class GroupDriveTool(BaseTool):
    """Privileged graph-scoped Drive administration (single dispatch tool)."""

    needs_context = True

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    @property
    def tool_name(self) -> str:
        return "group_drive"

    @property
    def description(self) -> str:
        return "Administer this graph's drives (create/list/assign/wake/retire/replay)"

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": sorted(_ACTIONS)},
                "drive_id": {"type": "string"},
                "delivery_id": {"type": "string"},
                "title": {"type": "string"},
                "kind": {"type": "string"},
                "spec": {"type": "object"},
                "assignee": {"type": "string"},
                "new_owner": {"type": "string"},
                "priority": {"type": "integer"},
                "expected_revision": {"type": "integer"},
                "idempotency_key": {"type": "string"},
            },
            "required": ["action"],
        }

    async def _execute(
        self, args: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        action = (args.get("action") or "").strip().lower()
        if action not in _ACTIONS:
            return _err(f"unknown action {action!r}; use one of {sorted(_ACTIONS)}")
        try:
            call = _resolve_call(context)
        except GroupToolError as exc:
            return _err(str(exc))
        try:
            return await self._dispatch(action, call, args)
        except DriveError as exc:
            return _drive_error_result(exc)

    async def _dispatch(
        self, action: str, call: _GroupDriveCall, args: dict[str, Any]
    ) -> ToolResult:
        match action:
            case "create":
                return await self._create(call, args)
            case "list":
                return await self._list(call, args)
            case "assign":
                return await self._assign(call, args)
            case "unassign":
                return await self._unassign(call, args)
            case "transfer_owner":
                return await self._transfer_owner(call, args)
            case "wake":
                return await self._wake(call, args)
            case "retire":
                return await self._retire(call, args)
            case _:  # replay
                return await self._replay(call, args)

    # -- actions -------------------------------------------------------------

    async def _create(self, call: _GroupDriveCall, args: dict[str, Any]) -> ToolResult:
        title = (args.get("title") or "").strip()
        if not title:
            return _err("'title' is required for create")
        spec = args.get("spec") or {}
        if not isinstance(spec, dict):
            return _err("'spec' must be an object")
        assignee_id = None
        raw_assignee = (args.get("assignee") or "").strip()
        if raw_assignee:
            target = resolve_group_target(call.gctx, raw_assignee)
            if target is None:
                return _err(f"assignee {raw_assignee!r} is not in your graph")
            assignee_id = target.creature_id
        request = CreateDriveRequest(
            kind=(args.get("kind") or "generic").strip(),
            title=title,
            scope_type="graph",
            scope_id=call.graph_id,
            owner=call.actor,
            owner_scope="graph",
            created_by=call.actor,
            spec=spec,
            priority=(
                args.get("priority", 0)
                if _int_or_none(args.get("priority")) is not None
                else 0
            ),
            assignee_creature_id=assignee_id,
            idempotency_key=(args.get("idempotency_key") or None),
        )
        record = await call.manager.create_drive(
            request, actor=call.actor, graph_id=call.graph_id, is_privileged=True
        )
        return _ok(await _summary(call, record))

    async def _list(self, call: _GroupDriveCall, args: dict[str, Any]) -> ToolResult:
        records = await call.manager.list_drives(DriveQuery(graph_id=call.graph_id))
        summaries = [await _summary(call, r) for r in records]
        return _ok({"drives": summaries, "count": len(summaries)})

    async def _require_in_graph(
        self, call: _GroupDriveCall, drive_id: str
    ) -> ToolResult | None:
        """group_drive only administers Drives in the caller's own graph (§9.3).

        The repository tracks each Drive's graph, so a graph-scoped list is the
        authoritative membership check — a foreign-graph ``drive_id`` is denied
        even though the caller is privileged."""
        records = await call.manager.list_drives(DriveQuery(graph_id=call.graph_id))
        if not any(r.drive_id == drive_id for r in records):
            return _err(f"drive {drive_id!r} is not in your graph")
        return None

    async def _assign(self, call: _GroupDriveCall, args: dict[str, Any]) -> ToolResult:
        drive_id, rev, err = self._id_and_rev(args)
        if err is not None:
            return err
        guard = await self._require_in_graph(call, drive_id)
        if guard is not None:
            return guard
        raw_assignee = (args.get("assignee") or "").strip()
        if not raw_assignee:
            return _err("'assignee' is required for assign")
        target = resolve_group_target(call.gctx, raw_assignee)
        if target is None:
            return _err(f"assignee {raw_assignee!r} is not in your graph")
        record = await call.manager.assign(
            drive_id,
            target.creature_id,
            call.graph_id,
            expected_revision=rev,
            actor=call.actor,
            is_privileged=True,
        )
        return _ok(await _summary(call, record))

    async def _unassign(
        self, call: _GroupDriveCall, args: dict[str, Any]
    ) -> ToolResult:
        drive_id, rev, err = self._id_and_rev(args)
        if err is not None:
            return err
        guard = await self._require_in_graph(call, drive_id)
        if guard is not None:
            return guard
        record = await call.manager.unassign(
            drive_id, expected_revision=rev, actor=call.actor, is_privileged=True
        )
        return _ok(await _summary(call, record))

    async def _transfer_owner(
        self, call: _GroupDriveCall, args: dict[str, Any]
    ) -> ToolResult:
        drive_id, rev, err = self._id_and_rev(args)
        if err is not None:
            return err
        guard = await self._require_in_graph(call, drive_id)
        if guard is not None:
            return guard
        raw_owner = (args.get("new_owner") or "").strip()
        if not raw_owner:
            return _err("'new_owner' (an actor ref like 'creature:<id>') is required")
        try:
            new_owner = ActorRef.parse(raw_owner)
        except DriveError as exc:
            return _drive_error_result(exc)
        record = await call.manager.transfer_owner(
            drive_id,
            new_owner,
            expected_revision=rev,
            actor=call.actor,
            is_privileged=True,
        )
        return _ok(await _summary(call, record))

    async def _wake(self, call: _GroupDriveCall, args: dict[str, Any]) -> ToolResult:
        drive_id = (args.get("drive_id") or "").strip()
        if not drive_id:
            return _err("'drive_id' is required")
        guard = await self._require_in_graph(call, drive_id)
        if guard is not None:
            return guard
        record = await call.manager.wake_drive(
            drive_id,
            actor=call.actor,
            expected_revision=_int_or_none(args.get("expected_revision")),
            is_privileged=True,
        )
        return _ok(await _summary(call, record))

    async def _retire(self, call: _GroupDriveCall, args: dict[str, Any]) -> ToolResult:
        drive_id, rev, err = self._id_and_rev(args)
        if err is not None:
            return err
        guard = await self._require_in_graph(call, drive_id)
        if guard is not None:
            return guard
        record = await call.manager.retire_drive(
            drive_id, expected_revision=rev, actor=call.actor, is_privileged=True
        )
        return _ok(await _summary(call, record))

    async def _replay(self, call: _GroupDriveCall, args: dict[str, Any]) -> ToolResult:
        drive_id = (args.get("drive_id") or "").strip()
        delivery_id = (args.get("delivery_id") or "").strip()
        if not drive_id or not delivery_id:
            return _err("'drive_id' and 'delivery_id' are required for replay")
        guard = await self._require_in_graph(call, drive_id)
        if guard is not None:
            return guard
        deliveries = await call.manager.list_deliveries(drive_id)
        if not any(d.delivery_id == delivery_id for d in deliveries):
            return _err(f"delivery {delivery_id!r} is not on drive {drive_id!r}")
        delivery = await call.manager.replay_dead_letter(
            delivery_id, actor=call.actor, is_privileged=True
        )
        return _ok({"delivery_id": delivery.delivery_id, "drive_id": delivery.drive_id})

    def _id_and_rev(self, args: dict[str, Any]) -> tuple[str, int, ToolResult | None]:
        drive_id = (args.get("drive_id") or "").strip()
        rev = _int_or_none(args.get("expected_revision"))
        if not drive_id or rev is None:
            return (
                "",
                0,
                _err("'drive_id' and integer 'expected_revision' are required"),
            )
        return drive_id, rev, None


def build_group_drive_tools() -> list[BaseTool]:
    """Instantiate the privileged Drive tools (injection order)."""
    return [GroupDriveTool()]


GROUP_DRIVE_TOOL_NAMES: tuple[str, ...] = ("group_drive",)


__all__ = [
    "GROUP_DRIVE_TOOL_NAMES",
    "GroupDriveTool",
    "build_group_drive_tools",
]
