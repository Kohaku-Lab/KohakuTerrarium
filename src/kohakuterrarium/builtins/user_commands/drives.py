"""``/drives`` — the generic Drive record command (design §12.4-§12.6).

Independent of GoalPlugin: present on any creature hosted by a Drive-enabled
Terrarium. It resolves the service + actor + focused creature from the trusted
:class:`UserCommandContext` (same seam ``/goal`` uses) and delegates every
operation to :mod:`kohakuterrarium.studio.sessions.drives`, returning ordinary
:class:`UserCommandResult` payloads so CLI, TUI, and web render the same result.

Mutations require an explicit ``--revision`` (append-only ``progress`` excepted);
omitting it returns a refetch instruction rather than risking a blind overwrite.
"""

import json
from typing import Any

from kohakuterrarium.builtins.user_commands.registry import register_user_command
from kohakuterrarium.modules.user_command.base import (
    BaseUserCommand,
    CommandLayer,
    UserCommandContext,
    UserCommandResult,
    ui_info_panel,
    ui_list,
    ui_text,
)
from kohakuterrarium.studio.sessions import drives as _drives
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
from kohakuterrarium.terrarium.drive.models import ActorRef

_SUBCOMMANDS = frozenset(
    {"list", "show", "create", "pause", "resume", "wake", "cancel", "progress"}
)
_TRANSITIONS = {"pause": "paused", "resume": "active", "cancel": "cancelled"}
_USAGE = (
    "usage: /drives list [--mine|--assigned|--graph] [--status active,blocked] | "
    "show <id> | create --kind K --title T [--scope graph|creature] | "
    "pause|resume|wake|cancel <id> --revision N | progress <id> <summary>"
)


class _Unavailable(Exception):
    """The Drive runtime / focused creature is not usable in this context."""


