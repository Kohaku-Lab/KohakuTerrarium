"""Graph merge / split / fork Drive row movement (design §6.6-6.8; Phase F).

A Drive is graph-scoped, so when the engine's topology changes its rows must
follow the graph — logically, with :meth:`export_rows` / :meth:`import_rows`
(the Phase B move seam), never remapping a ``drive_id``. Ownership stays exactly
one canonical graph repository per Drive.

Sync/async bridge: the engine's merge/split coordination
(:mod:`terrarium.session_coord`, :mod:`terrarium.channel_lifecycle`) is
synchronous, but Drive repositories are async and, on Windows, a session-backed
repo's sqlite connection is closed when its store closes (WinError 32). So the
coordinator STASHES a cheap synchronous capture (an ephemeral repo reference, or
a persistent store's file path — the file survives a store close, only a delete
would remove it) on the engine, and the engine's async topology method drains it
afterwards: reopening the persistent file if needed, exporting, and importing
into the destination graph(s).

- Merge (§6.6): rows from every source graph move into the survivor's repository
  transactionally; a duplicate ``drive_id`` across sources is a hard integrity
  error; dispatcher leases are stripped so the survivor re-claims.
- Split (§6.7): placement per :func:`select_split_placement` — creature-scoped
  follows its creature, assigned follows the assignee's child, unassigned follows
  ``split_policy`` (largest_component / anchor / orphan / clone). Clone mints new
  ids with ``parent_drive_id`` lineage; a now-cross-graph dependency blocks.
- Fork (§6.8): a conversation fork carries ZERO Drives by default (see
  :func:`fork_carries_no_drives` / :mod:`session.store_fork`); explicit Drive
  cloning is unsupported in v1.
"""

from dataclasses import dataclass, replace
from typing import Any

