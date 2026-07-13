"""GoalPlugin + its ``/goal`` command — the optional built-in Goal composition.

`/goal` is **not** a framework feature. The plugin is a pure adapter over the
generic Drive runtime: it contributes the ``/goal`` user command and a bounded
prompt fragment explaining Goal completion semantics. It owns no Drive record,
queue, scheduler, or repository, and holds no canonical Goal state. It ships
registered and enabled automatically for every Terrarium-hosted creature. A
standalone Agent may still opt in explicitly. Disabling it removes ``/goal`` and
the prompt fragment while leaving Goal Drives untouched — Goal-kind runtime
semantics live in the separately-enabled
:class:`~kohakuterrarium.terrarium.drive.goal.GoalDriveRegistration`. Enabling
this plugin does NOT enable that registration; they are independent toggles
(design §11.3): the plugin is a plugin-panel decision, the registration a
Drive-settings decision.

The ``/goal`` command parses deterministically, resolves the authenticated user
actor + focused creature + ``TerrariumService`` from the trusted
:class:`UserCommandContext`, and calls generic Drive service methods. It stores
no canonical Goal state; every ``show`` / ``list`` reads live Drive state. When
the Drive runtime or the ``goal`` registration is unavailable it returns a clear
typed result and never falls back to plugin-local state.
"""

from typing import Any

from kohakuterrarium.modules.plugin.base import BasePlugin, PluginContext
from kohakuterrarium.modules.user_command.base import (
    BaseUserCommand,
    CommandLayer,
    UserCommandContext,
    UserCommandResult,
    ui_info_panel,
    ui_list,
)
from kohakuterrarium.terrarium.drive.errors import (
    DriveConflictError,
    DriveError,
    DriveIdempotencyConflictError,
    DriveNotFoundError,
    DrivePermissionError,
    DriveRegistrationDisabledError,
    DriveRegistrationIncompatibleError,
    DriveRegistrationNotFoundError,
    DriveTransitionError,
)
from kohakuterrarium.terrarium.drive.goal import GoalSpecError, build_goal_spec
from kohakuterrarium.terrarium.drive.models import ActorRef, DriveStatus
from kohakuterrarium.terrarium.drive.requests import CreateDriveRequest
from kohakuterrarium.terrarium.service import LocalTerrariumService

# Bounded, static guidance. The CURRENT objective for a turn arrives through the
# Goal Drive event projection (design §8.7: current records are not dumped into
# the system prompt), so this fragment only explains the durable semantics.
_PLUGIN_PROMPT = (
    "You may be assigned durable Goal drives. A Goal is a continuing "
    "commitment, not a one-shot request: pursue it across turns, report "
    "material progress with evidence, and propose completion with evidence "
    "rather than asserting it. Completion is authoritative only through the "
    "goal's completion policy — a user_confirm goal completes only when the "
    "human confirms via /goal complete. If a budget is exhausted, propose "
    "pausing; a budget never means success."
)


class GoalPlugin(BasePlugin):
    """Contributes the ``/goal`` command + Goal completion-semantics prompt."""

    name = "goal"
    description = "Optional /goal command + Goal prompt guidance over Drive."
    priority = 50

    def contribute_user_commands(self) -> dict[str, Any]:
        return {"goal": GoalCommand()}

    def get_prompt_content(self, context: PluginContext) -> str | None:
        return _PLUGIN_PROMPT


# ---------------------------------------------------------------------------
# /goal command
# ---------------------------------------------------------------------------

_SUBCOMMANDS = frozenset(
    {"set", "show", "list", "pause", "resume", "cancel", "complete", "assign"}
)
_TERMINAL = frozenset(
    {
        DriveStatus.COMPLETED,
        DriveStatus.FAILED,
        DriveStatus.CANCELLED,
        DriveStatus.RETIRED,
    }
)
_LIVE = frozenset(DriveStatus) - _TERMINAL
_ELIGIBLE_STATUSES = {
    "pause": frozenset({DriveStatus.ACTIVE, DriveStatus.WAITING, DriveStatus.BLOCKED}),
    "resume": frozenset({DriveStatus.WAITING, DriveStatus.BLOCKED, DriveStatus.PAUSED}),
    "cancel": _LIVE,
    "complete": frozenset({DriveStatus.ACTIVE}),
}
_TRANSITION_TARGETS = {
    "pause": DriveStatus.PAUSED,
    "resume": DriveStatus.ACTIVE,
    "cancel": DriveStatus.CANCELLED,
}
_TRANSITION_TITLES = {
    "pause": "Goal paused",
    "resume": "Goal resumed",
    "cancel": "Goal cancelled",
}
_SET_FLAG_KEYS = frozenset({"autonomy", "policy", "criteria"})
_USAGE = (
    "usage: /goal set <objective> | show [id] | list | pause [id] | "
    "resume [id] | cancel [id] | complete [id] | assign <id> <creature>\n"
    "list shows live goals; show <id> can display terminal history; "
    "actions without an id select an eligible live goal"
)


