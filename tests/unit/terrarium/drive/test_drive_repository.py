"""Behaviour contract for the Drive repository (design §4, §7).

The mutation/idempotency/CAS/delivery/query suite runs against BOTH backends
(``MemoryDriveRepository`` and ``SqliteDriveRepository``) through the ``repo``
fixture so drift between them fails a test. Pure builders, wire helpers, and
query predicates from ``drive_repository`` are unit-tested directly.
Persistence-only concerns (close/reopen, crash, lazy tables) live in
``test_drive_store.py``.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from kohakuterrarium.terrarium.drive.errors import (
    DriveConflictError,
    DriveDeliveryError,
    DriveIdempotencyConflictError,
    DriveNotFoundError,
    DriveTransitionError,
    DriveValidationError,
)
from kohakuterrarium.terrarium.drive.memory import MemoryDriveRepository
from kohakuterrarium.terrarium.drive.models import ActorRef, DriveStatus
from kohakuterrarium.terrarium.drive.repository import (
    DriveDeadLetter,
    DriveOutboxEntry,
    Mutation,
    build_create,
    dead_letter_from_wire,
    dead_letter_to_wire,
    in_graph,
    matches_query,
    outbox_from_wire,
    outbox_to_wire,
    require_revision,
)
from kohakuterrarium.terrarium.drive.requests import (
    CreateDriveRequest,
    DrivePatch,
    DriveQuery,
)
from kohakuterrarium.terrarium.drive.store import SqliteDriveRepository

WORKER = ActorRef("creature", "worker")
OTHER = ActorRef("creature", "other")
USER = ActorRef("user", "alice")


class Clock:
    def __init__(self) -> None:
        self.t = datetime(2026, 7, 11, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


def _ids():
    n = [0]

    def mint() -> str:
        n[0] += 1
        return f"id{n[0]:05d}"

    return mint


@pytest.fixture(params=["memory", "sqlite"])
def repo(request, tmp_path):
    clock = Clock()
    if request.param == "memory":
        r = MemoryDriveRepository(clock=clock, id_factory=_ids())
    else:
        r = SqliteDriveRepository(
            str(tmp_path / "d.sqlite"), clock=clock, id_factory=_ids()
        )
    r.clock = clock
    yield r
    close = getattr(r, "close_blocking", None)
    if close is not None:
        close()


def _req(**over) -> CreateDriveRequest:
    base = dict(
        kind="generic",
        title="watch",
        scope_type="creature",
        scope_id="worker",
        owner=WORKER,
        owner_scope="creature",
        created_by=WORKER,
    )
    base.update(over)
    return CreateDriveRequest(**base)


async def _make(repo, **over) -> str:
    rec = await repo.create_drive(_req(**over), actor=WORKER, graph_id="g1")
    return rec.drive_id


# ── creation ──────────────────────────────────────────────────────


class TestCreate:
    async def test_creates_drive_assignment_audit_outbox(self, repo):
        rec = await repo.create_drive(_req(), actor=WORKER, graph_id="g1")
        assert rec.revision == 1 and rec.lifecycle_epoch == 0
        assert rec.status is DriveStatus.ACTIVE
        assignment = await repo.get_assignment(rec.drive_id)
        assert assignment.assignee_creature_id == "worker"
        assert assignment.assignment_state == "assigned"
        assert assignment.assignee_graph_id == "g1"
        audit = await repo.list_audit(rec.drive_id)
        assert [a.operation for a in audit] == ["create"]
        assert audit[0].after_status is DriveStatus.ACTIVE
        outbox = await repo.list_outbox()
        assert [e.kind for e in outbox] == ["drive_created"]

    async def test_graph_scope_unassigned_by_default(self, repo):
        rec = await repo.create_drive(
            _req(scope_type="graph", scope_id="g1"), actor=WORKER, graph_id="g1"
        )
        assignment = await repo.get_assignment(rec.drive_id)
        assert assignment.assignment_state == "unassigned"
        assert assignment.assignee_creature_id is None

    async def test_graph_scope_with_assignee(self, repo):
        rec = await repo.create_drive(
            _req(scope_type="graph", scope_id="g1", assignee_creature_id="worker"),
            actor=WORKER,
            graph_id="g1",
        )
        assignment = await repo.get_assignment(rec.drive_id)
        assert assignment.assignment_state == "assigned"
        assert assignment.assignee_creature_id == "worker"

    async def test_graph_scope_id_must_match_graph(self, repo):
        with pytest.raises(DriveValidationError):
            await repo.create_drive(
                _req(scope_type="graph", scope_id="other"), actor=WORKER, graph_id="g1"
            )

    async def test_draft_status_when_requested(self, repo):
        rec = await repo.create_drive(
            _req(), actor=WORKER, graph_id="g1", initial_status=DriveStatus.DRAFT
        )
        assert rec.status is DriveStatus.DRAFT

    async def test_drive_id_is_short_kind_prefixed_and_preserves_other_ids(self, repo):
        rec = await repo.create_drive(_req(kind="goal"), actor=WORKER, graph_id="g1")
        assert rec.drive_id.startswith("goal-")
        assert len(rec.drive_id) == len("goal-") + 6
        audit = await repo.list_audit(rec.drive_id)
        outbox = await repo.list_outbox()
        assert audit[0].audit_id.startswith("id")
        assert outbox[0].outbox_id.startswith("id")

    async def test_drive_id_collision_retries_inside_create_transaction(
        self, repo, monkeypatch
    ):
        monkeypatch.setattr(repo, "_new_drive_id", lambda kind: f"{kind}-repeat")
        first = await repo.create_drive(_req(), actor=WORKER, graph_id="g1")
        ids = iter(("generic-repeat", "generic-fresh"))
        monkeypatch.setattr(repo, "_new_drive_id", lambda kind: next(ids))
        second = await repo.create_drive(_req(), actor=WORKER, graph_id="g1")
        assert first.drive_id == "generic-repeat"
        assert second.drive_id == "generic-fresh"


# ── update + CAS ──────────────────────────────────────────────────


class TestUpdate:
    async def test_patch_applies_and_bumps_revision(self, repo):
        did = await _make(repo)
        rec = await repo.update_drive(
            did, DrivePatch(title="t2", priority=5), expected_revision=1, actor=WORKER
        )
        assert rec.revision == 2 and rec.title == "t2" and rec.priority == 5
        audit = await repo.list_audit(did)
        assert [a.operation for a in audit] == ["create", "update"]

    async def test_stale_revision_conflicts(self, repo):
        did = await _make(repo)
        with pytest.raises(DriveConflictError):
            await repo.update_drive(
                did, DrivePatch(title="x"), expected_revision=99, actor=WORKER
            )
        # nothing persisted: revision unchanged, no extra audit row
        rec = await repo.get(did)
        assert rec.revision == 1
        assert [a.operation for a in await repo.list_audit(did)] == ["create"]

    async def test_missing_drive_raises(self, repo):
        with pytest.raises(DriveNotFoundError):
            await repo.update_drive(
                "nope", DrivePatch(title="x"), expected_revision=1, actor=WORKER
            )

    async def test_concurrent_same_revision_one_loser(self, repo):
        did = await _make(repo)

        async def upd():
            return await repo.update_drive(
                did, DrivePatch(title="c"), expected_revision=1, actor=WORKER
            )

        results = await asyncio.gather(upd(), upd(), return_exceptions=True)
        losers = [r for r in results if isinstance(r, DriveConflictError)]
        winners = [r for r in results if not isinstance(r, Exception)]
        assert len(losers) == 1 and len(winners) == 1
        rec = await repo.get(did)
        assert rec.revision == 2  # advanced exactly once


# ── mutation isolation (R1-07) ────────────────────────────────────


class TestMutationIsolation:
    """A stored record's nested mutable payloads must not alias caller state.

    ``DriveRecord`` is a frozen dataclass holding mutable dicts; a caller that
    mutates ``spec``/``metadata``/``presentation``/``policy_options`` in place
    on a fetched record must not be able to change canonical state without a
    revision/audit. Both backends must expose identical isolation.
    """

    async def test_mutating_fetched_record_does_not_change_canonical_state(self, repo):
        did = await _make(
            repo,
            spec={"nested": {"k": "v"}},
            metadata={"m": [1, 2]},
            presentation={"p": "x"},
            policy_options={"o": {"deep": 1}},
        )
        fetched = await repo.get(did)
        fetched.spec["nested"]["k"] = "hacked"
        fetched.spec["added"] = True
        fetched.metadata["m"].append(99)
        fetched.presentation["p"] = "tampered"
        fetched.policy_options["o"]["deep"] = 999

        again = await repo.get(did)
        assert again.spec == {"nested": {"k": "v"}}
        assert again.metadata == {"m": [1, 2]}
        assert again.presentation == {"p": "x"}
        assert again.policy_options == {"o": {"deep": 1}}
        assert again.revision == 1
        assert [a.operation for a in await repo.list_audit(did)] == ["create"]

    async def test_two_fetches_are_independent(self, repo):
        did = await _make(repo, spec={"count": [0]})
        first = await repo.get(did)
        first.spec["count"].append(1)
        second = await repo.get(did)
        assert second.spec == {"count": [0]}

    async def test_mutating_request_after_create_does_not_leak(self, repo):
        spec = {"live": {"x": 1}}
        rec = await repo.create_drive(_req(spec=spec), actor=WORKER, graph_id="g1")
        spec["live"]["x"] = 2
        spec["injected"] = True
        again = await repo.get(rec.drive_id)
        assert again.spec == {"live": {"x": 1}}

    async def test_progress_evidence_isolated(self, repo):
        did = await _make(repo)
        await repo.report_progress(
            did, summary="s", evidence={"e": {"n": 1}}, actor=WORKER
        )
        rows = await repo.list_progress(did)
        rows[0].evidence["e"]["n"] = 999
        again = await repo.list_progress(did)
        assert again[0].evidence == {"e": {"n": 1}}


# ── idempotency ───────────────────────────────────────────────────


class TestIdempotency:
    async def test_same_key_same_op_returns_original_one_audit(self, repo):
        req = _req(idempotency_key="k1")
        rec1 = await repo.create_drive(req, actor=WORKER, graph_id="g1")
        rec2 = await repo.create_drive(req, actor=WORKER, graph_id="g1")
        assert rec1.drive_id == rec2.drive_id
        # exactly ONE drive and one audit row despite two calls
        assert len(await repo.list_drives(DriveQuery())) == 1
        assert len(await repo.list_audit(rec1.drive_id)) == 1

    async def test_same_key_different_op_conflicts(self, repo):
        await repo.create_drive(_req(idempotency_key="k1"), actor=WORKER, graph_id="g1")
        with pytest.raises(DriveIdempotencyConflictError):
            await repo.create_drive(
                _req(title="DIFFERENT", idempotency_key="k1"),
                actor=WORKER,
                graph_id="g1",
            )

    async def test_idempotency_is_per_actor(self, repo):
        # The ledger is keyed on (actor, key): the same key from two actors is
        # two independent operations, not a collision.
        await repo.create_drive(
            _req(idempotency_key="shared"), actor=WORKER, graph_id="g1"
        )
        await repo.create_drive(
            _req(
                idempotency_key="shared",
                scope_type="graph",
                scope_id="g1",
                owner=OTHER,
                owner_scope="graph",
                created_by=OTHER,
            ),
            actor=OTHER,
            graph_id="g1",
        )
        assert len(await repo.list_drives(DriveQuery())) == 2

    async def test_idempotent_update_replays_result(self, repo):
        did = await _make(repo)
        r1 = await repo.update_drive(
            did,
            DrivePatch(title="z"),
            expected_revision=1,
            actor=WORKER,
            idempotency_key="u1",
        )
        r2 = await repo.update_drive(
            did,
            DrivePatch(title="z"),
            expected_revision=1,
            actor=WORKER,
            idempotency_key="u1",
        )
        assert r1.revision == r2.revision == 2
        # replay must not double-apply: still exactly one update audit row
        assert [a.operation for a in await repo.list_audit(did)] == ["create", "update"]


# ── assignment / reassignment ─────────────────────────────────────


class TestAssignment:
    async def test_reassign_bumps_epoch_and_supersedes_deliveries(self, repo):
        did = await repo.create_drive(
            _req(scope_type="graph", scope_id="g1", assignee_creature_id="worker"),
            actor=USER,
            graph_id="g1",
        )
        did = did.drive_id
        d = await repo.enqueue_delivery(did, reason="ready")
        rec = await repo.assign_drive(
            did,
            assignee_creature_id="other",
            assignee_graph_id="g1",
            expected_revision=1,
            actor=USER,
        )
        assert rec.lifecycle_epoch == 1 and rec.revision == 2
        assignment = await repo.get_assignment(did)
        assert assignment.assignee_creature_id == "other"
        # the old pending delivery was superseded, not left live
        delivery = (await repo.list_deliveries(did))[0]
        assert delivery.delivery_id == d.delivery_id
        assert delivery.state == "superseded"

    async def test_superseded_delivery_not_reclaimable(self, repo):
        rec = await repo.create_drive(
            _req(scope_type="graph", scope_id="g1", assignee_creature_id="worker"),
            actor=USER,
            graph_id="g1",
        )
        did = rec.drive_id
        await repo.enqueue_delivery(did, reason="ready")
        await repo.claim_deliveries("disp1", 10, lease_seconds=30)
        # reassignment supersedes the in-flight delivery
        await repo.assign_drive(
            did,
            assignee_creature_id="other",
            assignee_graph_id="g1",
            expected_revision=1,
            actor=USER,
        )
        # the superseded delivery must never be handed out again
        assert await repo.claim_deliveries("disp2", 10, lease_seconds=30) == ()
        assert (await repo.list_deliveries(did))[0].state == "superseded"

    async def test_unassign_graph_scope(self, repo):
        rec = await repo.create_drive(
            _req(scope_type="graph", scope_id="g1", assignee_creature_id="worker"),
            actor=USER,
            graph_id="g1",
        )
        out = await repo.unassign_drive(rec.drive_id, expected_revision=1, actor=USER)
        assignment = await repo.get_assignment(rec.drive_id)
        assert out.lifecycle_epoch == 1
        assert assignment.assignment_state == "unassigned"
        assert assignment.assignee_creature_id is None

    async def test_creature_scope_cannot_unassign(self, repo):
        did = await _make(repo)
        with pytest.raises(DriveValidationError):
            await repo.unassign_drive(did, expected_revision=1, actor=WORKER)

    async def test_creature_scope_assignee_fixed(self, repo):
        did = await _make(repo)
        with pytest.raises(DriveValidationError):
            await repo.assign_drive(
                did,
                assignee_creature_id="other",
                assignee_graph_id="g1",
                expected_revision=1,
                actor=WORKER,
            )


# ── transitions ───────────────────────────────────────────────────


class TestTransition:
    async def test_legal_transition(self, repo):
        did = await _make(repo)
        rec = await repo.transition_drive(
            did, DriveStatus.WAITING, expected_revision=1, actor=WORKER
        )
        assert rec.status is DriveStatus.WAITING and rec.revision == 2

    async def test_illegal_transition_rejected(self, repo):
        did = await _make(repo)
        with pytest.raises(DriveTransitionError):
            await repo.transition_drive(
                did, DriveStatus.RETIRED, expected_revision=1, actor=WORKER
            )
        assert (await repo.get(did)).status is DriveStatus.ACTIVE

    async def test_terminal_evidence_recorded(self, repo):
        did = await _make(repo)
        rec = await repo.transition_drive(
            did,
            DriveStatus.COMPLETED,
            expected_revision=1,
            actor=WORKER,
            terminal_evidence={"proof": "done"},
        )
        assert rec.status is DriveStatus.COMPLETED
        assert rec.terminal_evidence == {"proof": "done"}

    async def test_retire_from_terminal_preserves_rows(self, repo):
        did = await _make(repo)
        await repo.transition_drive(
            did, DriveStatus.COMPLETED, expected_revision=1, actor=WORKER
        )
        rec = await repo.retire_drive(did, expected_revision=2, actor=WORKER)
        assert rec.status is DriveStatus.RETIRED
        # audit chain preserved (nothing deleted)
        ops = [a.operation for a in await repo.list_audit(did)]
        assert ops == ["create", "transition", "retire"]

    async def test_extra_transitions_allow_reopen(self, repo):
        did = await _make(repo)
        await repo.transition_drive(
            did, DriveStatus.COMPLETED, expected_revision=1, actor=WORKER
        )
        extra = frozenset({(DriveStatus.COMPLETED, DriveStatus.ACTIVE)})
        rec = await repo.transition_drive(
            did,
            DriveStatus.ACTIVE,
            expected_revision=2,
            actor=WORKER,
            extra_transitions=extra,
        )
        assert rec.status is DriveStatus.ACTIVE


# ── progress (append-only) ────────────────────────────────────────


class TestProgress:
    async def test_progress_no_revision_change(self, repo):
        did = await _make(repo)
        p = await repo.report_progress(
            did, summary="halfway", evidence={"pct": 50}, actor=WORKER
        )
        rec = await repo.get(did)
        assert rec.revision == 1  # unchanged
        progresses = await repo.list_progress(did)
        assert [x.summary for x in progresses] == ["halfway"]
        assert p.evidence == {"pct": 50}
        # audited at the current revision, no drive row rewrite
        ops = [a.operation for a in await repo.list_audit(did)]
        assert ops == ["create", "report_progress"]


# ── deliveries: enqueue / claim / mark / purge ────────────────────


class TestDelivery:
    async def test_enqueue_creates_pending_and_outbox(self, repo):
        did = await _make(repo)
        d = await repo.enqueue_delivery(did, reason="ready")
        assert d.state == "pending" and d.attempt == 0
        kinds = [e.kind for e in await repo.list_outbox()]
        assert "drive_ready" in kinds

    async def test_enqueue_unassigned_rejected(self, repo):
        rec = await repo.create_drive(
            _req(scope_type="graph", scope_id="g1"), actor=USER, graph_id="g1"
        )
        with pytest.raises(DriveValidationError):
            await repo.enqueue_delivery(rec.drive_id, reason="ready")

    async def test_claim_sets_lease_on_assignment(self, repo):
        did = await _make(repo)
        await repo.enqueue_delivery(did, reason="ready")
        claimed = await repo.claim_deliveries("disp1", 10, lease_seconds=30)
        assert len(claimed) == 1 and claimed[0].state == "claimed"
        assignment = await repo.get_assignment(did)
        assert assignment.lease_owner == "disp1"

    async def test_unexpired_lease_not_reclaimable_by_other(self, repo):
        did = await _make(repo)
        await repo.enqueue_delivery(did, reason="ready")
        await repo.claim_deliveries("disp1", 10, lease_seconds=30)
        again = await repo.claim_deliveries("disp2", 10, lease_seconds=30)
        assert again == ()

    async def test_expired_lease_reclaimable(self, repo):
        did = await _make(repo)
        await repo.enqueue_delivery(did, reason="ready")
        await repo.claim_deliveries("disp1", 10, lease_seconds=30)
        repo.clock.advance(60)
        again = await repo.claim_deliveries("disp2", 10, lease_seconds=30)
        assert len(again) == 1
        assert (await repo.get_assignment(did)).lease_owner == "disp2"

    async def test_future_available_at_not_claimed(self, repo):
        did = await _make(repo)
        future = repo.clock() + timedelta(seconds=60)
        await repo.enqueue_delivery(did, reason="retry", available_at=future)
        assert await repo.claim_deliveries("disp1", 10, lease_seconds=30) == ()
        repo.clock.advance(120)
        assert len(await repo.claim_deliveries("disp1", 10, lease_seconds=30)) == 1

    async def test_mark_states(self, repo):
        did = await _make(repo)
        d = await repo.enqueue_delivery(did, reason="ready")
        adm = await repo.mark_delivery(d.delivery_id, "admitted")
        assert adm.state == "admitted" and adm.admitted_at is not None
        ack = await repo.mark_delivery(d.delivery_id, "acknowledged")
        assert ack.state == "acknowledged" and ack.acknowledged_at is not None

    async def test_retry_wait_increments_attempt(self, repo):
        did = await _make(repo)
        d = await repo.enqueue_delivery(did, reason="ready")
        future = repo.clock() + timedelta(seconds=10)
        r = await repo.mark_delivery(
            d.delivery_id, "retry_wait", error="boom", available_at=future
        )
        assert r.state == "retry_wait" and r.attempt == 1 and r.last_error == "boom"

    async def test_dead_letter_writes_snapshot_and_outbox(self, repo):
        did = await _make(repo)
        d = await repo.enqueue_delivery(did, reason="ready")
        await repo.mark_delivery(
            d.delivery_id, "dead_letter", error="fatal", reason="exhausted"
        )
        # a dead-letter structural event lands in the outbox
        kinds = [e.kind for e in await repo.list_outbox()]
        assert "drive_dead_lettered" in kinds

    async def test_mark_missing_delivery_raises(self, repo):
        with pytest.raises(DriveDeliveryError):
            await repo.mark_delivery("nope", "admitted")

    async def test_purge_keeps_live_drive_deliveries(self, repo):
        did = await _make(repo)  # ACTIVE (nonterminal)
        d = await repo.enqueue_delivery(did, reason="ready")
        await repo.mark_delivery(d.delivery_id, "acknowledged")
        repo.clock.advance(10_000)
        removed = await repo.purge_deliveries(
            acknowledged_seconds=1, superseded_seconds=1
        )
        assert removed == 0
        assert len(await repo.list_deliveries(did)) == 1

    async def test_purge_removes_terminal_drive_old_deliveries(self, repo):
        did = await _make(repo)
        d = await repo.enqueue_delivery(did, reason="ready")
        await repo.mark_delivery(d.delivery_id, "acknowledged")
        await repo.transition_drive(
            did, DriveStatus.COMPLETED, expected_revision=1, actor=WORKER
        )
        repo.clock.advance(10_000)
        removed = await repo.purge_deliveries(
            acknowledged_seconds=1, superseded_seconds=1
        )
        assert removed == 1
        assert await repo.list_deliveries(did) == ()


# ── queries ───────────────────────────────────────────────────────


class TestQuery:
    async def test_filters(self, repo):
        a = await repo.create_drive(
            _req(kind="generic", title="a"), actor=WORKER, graph_id="g1"
        )
        b = await repo.create_drive(
            _req(
                kind="goal",
                title="b",
                scope_type="graph",
                scope_id="g1",
                owner=USER,
                owner_scope="actor",
                created_by=USER,
            ),
            actor=USER,
            graph_id="g1",
        )
        await repo.transition_drive(
            b.drive_id, DriveStatus.COMPLETED, expected_revision=1, actor=USER
        )
        by_kind = await repo.list_drives(DriveQuery(kinds=frozenset({"goal"})))
        assert {r.drive_id for r in by_kind} == {b.drive_id}
        by_status = await repo.list_drives(
            DriveQuery(statuses=frozenset({DriveStatus.ACTIVE}))
        )
        assert {r.drive_id for r in by_status} == {a.drive_id}
        by_owner = await repo.list_drives(DriveQuery(owner=USER))
        assert {r.drive_id for r in by_owner} == {b.drive_id}
        non_terminal = await repo.list_drives(DriveQuery(include_terminal=False))
        assert {r.drive_id for r in non_terminal} == {a.drive_id}
        by_assignee = await repo.list_drives(DriveQuery(assignee_creature_id="worker"))
        assert {r.drive_id for r in by_assignee} == {a.drive_id}

    async def test_graph_and_limit(self, repo):
        await _make(repo, title="one")
        await _make(repo, title="two")
        assert len(await repo.list_drives(DriveQuery(graph_id="g1"))) == 2
        assert len(await repo.list_drives(DriveQuery(graph_id="nope"))) == 0
        assert len(await repo.list_drives(DriveQuery(limit=1))) == 1

    async def test_get_missing_returns_none(self, repo):
        assert await repo.get("nope") is None


# ── outbox ────────────────────────────────────────────────────────


class TestOutbox:
    async def test_mark_dispatched_and_watch(self, repo):
        await _make(repo)
        pending = await repo.list_outbox()
        assert len(pending) == 1
        await repo.mark_outbox_dispatched(pending[0].outbox_id)
        assert await repo.list_outbox() == ()
        assert len(await repo.list_outbox(include_dispatched=True)) == 1
        # watch_outbox drains only undispatched, owns no loop
        await _make(repo)
        seen = [e async for e in repo.watch_outbox()]
        assert all(not e.dispatched for e in seen) and len(seen) == 1


# ── export / import (Phase F seam) ────────────────────────────────


class TestExportImport:
    async def test_round_trip_moves_rows(self, repo, tmp_path):
        did = await _make(repo)
        await repo.enqueue_delivery(did, reason="ready")
        await repo.report_progress(did, summary="p", evidence={}, actor=WORKER)
        payload = await repo.export_rows()
        dest = SqliteDriveRepository(
            str(tmp_path / "dest.sqlite"), clock=Clock(), id_factory=_ids()
        )
        try:
            await dest.import_rows(payload)
            moved = await dest.get(did)
            assert moved is not None and moved.title == "watch"
            assert len(await dest.list_deliveries(did)) == 1
            assert len(await dest.list_progress(did)) == 1
            assert [a.operation for a in await dest.list_audit(did)] == [
                "create",
                "report_progress",
            ]
        finally:
            dest.close_blocking()

    async def test_export_filter_by_drive_ids(self, repo):
        a = await _make(repo, title="a")
        await _make(repo, title="b")
        payload = await repo.export_rows(drive_ids=frozenset({a}))
        assert len(payload["drives"]) == 1

    async def test_import_wrong_schema_rejected(self, repo):
        with pytest.raises(DriveValidationError):
            await repo.import_rows({"wire_schema": 999})


# ── durability flag ───────────────────────────────────────────────


class TestDurability:
    async def test_durability(self, repo):
        expected = (
            "ephemeral" if isinstance(repo, MemoryDriveRepository) else "persistent"
        )
        assert repo.durability == expected


# ── pure builders / wire helpers / predicates (no backend) ────────


class TestPureBuilders:
    def test_build_create_mints_all_rows(self):
        mint = _ids()
        now = datetime(2026, 7, 11, tzinfo=timezone.utc)
        mutation, record = build_create(
            _req(),
            actor=WORKER,
            graph_id="g1",
            status=DriveStatus.ACTIVE,
            now=now,
            mint=mint,
        )
        assert len(mutation.drives) == 1
        assert len(mutation.assignments) == 1
        assert len(mutation.audit) == 1
        assert len(mutation.outbox) == 1
        assert record.revision == 1

    def test_require_revision(self):
        _, rec = build_create(
            _req(),
            actor=WORKER,
            graph_id="g1",
            status=DriveStatus.ACTIVE,
            now=datetime(2026, 7, 11, tzinfo=timezone.utc),
            mint=_ids(),
        )
        require_revision(rec, 1)  # no raise
        with pytest.raises(DriveConflictError):
            require_revision(rec, 2)
        with pytest.raises(DriveConflictError):
            require_revision(rec, None)

    def test_outbox_wire_round_trip(self):
        entry = DriveOutboxEntry(
            outbox_id="o1",
            drive_id="d1",
            kind="drive_ready",
            created_at=datetime(2026, 7, 11, tzinfo=timezone.utc),
            delivery_id="x1",
            dispatched=True,
            payload={"a": 1},
        )
        assert outbox_from_wire(outbox_to_wire(entry)) == entry

    def test_dead_letter_wire_round_trip(self):
        dl = DriveDeadLetter(
            delivery_id="x1",
            drive_id="d1",
            reason="fatal",
            attempt=3,
            created_at=datetime(2026, 7, 11, tzinfo=timezone.utc),
            detail={"k": "v"},
        )
        assert dead_letter_from_wire(dead_letter_to_wire(dl)) == dl

    def test_matches_query_and_in_graph(self):
        mint = _ids()
        _, rec = build_create(
            _req(scope_type="graph", scope_id="g1"),
            actor=WORKER,
            graph_id="g1",
            status=DriveStatus.ACTIVE,
            now=datetime(2026, 7, 11, tzinfo=timezone.utc),
            mint=mint,
        )
        assert in_graph(rec, None, "g1")
        assert not in_graph(rec, None, "g2")
        assert matches_query(DriveQuery(graph_id="g1"), rec, None)
        assert not matches_query(DriveQuery(graph_id="g2"), rec, None)
        assert not matches_query(
            DriveQuery(statuses=frozenset({DriveStatus.WAITING})), rec, None
        )


# ── mutation staging is atomic (rollback discards) ────────────────


class TestTransactionAtomicity:
    async def test_exception_before_commit_persists_nothing(self, repo):
        did = await _make(repo)
        rec = await repo.get(did)
        with pytest.raises(RuntimeError):
            async with repo.transaction() as txn:
                # stage a partial mutation (a drive row WITHOUT its audit/outbox)
                bumped = type(rec)(**{**rec.__dict__, "title": "GHOST", "revision": 2})
                await txn.apply(Mutation(drives=[bumped]))
                raise RuntimeError("crash before commit")
        after = await repo.get(did)
        assert after.title == "watch" and after.revision == 1

    async def test_commit_persists_full_mutation(self, repo):
        async with repo.transaction() as txn:
            mutation, record = build_create(
                _req(),
                actor=WORKER,
                graph_id="g1",
                status=DriveStatus.ACTIVE,
                now=repo.clock(),
                mint=repo._mint,
            )
            await txn.apply(mutation)
        assert await repo.get(record.drive_id) is not None

    async def test_mid_commit_failure_restores_full_precommit_state(self, monkeypatch):
        # Memory-backend specific: the failure is interposed inside the real
        # _apply, AFTER the drive row is written but BEFORE the delete step, so a
        # non-atomic commit would leave a PARTIALLY-COMMITTED title visible.
        clock = Clock()
        repo = MemoryDriveRepository(clock=clock, id_factory=_ids())
        repo.clock = clock
        did = await _make(repo)
        keep = await _make(repo, title="keep")
        before = await repo.get(did)
        assert before.title == "watch" and before.revision == 1

        def boom(_drive_id):
            raise RuntimeError("late commit failure")

        monkeypatch.setattr(repo, "_delete_drive_cascade", boom)

        bumped = type(before)(
            **{**before.__dict__, "title": "PARTIALLY-COMMITTED", "revision": 2}
        )
        with pytest.raises(RuntimeError, match="late commit failure"):
            async with repo.transaction() as txn:
                # drives applies first (mutates title), deleted_drives runs last
                await txn.apply(Mutation(drives=[bumped], deleted_drives=[keep]))

        after = await repo.get(did)
        assert after.title == "watch" and after.revision == 1
        assert await repo.get(keep) is not None

        # a subsequent successful transaction still commits cleanly
        monkeypatch.undo()
        async with repo.transaction() as txn:
            mutation, record = build_create(
                _req(),
                actor=WORKER,
                graph_id="g1",
                status=DriveStatus.ACTIVE,
                now=repo.clock(),
                mint=repo._mint,
            )
            await txn.apply(mutation)
        assert await repo.get(record.drive_id) is not None
