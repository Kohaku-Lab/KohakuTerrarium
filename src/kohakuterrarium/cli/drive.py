"""``kt drive`` — non-interactive Drive record automation (design §12.5).

Operator automation over a saved session: resume it through the managed
:class:`~kohakuterrarium.studio.studio.Studio` surface (which resolves the node's
Drive settings), run one record operation through the
:mod:`kohakuterrarium.studio.sessions.drives` façade, print a stable table or
``--json`` (the service DTO shape), then shut the engine down.

Mutations that omit ``--revision`` fail with a refetch instruction rather than
silently overwriting. Exit codes are meaningful: 0 ok, 1 error, 2 usage,
3 conflict, 4 not-found, 5 permission — so a script can branch on the outcome.
"""

import argparse
import asyncio
import json
from typing import Any

from kohakuterrarium.cli.run import _resolve_session
from kohakuterrarium.studio.sessions import drives as _drives
from kohakuterrarium.studio.studio import Studio
from kohakuterrarium.terrarium.drive.errors import (
    DriveConflictError,
    DriveError,
    DriveIdempotencyConflictError,
    DriveNotFoundError,
    DrivePermissionError,
)
from kohakuterrarium.terrarium.drive.models import ActorRef

# Local operator console: the CLI acts as the trusted node operator.
_ACTOR = ActorRef("user", "local")

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_CONFLICT = 3
EXIT_NOT_FOUND = 4
EXIT_PERMISSION = 5


def add_drive_subparser(subparsers: Any) -> None:
    """Wire ``kt drive <subcommand>`` onto the top-level parser."""
    parser = subparsers.add_parser(
        "drive", help="Inspect + administer Drive records in a saved session"
    )
    parser.add_argument(
        "--session",
        required=True,
        help="Session name/prefix or .kohakutr path to operate on",
    )
    parser.add_argument("--json", action="store_true", help="Emit the service DTO JSON")
    sub = parser.add_subparsers(dest="drive_command")

    p_list = sub.add_parser("list", help="List Drive records in the session")
    p_list.add_argument("--status", default=None, help="Filter, e.g. active,blocked")
    p_list.add_argument("--kind", default=None, help="Filter by kind")
    p_list.add_argument("--mine", action="store_true", help="Only caller-owned drives")
    p_list.add_argument(
        "--graph", default=None, help="Restrict to one graph id (default: all graphs)"
    )

    p_show = sub.add_parser("show", help="Show one Drive record")
    p_show.add_argument("drive_id")

    p_create = sub.add_parser("create", help="Create a Drive record")
    p_create.add_argument("--kind", required=True)
    p_create.add_argument("--title", required=True)
    p_create.add_argument("--scope", default="graph", choices=["graph", "creature"])
    p_create.add_argument(
        "--creature", default=None, help="Creature id (required for --scope creature)"
    )
    p_create.add_argument(
        "--graph", default=None, help="Target graph id (required if session has many)"
    )
    p_create.add_argument("--priority", type=int, default=0)
    p_create.add_argument("--spec-json", default=None, dest="spec_json")

    p_assign = sub.add_parser("assign", help="Assign a Drive to a creature")
    p_assign.add_argument("drive_id")
    p_assign.add_argument("creature")
    p_assign.add_argument("--revision", type=int, default=None)

    p_trans = sub.add_parser("transition", help="Transition a Drive to a status")
    p_trans.add_argument("drive_id")
    p_trans.add_argument("status")
    p_trans.add_argument("--revision", type=int, default=None)
    p_trans.add_argument("--reason", default=None)

    p_del = sub.add_parser("deliveries", help="List a Drive's delivery history")
    p_del.add_argument("drive_id")

    p_replay = sub.add_parser("replay", help="Replay a dead-letter delivery")
    p_replay.add_argument("delivery_id")


def drive_cli(args: argparse.Namespace) -> int:
    """Dispatch a ``kt drive`` subcommand (sync entry over the async body)."""
    sub = getattr(args, "drive_command", None)
    if sub is None:
        print(
            "Usage: kt drive --session <id> {list|show|create|assign|transition|"
            "deliveries|replay}"
        )
        return EXIT_USAGE
    return asyncio.run(_run(args, sub))