@register_user_command("drives")
class DrivesCommand(BaseUserCommand):
    """Generic ``/drives`` record command over the Drive service (design §12)."""

    name = "drives"
    aliases: list[str] = []
    description = "Inspect + manage Drive records (list/show/create/pause/…)"
    layer = CommandLayer.AGENT
    # The CLI/TUI/API dispatch fills UserCommandContext.extra with the engine +
    # focused creature + trusted principal when this is set (see /goal).
    needs_engine = True

    async def _execute(
        self, args: str, context: UserCommandContext
    ) -> UserCommandResult:
        tokens = (args or "").strip().split(None, 1)
        sub = tokens[0].lower() if tokens else "list"
        rest = tokens[1] if len(tokens) > 1 else ""
        if sub not in _SUBCOMMANDS:
            return UserCommandResult(error=_USAGE)
        try:
            ctx = _resolve(context)
            return await self._dispatch(sub, rest, ctx)
        except _Unavailable as exc:
            return UserCommandResult(error=str(exc))
        except DriveError as exc:
            return UserCommandResult(error=_error_text(exc))

    async def _dispatch(
        self, sub: str, rest: str, ctx: "_Resolved"
    ) -> UserCommandResult:
        match sub:
            case "list":
                return await self._list(rest, ctx)
            case "show":
                return await self._show(rest.strip(), ctx)
            case "create":
                return await self._create(rest, ctx)
            case "progress":
                return await self._progress(rest, ctx)
            case _:  # pause / resume / wake / cancel
                return await self._transition(sub, rest, ctx)

    # -- reads ---------------------------------------------------------------

    async def _list(self, rest: str, ctx: "_Resolved") -> UserCommandResult:
        flags, _ = _parse_flags(rest)
        graph_id = await _graph_of(ctx, ctx.creature_id)
        assignee = ctx.creature_id if "assigned" in flags else None
        rows = await _drives.list_records(
            ctx.service,
            graph_id=graph_id,
            actor=ctx.principal,
            is_privileged=ctx.is_operator,
            statuses=flags.get("status"),
            kinds=flags.get("kind"),
            mine="mine" in flags,
            assignee_creature_id=assignee,
        )
        if not rows:
            return UserCommandResult(output="No drives in this graph.")
        items = [
            {
                "label": f"{r['title']} [{r['status']}]",
                "description": (
                    f"id={r['drive_id']} owner={r['owner']} "
                    f"assignee={r.get('assignee_creature_id') or '-'}"
                ),
            }
            for r in rows
        ]
        return UserCommandResult(output=_rows_text(rows), data=ui_list("Drives", items))

    async def _show(self, drive_id: str, ctx: "_Resolved") -> UserCommandResult:
        if not drive_id:
            return UserCommandResult(error="usage: /drives show <id>")
        record = await _drives.get_record(
            ctx.service, drive_id, actor=ctx.principal, is_privileged=ctx.is_operator
        )
        if record is None:
            return UserCommandResult(error=f"no such drive: {drive_id}")
        return _panel("Drive", record)

    # -- writes --------------------------------------------------------------

    async def _create(self, rest: str, ctx: "_Resolved") -> UserCommandResult:
        flags, _ = _parse_flags(rest)
        if not flags.get("kind") or not flags.get("title"):
            return UserCommandResult(
                error="usage: /drives create --kind K --title T [--scope graph|creature]"
            )
        graph_id = await _graph_of(ctx, ctx.creature_id)
        body: dict[str, Any] = {
            "kind": flags["kind"],
            "title": flags["title"],
            "scope_type": flags.get("scope", "graph"),
        }
        if "priority" in flags:
            body["priority"] = _int_flag(flags["priority"], "priority")
        if "spec-json" in flags:
            body["spec"] = _json_flag(flags["spec-json"], "spec-json")
        record = await _drives.create_record(
            ctx.service,
            graph_id=graph_id,
            actor=ctx.principal,
            body=body,
            is_privileged=ctx.is_operator,
            operator=ctx.is_operator,
        )
        return _panel("Drive created", record)

    async def _transition(
        self, sub: str, rest: str, ctx: "_Resolved"
    ) -> UserCommandResult:
        flags, positional = _parse_flags(rest)
        drive_id = positional[0] if positional else ""
        if not drive_id:
            return UserCommandResult(error=f"usage: /drives {sub} <id> --revision N")
        if sub == "wake":
            record = await _drives.wake_record(
                ctx.service,
                drive_id,
                actor=ctx.principal,
                expected_revision=_opt_int(flags.get("revision"), "revision"),
                is_privileged=ctx.is_operator,
            )
            return _panel("Drive woken", record)
        revision = _require_revision(flags)
        record = await _drives.transition_record(
            ctx.service,
            drive_id,
            target_status=_TRANSITIONS[sub],
            expected_revision=revision,
            actor=ctx.principal,
            is_privileged=ctx.is_operator,
        )
        return _panel(f"Drive {sub}d", record)

    async def _progress(self, rest: str, ctx: "_Resolved") -> UserCommandResult:
        parts = rest.split(None, 1)
        if len(parts) < 2 or not parts[1].strip():
            return UserCommandResult(error="usage: /drives progress <id> <summary>")
        drive_id, summary = parts[0], parts[1].strip()
        await _drives.report_progress_record(
            ctx.service,
            drive_id,
            summary=summary,
            evidence=None,
            actor=ctx.principal,
            is_privileged=ctx.is_operator,
        )
        return UserCommandResult(
            output=f"recorded progress on {drive_id}",
            data=ui_text(f"recorded progress on {drive_id}"),
        )


# ---------------------------------------------------------------------------
# trusted-context resolution + parsing helpers
# ---------------------------------------------------------------------------


class _Resolved:
    __slots__ = ("service", "creature_id", "principal", "is_operator")

    def __init__(
        self, service: Any, creature_id: str, principal: ActorRef, is_operator: bool
    ) -> None:
        self.service = service
        self.creature_id = creature_id
        self.principal = principal
        self.is_operator = is_operator


def _resolve(context: UserCommandContext) -> _Resolved:
    extra = context.extra or {}
    # This command is a BUILT-IN loaded eagerly by the command package, which
    # sits inside the ``terrarium.service`` import chain; it must never import
    # that module (even lazily — the dep-graph guard traces it and it closes a
    # cycle). So it consumes the trusted ``service`` the dispatch supplies rather
    # than wrapping a bare engine itself.
    service = extra.get("service") or extra.get("drive_service")
    if service is None:
        raise _Unavailable(
            "/drives needs a running terrarium service; none is available in "
            "this context"
        )
    creature_id = extra.get("creature_id") or ""
    if not creature_id:
        raise _Unavailable("/drives has no focused creature to act on")
    principal = _principal(extra.get("principal"))
    # Default UNPRIVILEGED (R1-21): a trusted local-console / authenticated-admin
    # adapter must pass is_operator explicitly; missing context is never elevated.
    is_operator = bool(extra.get("is_operator", False))
    return _Resolved(service, creature_id, principal, is_operator)