class _GoalUnavailable(Exception):
    """The Drive runtime / focused creature / goal registration is unusable."""


class GoalCommand(BaseUserCommand):
    """Human ``/goal`` adapter over the generic Drive service (design §11)."""

    name = "goal"
    aliases: list[str] = []
    description = "Manage durable Goal drives (set/show/list/pause/…)"
    layer = CommandLayer.AGENT
    # Signals the CLI to dispatch this command with the engine + focused
    # creature in UserCommandContext.extra (see app_multi.dispatch).
    needs_engine = True
    override = False

    async def _execute(
        self, args: str, context: UserCommandContext
    ) -> UserCommandResult:
        tokens = (args or "").strip().split(None, 1)
        sub = tokens[0].lower() if tokens else "show"
        rest = tokens[1] if len(tokens) > 1 else ""
        if sub not in _SUBCOMMANDS:
            return UserCommandResult(error=_USAGE)
        try:
            resolved = _resolve(context)
        except _GoalUnavailable as exc:
            return UserCommandResult(error=str(exc))
        try:
            return await self._dispatch(sub, rest, resolved)
        except _GoalUnavailable as exc:
            return UserCommandResult(error=str(exc))
        except DriveError as exc:
            return UserCommandResult(error=_error_text(exc))

    async def _dispatch(
        self, sub: str, rest: str, ctx: "_Resolved"
    ) -> UserCommandResult:
        match sub:
            case "set":
                return await self._set(rest, ctx)
            case "list":
                return await self._list(ctx)
            case "show":
                return await self._show(rest.strip(), ctx)
            case "assign":
                return await self._assign(rest, ctx)
            case "complete":
                return await self._complete(rest.strip(), ctx)
            case _:  # pause / resume / cancel
                return await self._transition(sub, rest.strip(), ctx)

    # -- set -----------------------------------------------------------------

    async def _set(self, rest: str, ctx: "_Resolved") -> UserCommandResult:
        opts, objective = _parse_set(rest)
        if not objective:
            return UserCommandResult(error="usage: /goal set <objective>")
        try:
            spec = build_goal_spec(
                objective,
                success_criteria=_split_criteria(opts.get("criteria")),
                completion_policy=opts.get("policy", "self_propose"),
                autonomy=opts.get("autonomy", "continue_when_ready"),
            )
        except GoalSpecError as exc:
            return UserCommandResult(error=f"invalid goal: {exc}")
        graph_id = await _graph_of(ctx, ctx.creature_id)
        request = CreateDriveRequest(
            kind="goal",
            title=objective[:120],
            scope_type="graph",
            scope_id=graph_id,
            owner=ctx.principal,
            owner_scope="actor",
            created_by=ctx.principal,
            spec=spec,
            assignee_creature_id=ctx.creature_id,
        )
        # Creating a user-owned Drive assigned to *another* actor (the focused
        # creature) is a graph-authority (create_graph) operation, which a plain
        # user actor does not hold by default. The trusted operator console
        # supplies an explicit, audited operator elevation — never creature
        # privilege. Every other /goal verb runs as the plain user owner.
        view = await ctx.service.create_drive(
            request,
            graph_id=graph_id,
            actor=ctx.principal,
            operator=ctx.is_operator,
        )
        await ctx.service.wake_drive(
            view.record.drive_id,
            actor=ctx.principal,
            expected_revision=view.record.revision,
            is_privileged=False,
        )
        return _panel("Goal created", view)

    # -- show / list ---------------------------------------------------------

    async def _list(self, ctx: "_Resolved") -> UserCommandResult:
        views = [view for view in await _list_goals(ctx) if view.record.status in _LIVE]
        if not views:
            return UserCommandResult(output="No live goals for this creature.")
        items = [
            {
                "label": f"{v.record.title} [status: {v.record.status.value}]",
                "description": f"id={v.record.drive_id} owner={v.record.owner.format()}",
            }
            for v in views
        ]
        return UserCommandResult(
            output=_list_text(views),
            data=ui_list("Goals", items),
        )

    async def _show(self, drive_id: str, ctx: "_Resolved") -> UserCommandResult:
        view = await _select(ctx, drive_id)
        if view is None:
            return UserCommandResult(output="No active goal for this creature.")
        return _panel("Goal", view)

    # -- transitions ---------------------------------------------------------

    async def _transition(
        self, sub: str, drive_id: str, ctx: "_Resolved"
    ) -> UserCommandResult:
        view = await _select_eligible(ctx, drive_id, sub)
        if view is None:
            return _no_eligible_goal(sub)
        # Owner capability: the user owns the goal, so pause/resume/cancel need
        # no operator privilege.
        updated = await ctx.service.transition_drive(
            view.record.drive_id,
            _TRANSITION_TARGETS[sub],
            expected_revision=view.record.revision,
            actor=ctx.principal,
            is_privileged=False,
        )
        return _panel(_TRANSITION_TITLES[sub], updated)

    async def _complete(self, drive_id: str, ctx: "_Resolved") -> UserCommandResult:
        view = await _select_eligible(ctx, drive_id, "complete")
        if view is None:
            return _no_eligible_goal("complete")
        # User-authoritative completion: the owner proposes (owner holds
        # propose_terminal — no operator privilege) with evidence so
        # self_propose / user_confirm / verifier all finalize (design §11.2).
        # The extension verifier honors the per-Drive policy.
        try:
            result = await ctx.service.propose_drive_transition(
                view.record.drive_id,
                DriveStatus.COMPLETED,
                actor=ctx.principal,
                evidence={"confirmed_by": ctx.principal.format()},
                expected_revision=view.record.revision,
                is_privileged=False,
            )
        except DriveTransitionError as exc:
            return UserCommandResult(error=f"completion rejected: {exc}")
        if isinstance(result, dict) and result.get("pending"):
            approved = await ctx.service.approve_drive_proposal(
                str(result.get("proposal_id")),
                actor=ctx.principal,
                operator=ctx.is_operator,
            )
            return _panel("Goal completed", approved)
        return _panel("Goal completed", result)

    async def _assign(self, rest: str, ctx: "_Resolved") -> UserCommandResult:
        parts = rest.split()
        if len(parts) < 2:
            return UserCommandResult(error="usage: /goal assign <id> <creature>")
        drive_id, target_creature = parts[0], parts[1]
        target_graph = await _graph_of(ctx, target_creature)
        current = await ctx.service.get_drive(
            drive_id, actor=ctx.principal, is_privileged=False
        )
        if current is None:
            return UserCommandResult(error=f"no such goal: {drive_id}")
        _require_goal(current, drive_id)
        # Reassigning a Drive to a graph member is a graph-authority operation
        # (like create), so it uses the operator console's audited elevation.
        view = await ctx.service.assign_drive(
            drive_id,
            target_creature,
            target_graph,
            expected_revision=current.record.revision,
            actor=ctx.principal,
            operator=ctx.is_operator,
        )
        return _panel(f"Goal assigned to {target_creature}", view)