async def _run(args: argparse.Namespace, sub: str) -> int:
    """Resume the target session and execute one Drive operation."""
    path = _resolve_session(args.session)
    if path is None:
        print(f"Session not found: {args.session}")
        return EXIT_NOT_FOUND
    studio = await Studio.resume(path)
    try:
        return await _dispatch(sub, args, studio.service)
    except DriveError as exc:
        print(_error_text(exc))
        return _exit_for(exc)
    finally:
        await studio.shutdown()


async def _resolve_graph(service: Any, requested: str | None) -> str:
    """The graph a graph-scoped op targets: the requested one, or the sole graph.

    A multi-graph session with no ``--graph`` is ambiguous and fails closed —
    never a silent ``creatures[0].graph_id`` pick.
    """
    ids = [g.graph_id for g in await service.list_graphs()]
    if requested:
        if requested not in ids:
            raise DriveError(
                f"graph {requested!r} not found; known graphs: {ids or 'none'}"
            )
        return requested
    if not ids:
        raise DriveError("resumed session has no graph to target; pass --graph <id>")
    if len(ids) > 1:
        raise DriveError(
            f"session spans multiple graphs {ids}; pass --graph <id> to choose one"
        )
    return ids[0]


async def _creature_graph(service: Any, creature_id: str) -> str:
    """The graph a creature belongs to (for creature scope + assignment target)."""
    info = await service.get_creature_info(creature_id)
    if info is None:
        raise DriveError(f"creature {creature_id!r} not found in this session")
    return info.graph_id


async def _dispatch(sub: str, args: argparse.Namespace, service: Any) -> int:
    """Dispatch a Drive subcommand against the resumed service."""
    match sub:
        case "list":
            return await _list(args, service)
        case "show":
            return await _show(args, service)
        case "create":
            return await _create(args, service)
        case "assign":
            return await _assign(args, service)
        case "transition":
            return await _transition(args, service)
        case "deliveries":
            return await _deliveries(args, service)
        case "replay":
            return await _replay(args, service)
    print(f"unknown drive subcommand: {sub}")
    return EXIT_USAGE


async def _list(args: argparse.Namespace, service: Any) -> int:
    """List Drive records across all graphs or one requested graph."""
    graph_id = await _resolve_graph(service, args.graph) if args.graph else None
    rows = await _drives.list_records(
        service,
        graph_id=graph_id,
        actor=_ACTOR,
        is_privileged=True,
        statuses=args.status,
        kinds=args.kind,
        mine=args.mine,
    )
    if args.json:
        print(json.dumps({"drives": rows}, indent=2, default=str))
    elif not rows:
        print("No drives.")
    else:
        _print_rows(rows)
    return EXIT_OK


async def _show(args: argparse.Namespace, service: Any) -> int:
    """Display one Drive record by id."""
    record = await _drives.get_record(
        service, args.drive_id, actor=_ACTOR, is_privileged=True
    )
    if record is None:
        print(f"Drive not found: {args.drive_id}")
        return EXIT_NOT_FOUND
    _emit(record, args.json)
    return EXIT_OK


async def _create(args: argparse.Namespace, service: Any) -> int:
    """Create a graph- or creature-scoped Drive record."""
    body: dict[str, Any] = {
        "kind": args.kind,
        "title": args.title,
        "scope_type": args.scope,
        "priority": args.priority,
    }
    # Creature scope targets the creature's own graph and records its id;
    # graph scope selects a graph (explicit, or the session's sole graph).
    if args.scope == "creature":
        if not args.creature:
            print("create --scope creature requires --creature <id>")
            return EXIT_USAGE
        body["scope_id"] = args.creature
        graph_id = await _creature_graph(service, args.creature)
    else:
        graph_id = await _resolve_graph(service, args.graph)
    if args.spec_json:
        try:
            body["spec"] = json.loads(args.spec_json)
        except json.JSONDecodeError as exc:
            print(f"--spec-json must be valid JSON: {exc}")
            return EXIT_USAGE
    record = await _drives.create_record(
        service,
        graph_id=graph_id,
        actor=_ACTOR,
        body=body,
        is_privileged=True,
        operator=True,
    )
    _emit(record, args.json)
    return EXIT_OK