from kohakuterrarium.terrarium.drive.errors import DriveError, DriveValidationError
from kohakuterrarium.terrarium.drive.models import DriveStatus
from kohakuterrarium.terrarium.drive.policy import (
    GraphComponent,
    select_split_placement,
)
from kohakuterrarium.terrarium.drive.split_intent import (
    complete_split_intent,
    write_split_intent,
)
from kohakuterrarium.terrarium.drive.store import SqliteDriveRepository
from kohakuterrarium.terrarium.drive.wire import (
    WIRE_SCHEMA_VERSION,
    pack_drive_assignment,
    pack_drive_record,
    unpack_drive_assignment,
    unpack_drive_record,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_ROW_KEYS = (
    "drives",
    "assignments",
    "deliveries",
    "audit",
    "progress",
    "outbox",
    "dead_letters",
    # Idempotency travels with a merge so a retry after movement still replays
    # the original result; it is (actor, key)-scoped, not per-drive (R1-11).
    "idempotency",
    # Pending terminal proposals travel with their parent Drive so a merge/split
    # never silently drops an awaiting approval; per-drive on split (R1-08).
    "proposals",
)

# An unassigned graph-scoped Drive placed by the ``orphan`` split policy (and a
# Drive whose creature/assignee did not survive the split) lands in the largest
# child as its sole canonical owner, but BLOCKED with this reason so it cannot be
# delivered until an actor intervenes (§6.7 / §16 invariant 8 — an explicit
# orphaned state, never silently-continued pursuit).
ORPHAN_BLOCK_REASON = "split_orphaned: administrator action required"


# ---------------------------------------------------------------------------
# synchronous capture (runs inside the sync coordinator, before stores close)
# ---------------------------------------------------------------------------


@dataclass
class _DriveSource:
    """A capture of one graph's Drive rows, exportable after its store closes.

    ``memory_repo`` for an ephemeral graph (never closed); ``store_path`` for a
    persistent one (the file survives its store's close, so the drain reopens it).
    """

    gid: str
    memory_repo: Any = None
    store_path: str | None = None

    async def export(self) -> dict[str, Any]:
        if self.memory_repo is not None:
            return await self.memory_repo.export_rows()
        repo = SqliteDriveRepository(self.store_path)
        try:
            return await repo.export_rows()
        finally:
            repo.close_blocking()


def _capture(runtime: Any, gid: str) -> _DriveSource | None:
    manager = runtime.registry.peek(gid)
    if manager is None:
        return None
    repo = manager.repository
    if getattr(repo, "durability", "ephemeral") == "ephemeral":
        return _DriveSource(gid=gid, memory_repo=repo)
    path = getattr(repo, "_path", None)
    if not path:
        return None
    return _DriveSource(gid=gid, store_path=str(path))


def _enqueue(engine: Any, entry: tuple) -> None:
    # A queue (not a single slot): every engine topology method drains right
    # after its sync op, but an un-drained stash (e.g. an ``ensure_same_graph``
    # merge from cross-graph output wiring) must not be silently overwritten.
    pending = getattr(engine, "_pending_drive_topology", None)
    if not pending:
        pending = []
        engine._pending_drive_topology = pending
    pending.append(entry)


def stash_merge(engine: Any, keep_gid: str, source_gids: list[str]) -> None:
    """Record a pending Drive merge (sync; from ``session_coord.apply_merge``)."""
    runtime = getattr(engine, "_drive_runtime", None)
    if runtime is None:
        return
    sources = {}
    for gid in source_gids:
        captured = _capture(runtime, gid)
        if captured is not None:
            sources[gid] = captured
    if sources:
        _enqueue(engine, ("merge", keep_gid, sources))


def stash_split(engine: Any, parent_gid: str, child_gids: list[str]) -> None:
    """Record a pending Drive split (sync; from ``session_coord.apply_split``)."""
    runtime = getattr(engine, "_drive_runtime", None)
    if runtime is None:
        return
    captured = _capture(runtime, parent_gid)
    if captured is not None:
        _enqueue(engine, ("split", parent_gid, list(child_gids), captured))


# ---------------------------------------------------------------------------
# async drain (runs from the engine's async topology method, after the sync op)
# ---------------------------------------------------------------------------


async def drain(runtime: Any) -> None:
    """Apply every pending Drive topology op stashed on the engine, in order.

    Each op first drains (cancel + await) the in-flight ``_reconcile_when_ready``
    tasks of every graph whose repository it is about to rebind or close, so a
    reconcile never starts a manager on a repository being torn down (mirrors
    ``engine.detach_graph``; design §6.4/§6.6-6.7). The sync coordinator ran with
    no ``await`` between closing the superseded stores and this drain, so those
    tasks are still suspended here and are cancelled before they can resume."""
    engine = runtime._engine
    pending = getattr(engine, "_pending_drive_topology", None)
    if not pending:
        return
    engine._pending_drive_topology = None
    for entry in pending:
        if entry[0] == "merge":
            keep_gid, sources = entry[1], entry[2]
            await runtime._drain_reconcile(keep_gid)
            for gid in sources:
                await runtime._drain_reconcile(gid)
            await _apply_merge(runtime, keep_gid, sources)
        elif entry[0] == "split":
            await runtime._drain_reconcile(entry[1])
            await _apply_split(runtime, entry[1], entry[2], entry[3])


async def _apply_merge(
    runtime: Any, keep_gid: str, sources: dict[str, _DriveSource]
) -> None:
    registry = runtime.registry
    engine = runtime._engine
    payloads = [await src.export() for src in sources.values()]
    merged = _combine(payloads)
    if not merged["drives"]:
        # No Drives to move; just drop the non-survivor source graphs. Awaited
        # stop (not fire-and-forget) so each dispatcher releases against its live
        # repo before the merge collapses its session store (design §7.1).
        for gid in sources:
            if gid != keep_gid:
                await registry.detach_and_stop(gid)
        return
    # Rehome graph-scoped records + assignments to the survivor and invalidate
    # dispatcher leases so the survivor re-claims (design §6.6).
    _rehome_payload(merged, keep_gid)
    # One provider-aware destination resolver (R1-10): an explicit provider repo
    # wins over the session store. When it resolves to the survivor's CURRENT
    # repo (a single-store provider), import in place — no close/rebind.
    new_repo, survivor_store = registry.resolve_destination_repo(keep_gid)
    old_repo = registry.repository_for(keep_gid)
    same_repo = new_repo is old_repo
    # Import BEFORE touching the survivor's live repo/manager so a movement
    # failure leaves the pre-merge state consistent (design §6.6 atomicity).
    # Append-only rows already present (the survivor's own) are deduped on import
    # so its history is not multiplied (R1-11).
    try:
        await new_repo.import_rows(merged)
    except Exception:
        if not same_repo:
            _close_if_persistent(new_repo)
        raise
    if not same_repo:
        # Close the survivor's old repo, then rebind to the merged one. The
        # survivor KEEPS its session store (no companion-closer race), so the old
        # dispatcher's stop is fire-and-forget against the already-closed repo.
        _close_if_persistent(old_repo)
        registry.rebind_repository(keep_gid, new_repo, survivor_store)
    await registry.ensure_started(keep_gid)
    # Non-survivor graphs are DROPPED and their stores collapse — AWAIT-stop each
    # so its dispatcher is down before session_coord closes its store (§7.1).
    for gid in sources:
        if gid != keep_gid:
            await registry.detach_and_stop(gid)
    await _reconcile_graph(engine, registry, keep_gid)


async def _apply_split(
    runtime: Any,
    parent_gid: str,
    child_gids: list[str],
    source: _DriveSource,
) -> None:
    registry = runtime.registry
    engine = runtime._engine
    payload = await source.export()
    records = {
        r.drive_id: r for r in (unpack_drive_record(d) for d in payload["drives"])
    }
    assignments = {
        a.drive_id: a
        for a in (unpack_drive_assignment(a) for a in payload["assignments"])
    }
    live_children = [gid for gid in child_gids if gid in engine._topology.graphs]
    per_child = {gid: _empty_payload() for gid in live_children}
    if records:
        _place_drives(engine, records, assignments, payload, live_children, per_child)
    # The (actor, key) idempotency ledger is graph-global, not drive-scoped, so
    # it is replicated to every child; a retry of a pre-split mutation still
    # returns the original result wherever its Drive landed (R1-11).
    source_idem = payload.get("idempotency", [])
    for gid in live_children:
        per_child[gid]["idempotency"] = list(source_idem)
    intent = None
    stores = getattr(engine, "_session_stores", {})
    child_sessions = {
        gid: str(stores[gid].path)
        for gid in live_children
        if gid in stores and getattr(stores[gid], "path", None)
    }
    repo_paths = {
        gid: str(getattr(registry.resolve_destination_repo(gid)[0], "_path", ""))
        for gid in live_children
    }
    if (
        source.store_path is not None
        and len(child_sessions) == len(live_children)
        and all(repo_paths.values())
    ):
        intent = write_split_intent(
            source.store_path, repo_paths, child_sessions, per_child
        )
    await _install_children_atomic(engine, registry, live_children, per_child)
    if intent is not None:
        complete_split_intent(intent)
    for gid in live_children:
        await _reconcile_graph(engine, registry, gid)


# ---------------------------------------------------------------------------
# split placement
# ---------------------------------------------------------------------------


def _place_drives(
    engine: Any,
    records: dict[str, Any],
    assignments: dict[str, Any],
    payload: dict[str, Any],
    child_gids: list[str],
    per_child: dict[str, dict[str, Any]],
) -> None:
    components = [
        GraphComponent(gid, frozenset(engine._topology.graphs[gid].creature_ids))
        for gid in child_gids
    ]
    largest = child_gids[0]
    placement_of: dict[str, str] = {}
    clones: dict[str, list[str]] = {}
    orphaned: set[str] = set()
    for drive_id, record in records.items():
        placement = select_split_placement(
            record, assignments.get(drive_id), components
        )
        if placement.kind == "follow" and placement.graph_id in per_child:
            placement_of[drive_id] = placement.graph_id
        elif placement.kind == "clone":
            clones[drive_id] = list(child_gids)
        else:
            # orphan (explicit policy, or a follow whose creature/assignee did
            # not survive the split): the largest child is the sole canonical
            # owner, but the Drive is blocked below — never silently continued.
            placement_of[drive_id] = largest
            orphaned.add(drive_id)
    block_reason: dict[str, str] = {d: ORPHAN_BLOCK_REASON for d in orphaned}
    for drive_id in _cross_graph_blocked(records, placement_of):
        block_reason.setdefault(drive_id, "cross_graph_dependency")
    for drive_id, target in placement_of.items():
        _emit_drive(
            per_child[target],
            records[drive_id],
            assignments.get(drive_id),
            target,
            payload,
            drive_id,
            block_reason=block_reason.get(drive_id),
            orphaned=drive_id in orphaned,
        )
    for drive_id, targets in clones.items():
        _emit_clones(
            engine, per_child, records[drive_id], assignments.get(drive_id), targets
        )


def _cross_graph_blocked(
    records: dict[str, Any], placement_of: dict[str, str]
) -> set[str]:
    """Drives whose dependency now lands in a different child graph (§6.7)."""
    blocked: set[str] = set()
    for drive_id, target in placement_of.items():
        for dep in records[drive_id].dependency_ids:
            if dep in placement_of and placement_of[dep] != target:
                blocked.add(drive_id)
                break
    return blocked


def _emit_drive(
    dest: dict[str, Any],
    record: Any,
    assignment: Any,
    target_gid: str,
    payload: dict[str, Any],
    drive_id: str,
    *,
    block_reason: str | None = None,
    orphaned: bool = False,
) -> None:
    rehomed = _rehome_record(record, target_gid)
    if block_reason is not None:
        rehomed = replace(
            rehomed, status=DriveStatus.BLOCKED, status_reason=block_reason
        )
    dest["drives"].append(pack_drive_record(rehomed))
    if assignment is not None:
        rehomed_assign = _rehome_assignment(assignment, target_gid)
        # An orphaned Drive that still names a (now-gone) assignee is flipped to
        # the orphaned assignment state so nothing treats it as deliverable.
        if orphaned and rehomed_assign.assignee_creature_id is not None:
            rehomed_assign = replace(rehomed_assign, assignment_state="orphaned")
        dest["assignments"].append(pack_drive_assignment(rehomed_assign))
    _copy_child_rows(payload, dest, drive_id)


def _emit_clones(
    engine: Any,
    per_child: dict[str, dict[str, Any]],
    record: Any,
    assignment: Any,
    targets: list[str],
) -> None:
    runtime = engine._drive_runtime
    for gid in targets:
        if gid not in per_child:
            continue
        new_id = runtime._id_factory()
        clone = replace(
            _rehome_record(record, gid),
            drive_id=new_id,
            metadata={**record.metadata, "parent_drive_id": record.drive_id},
        )
        per_child[gid]["drives"].append(pack_drive_record(clone))
        if assignment is not None:
            per_child[gid]["assignments"].append(
                pack_drive_assignment(
                    replace(_rehome_assignment(assignment, gid), drive_id=new_id)
                )
            )


def _rehome_record(record: Any, target_gid: str) -> Any:
    if record.scope_type == "graph":
        return replace(record, scope_id=target_gid)
    return record


def _rehome_assignment(assignment: Any, target_gid: str) -> Any:
    return replace(
        assignment,
        assignee_graph_id=target_gid,
        lease_owner=None,
        lease_expires_at=None,
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _empty_payload() -> dict[str, Any]:
    payload: dict[str, Any] = {"wire_schema": WIRE_SCHEMA_VERSION}
    for key in _ROW_KEYS:
        payload[key] = []
    return payload


def _combine(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    combined = _empty_payload()
    seen: set[str] = set()
    for payload in payloads:
        for entry in payload.get("drives", []):
            drive_id = unpack_drive_record(entry).drive_id
            if drive_id in seen:
                raise DriveError(
                    f"duplicate drive_id {drive_id!r} across merged graphs — "
                    "Drive ids are globally unique and are never remapped (§6.6)"
                )
            seen.add(drive_id)
        for key in _ROW_KEYS:
            combined[key].extend(payload.get(key, []))
    return combined


def _copy_child_rows(
    payload: dict[str, Any], dest: dict[str, Any], drive_id: str
) -> None:
    for key in (
        "deliveries",
        "progress",
        "audit",
        "outbox",
        "dead_letters",
        "proposals",
    ):
        for entry in payload.get(key, []):
            if _entry_drive_id(entry) == drive_id:
                dest[key].append(entry)


def _entry_drive_id(entry: dict[str, Any]) -> str | None:
    data = entry.get("data", entry) if isinstance(entry, dict) else {}
    return data.get("drive_id") or entry.get("drive_id")


def _rehome_payload(payload: dict[str, Any], target_gid: str) -> None:
    """Rewrite a combined payload so graph-scoped records + assignments belong
    to ``target_gid`` and carry no dispatcher lease (merge, §6.6)."""
    payload["drives"] = [
        pack_drive_record(_rehome_record(unpack_drive_record(d), target_gid))
        for d in payload.get("drives", [])
    ]
    payload["assignments"] = [
        pack_drive_assignment(
            _rehome_assignment(unpack_drive_assignment(a), target_gid)
        )
        for a in payload.get("assignments", [])
    ]


def _close_if_persistent(repo: Any) -> None:
    close = getattr(repo, "close_blocking", None)
    if callable(close):
        close()


async def _install_children_atomic(
    engine: Any,
    registry: Any,
    gids: list[str],
    payloads: dict[str, dict[str, Any]],
) -> None:
    """Stage all child rows before publishing any repository binding."""
    destinations: list[tuple[str, Any, Any, Any, dict[str, Any]]] = []
    for gid in gids:
        repo, store = registry.resolve_destination_repo(gid)
        old_repo = registry.repository_for(gid)
        backup = await repo.export_rows()
        destinations.append((gid, repo, store, old_repo, backup))
    touched: list[tuple[str, Any, Any, Any, dict[str, Any]]] = []
    try:
        for item in destinations:
            gid, repo, _, _, _ = item
            touched.append(item)
            await repo.replace_rows(payloads[gid])
    except Exception as install_error:
        rollback_errors: list[Exception] = []
        for _, repo, _, _, backup in reversed(touched):
            try:
                await repo.replace_rows(backup)
            except Exception as rollback_error:
                rollback_errors.append(rollback_error)
        for _, repo, _, old_repo, _ in destinations:
            if repo is not old_repo:
                _close_if_persistent(repo)
        if rollback_errors:
            raise RuntimeError(
                "Drive split failed and one or more destinations could not be restored"
            ) from install_error
        raise
    for gid, repo, store, old_repo, _ in destinations:
        if repo is not old_repo:
            _close_if_persistent(old_repo)
            registry.rebind_repository(gid, repo, store)
        await registry.ensure_started(gid)


async def _reconcile_graph(engine: Any, registry: Any, gid: str) -> None:
    manager = registry.peek(gid)
    if manager is None:
        return
    graph = engine._topology.graphs.get(gid)
    if graph is None:
        return
    # Reconcile per running, restoration-ready creature so redelivery still
    # respects the §6.5 barrier. A warm-paused creature (UXI-11) is skipped
    # too — redelivering to it would only re-hit the paused admission gate.
    for cid in graph.creature_ids:
        creature = engine._creatures.get(cid)
        if creature is None or not creature.is_running:
            continue
        if getattr(creature, "paused", False):
            continue
        if not getattr(creature, "restoration_ready", True):
            continue
        await manager.reconcile(creature_id=cid)


# ---------------------------------------------------------------------------
# session fork (design §6.8)
# ---------------------------------------------------------------------------


def fork_carries_no_drives() -> bool:
    """A conversation fork carries ZERO active Drives by default (§6.8).

    The Drive tables are a separate sqlite surface that ``session.store_fork``
    does not copy (it is a logical KVault row copy) and explicitly purges, so a
    forked session opens with no Drives — two branches can never mutate one
    commitment. This predicate documents the invariant for callers/tests."""
    return True


def clone_drives_for_fork(*_args: Any, **_kwargs: Any) -> None:
    """Explicit ``fork_drives=True`` Drive cloning — unsupported in v1 (§6.8)."""
    raise DriveValidationError(
        "forking a session's Drives (fork_drives=True) is not supported in v1; "
        "a conversation fork carries zero Drives by design (§6.8)"
    )


__all__ = [
    "clone_drives_for_fork",
    "drain",
    "fork_carries_no_drives",
    "stash_merge",
    "stash_split",
]