# ---------------------------------------------------------------------------
# trusted-context resolution + helpers
# ---------------------------------------------------------------------------


class _Resolved:
    """The trusted handles one ``/goal`` invocation needs.

    ``is_operator`` is the console's graph-authority flag: it authorizes the
    graph-scoped verbs (``set`` create, ``assign``) that a plain user actor
    cannot perform. It defaults False (R1-21) — only a trusted local-console or
    authenticated-admin adapter sets it True; a multi-user ingress leaves it off.
    Read/manage verbs never consult it — the user acts as the Drive owner there.
    """

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
    service = extra.get("service") or extra.get("drive_service")
    if service is None and extra.get("engine") is not None:
        service = LocalTerrariumService(extra["engine"])
    if service is None:
        raise _GoalUnavailable(
            "/goal needs a running terrarium; none is available in this context"
        )
    creature_id = extra.get("creature_id") or ""
    if not creature_id:
        raise _GoalUnavailable("/goal has no focused creature to act on")
    principal = _principal(extra.get("principal"))
    # Default UNPRIVILEGED (R1-21): only a trusted local-console / authenticated-
    # admin adapter sets is_operator explicitly. Missing authority context is
    # never a silent operator grant.
    is_operator = bool(extra.get("is_operator", False))
    return _Resolved(service, creature_id, principal, is_operator)


def _principal(raw: Any) -> ActorRef:
    if isinstance(raw, ActorRef):
        if raw.kind != "user":
            # /goal always acts as the authenticated human, never as a plugin
            # or creature identity (design §11.5).
            raise _GoalUnavailable("/goal principal must be a user actor")
        return raw
    if isinstance(raw, str) and raw:
        actor = ActorRef.parse(raw)
        if actor.kind != "user":
            raise _GoalUnavailable("/goal principal must be a user actor")
        return actor
    return ActorRef("user", "local")


async def _graph_of(ctx: _Resolved, creature_id: str) -> str:
    info = await ctx.service.get_creature_info(creature_id)
    if info is None:
        raise _GoalUnavailable(f"unknown creature: {creature_id}")
    return info.graph_id


async def _list_goals(ctx: _Resolved) -> list[Any]:
    # Reads run as the plain user; the manager scopes the view to authorized
    # (owned / assigned) records without any privilege.
    views = await ctx.service.list_drives(
        actor=ctx.principal,
        assignee_creature_id=ctx.creature_id,
        kinds=frozenset({"goal"}),
        is_privileged=False,
    )
    return list(views)