async def _assign(args: argparse.Namespace, service: Any) -> int:
    """Assign a Drive with optimistic revision checking."""
    if args.revision is None:
        print(_needs_revision("assign"))
        return EXIT_USAGE
    # The assignee's graph comes from the creature itself, not an arbitrary pick.
    assignee_graph_id = await _creature_graph(service, args.creature)
    record = await _drives.assign_record(
        service,
        args.drive_id,
        assignee_creature_id=args.creature,
        assignee_graph_id=assignee_graph_id,
        expected_revision=args.revision,
        actor=_ACTOR,
        is_privileged=True,
        operator=True,
    )
    _emit(record, args.json)
    return EXIT_OK


async def _transition(args: argparse.Namespace, service: Any) -> int:
    """Transition a Drive with optimistic revision checking."""
    if args.revision is None:
        print(_needs_revision("transition"))
        return EXIT_USAGE
    record = await _drives.transition_record(
        service,
        args.drive_id,
        target_status=args.status,
        expected_revision=args.revision,
        actor=_ACTOR,
        status_reason=args.reason,
        is_privileged=True,
    )
    _emit(record, args.json)
    return EXIT_OK


async def _deliveries(args: argparse.Namespace, service: Any) -> int:
    """List delivery history for a globally addressed Drive."""
    # Delivery lookup needs no graph binding, but still enforces actor privileges.
    deliveries = await _drives.list_deliveries_records(
        service, args.drive_id, actor=_ACTOR, is_privileged=True
    )
    if args.json:
        print(json.dumps({"deliveries": deliveries}, indent=2, default=str))
    elif not deliveries:
        print("No deliveries.")
    else:
        for d in deliveries:
            print(
                f"  {d['delivery_id']}  {d['state']:<12} {d['reason']:<16} "
                f"attempt={d['attempt']} rev={d['drive_revision']}"
            )
    return EXIT_OK


async def _replay(args: argparse.Namespace, service: Any) -> int:
    """Replay one dead-letter delivery."""
    delivery = await _drives.replay_delivery_record(
        service, args.delivery_id, actor=_ACTOR, is_privileged=True
    )
    _emit(delivery, args.json)
    return EXIT_OK


def _emit(record: dict[str, Any], as_json: bool) -> None:
    """Render a record as JSON or terminal detail rows."""
    if as_json:
        print(json.dumps(record, indent=2, default=str))
    else:
        _print_detail(record)


def _print_rows(rows: list[dict[str, Any]]) -> None:
    """Print the compact Drive table."""
    header = f"{'ID':<24} {'STATUS':<10} {'REV':>4}  {'KIND':<10} TITLE"
    print(header)
    print("-" * len(header))
    for r in rows:
        print(
            f"{r['drive_id']:<24} {r['status']:<10} {r['revision']:>4}  "
            f"{r['kind']:<10} {r['title']}"
        )


def _print_detail(record: dict[str, Any]) -> None:
    """Print selected fields from a Drive record."""
    keys = (
        "drive_id",
        "title",
        "status",
        "revision",
        "kind",
        "owner",
        "assignee_creature_id",
        "scope_type",
        "scope_id",
        "availability",
        "durability",
        "priority",
        "allowed_actions",
    )
    for key in keys:
        if key in record:
            print(f"  {key:<20} {record[key]}")


def _needs_revision(op: str) -> str:
    """Format guidance for a mutation missing its expected revision."""
    return (
        f"{op} needs --revision N; run 'kt drive --session <id> show <drive_id>' "
        "to read the current revision"
    )


def _exit_for(exc: DriveError) -> int:
    """Map Drive errors to stable CLI exit codes."""
    if isinstance(exc, (DriveConflictError, DriveIdempotencyConflictError)):
        return EXIT_CONFLICT
    if isinstance(exc, DriveNotFoundError):
        return EXIT_NOT_FOUND
    if isinstance(exc, DrivePermissionError):
        return EXIT_PERMISSION
    return EXIT_ERROR


def _error_text(exc: DriveError) -> str:
    """Format a Drive error with actionable retry guidance."""
    if isinstance(exc, (DriveConflictError, DriveIdempotencyConflictError)):
        return f"conflict: {exc}; refetch the record (its revision moved) and retry"
    if isinstance(exc, DrivePermissionError):
        return f"permission denied: {exc}"
    if isinstance(exc, DriveNotFoundError):
        return f"not found: {exc}"
    return f"error: {exc}"


__all__ = ["add_drive_subparser", "drive_cli"]