def _principal(raw: Any) -> ActorRef:
    if isinstance(raw, ActorRef):
        return raw
    if isinstance(raw, str) and raw:
        return ActorRef.parse(raw)
    return ActorRef("user", "local")


async def _graph_of(ctx: _Resolved, creature_id: str) -> str:
    info = await ctx.service.get_creature_info(creature_id)
    if info is None:
        raise _Unavailable(f"unknown creature: {creature_id}")
    return info.graph_id


def _parse_flags(rest: str) -> tuple[dict[str, str], list[str]]:
    """Split ``--key value`` / bare ``--flag`` options from positional args.

    A bare flag (``--mine``) maps to ``""``; a value flag consumes the next
    token unless it is itself a flag.
    """
    flags: dict[str, str] = {}
    positional: list[str] = []
    tokens = rest.split()
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        if token.startswith("--"):
            key = token[2:]
            if idx + 1 < len(tokens) and not tokens[idx + 1].startswith("--"):
                flags[key] = tokens[idx + 1]
                idx += 2
            else:
                flags[key] = ""
                idx += 1
        else:
            positional.append(token)
            idx += 1
    return flags, positional


def _require_revision(flags: dict[str, str]) -> int:
    if "revision" not in flags or flags["revision"] == "":
        raise _Unavailable(
            "this mutation needs --revision N; run /drives show <id> to read it"
        )
    return _int_flag(flags["revision"], "revision")


def _opt_int(value: str | None, name: str) -> int | None:
    if value is None or value == "":
        return None
    return _int_flag(value, name)


def _int_flag(value: str, name: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise _Unavailable(f"--{name} must be an integer, got {value!r}") from exc


def _json_flag(value: str, name: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise _Unavailable(f"--{name} must be valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise _Unavailable(f"--{name} must be a JSON object")
    return parsed


def _panel(title: str, record: dict[str, Any]) -> UserCommandResult:
    fields = [
        {"key": "id", "value": record.get("drive_id", "")},
        {"key": "title", "value": record.get("title", "")},
        {"key": "status", "value": record.get("status", "")},
        {"key": "revision", "value": str(record.get("revision", ""))},
        {"key": "kind", "value": record.get("kind", "")},
        {"key": "owner", "value": record.get("owner", "")},
        {"key": "assignee", "value": record.get("assignee_creature_id") or "-"},
        {
            "key": "scope",
            "value": f"{record.get('scope_type', '')}:{record.get('scope_id', '')}",
        },
        {"key": "availability", "value": record.get("availability", "")},
        {"key": "priority", "value": str(record.get("priority", ""))},
    ]
    text = (
        f"{title}: {record.get('drive_id', '')} "
        f"[{record.get('status', '')}] rev {record.get('revision', '')} — "
        f"{record.get('title', '')}"
    )
    return UserCommandResult(output=text, data=ui_info_panel(title, fields))


def _rows_text(rows: list[dict[str, Any]]) -> str:
    lines = ["Drives:"]
    for r in rows:
        lines.append(
            f"  {r['drive_id']}  [{r['status']}]  rev {r['revision']}  {r['title']}"
        )
    return "\n".join(lines)


def _error_text(exc: DriveError) -> str:
    if isinstance(
        exc,
        (
            DriveRegistrationDisabledError,
            DriveRegistrationIncompatibleError,
            DriveRegistrationNotFoundError,
        ),
    ):
        return f"registration unavailable: {exc}"
    if isinstance(exc, (DriveConflictError, DriveIdempotencyConflictError)):
        return f"conflict: {exc}; run /drives show <id> to refetch the revision"
    if isinstance(exc, DrivePermissionError):
        return f"permission denied: {exc}"
    if isinstance(exc, DriveNotFoundError):
        return f"no such drive: {exc}"
    return f"drive error: {exc}"


__all__ = ["DrivesCommand"]