def _require_goal(view: Any, drive_id: str) -> Any:
    """Ensure an explicitly-addressed Drive is goal-kind (design §11, R1-22).

    ``/goal <verb> <id>`` must never mutate a generic or package-kind Drive that
    the actor happens to be authorized for; a foreign kind is refused, not
    silently paused/completed/assigned. A ``None`` view (not found) passes
    through so callers report their own not-found message.
    """
    if view is not None and getattr(view.record, "kind", None) != "goal":
        raise _GoalUnavailable(
            f"drive {drive_id} is not a goal (kind="
            f"{getattr(view.record, 'kind', None)!r}); use /drives for it"
        )
    return view


async def _select(ctx: _Resolved, drive_id: str) -> Any | None:
    """Resolve an explicit goal, or the newest live goal for the creature.

    Explicit ``show`` keeps terminal history addressable. All implicit selection
    excludes terminal records so a stale completed/cancelled goal cannot become
    the current goal.
    """
    if drive_id:
        view = await ctx.service.get_drive(
            drive_id, actor=ctx.principal, is_privileged=False
        )
        return _require_goal(view, drive_id)
    return await _select_latest(ctx, _LIVE)


async def _select_eligible(ctx: _Resolved, drive_id: str, action: str) -> Any | None:
    """Select a goal whose current status permits ``action``."""
    eligible = _ELIGIBLE_STATUSES[action]
    if drive_id:
        view = await _select(ctx, drive_id)
        if view is None:
            return None
        if view.record.status not in eligible:
            allowed = ", ".join(sorted(status.value for status in eligible))
            raise _GoalUnavailable(
                f"cannot {action} goal {drive_id}: status is "
                f"{view.record.status.value}; eligible statuses: {allowed}"
            )
        return view
    return await _select_latest(ctx, eligible)


async def _select_latest(
    ctx: _Resolved, statuses: frozenset[DriveStatus]
) -> Any | None:
    matches = [
        view for view in await _list_goals(ctx) if view.record.status in statuses
    ]
    if not matches:
        return None
    matches.sort(key=lambda view: view.record.created_at)
    return matches[-1]


def _no_eligible_goal(action: str) -> UserCommandResult:
    allowed = ", ".join(sorted(status.value for status in _ELIGIBLE_STATUSES[action]))
    return UserCommandResult(
        error=f"no goal eligible to {action} (statuses: {allowed}; give an id)"
    )


def _parse_set(rest: str) -> tuple[dict[str, str], str]:
    """Split leading ``key=value`` flags from the free-text objective."""
    opts: dict[str, str] = {}
    words = rest.split()
    idx = 0
    while idx < len(words) and "=" in words[idx]:
        key, _, value = words[idx].partition("=")
        if key not in _SET_FLAG_KEYS:
            break
        opts[key] = value
        idx += 1
    return opts, " ".join(words[idx:]).strip()


def _split_criteria(raw: str | None) -> list[str]:
    if not raw:
        return []
    parts = raw.replace(";", ",").split(",")
    return [p.strip() for p in parts if p.strip()]


def _panel(title: str, view: Any) -> UserCommandResult:
    record = view.record
    spec = record.spec if isinstance(record.spec, dict) else {}
    fields = [
        {"key": "id", "value": record.drive_id},
        {"key": "objective", "value": str(spec.get("objective", record.title))},
        {"key": "status", "value": record.status.value},
        {"key": "revision", "value": str(record.revision)},
        {"key": "owner", "value": record.owner.format()},
        {"key": "assignee", "value": view.assignee_creature_id or "-"},
        {"key": "autonomy", "value": str(spec.get("autonomy", "-"))},
        {"key": "completion", "value": str(spec.get("completion_policy", "-"))},
    ]
    text = (
        f"{title}: {record.drive_id} [status: {record.status.value}] — {record.title}"
    )
    return UserCommandResult(output=text, data=ui_info_panel(title, fields))


def _list_text(views: list[Any]) -> str:
    lines = ["Goals:"]
    for v in views:
        lines.append(
            f"  {v.record.drive_id}  [status: {v.record.status.value}]  {v.record.title}"
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
        return (
            "the Goal drive registration is not enabled on this terrarium; "
            "enable it in Drive settings"
        )
    if isinstance(exc, (DriveConflictError, DriveIdempotencyConflictError)):
        return f"conflict: {exc}; refresh and retry"
    if isinstance(exc, DrivePermissionError):
        return f"permission denied: {exc}"
    if isinstance(exc, DriveNotFoundError):
        return f"no such goal: {exc}"
    return f"goal error: {exc}"


__all__ = ["GoalCommand", "GoalPlugin"]
