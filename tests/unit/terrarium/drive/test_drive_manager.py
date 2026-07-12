"""Behaviour contract for the DriveManager mutation pipeline (design §4, §8).

Authorization, CAS, spec/evidence guards, the terminal-proposal verifier modes,
owner transfer, dead-letter replay with lineage, reconcile resume, backpressure,
§8.6 availability gating, and the owner/assignee split — all over the real
``MemoryDriveRepository`` and a ``FakeSink``.
"""

import asyncio

import pytest

from kohakuterrarium.terrarium.drive.config import DriveRetryConfig
from kohakuterrarium.terrarium.drive.errors import (
    DriveBackpressureError,
    DriveConflictError,
    DrivePermissionError,
    DriveRegistrationDisabledError,
    DriveTransitionError,
    DriveValidationError,
)
from kohakuterrarium.terrarium.drive.delivery import RECOVERY_WARNING
from kohakuterrarium.terrarium.drive.goal import GoalDriveRegistration
from kohakuterrarium.terrarium.drive.manager import DriveManager
from kohakuterrarium.terrarium.drive.models import (
    SYSTEM_ACTOR,
    ActorRef,
    DriveRecord,
    DriveStatus,
)
from kohakuterrarium.terrarium.drive.registration import (
    DEFAULT_SPEC_MAX_BYTES,
    DriveRegistrationDescriptor,
    GenericDriveRegistration,
    Readiness,
    VerificationResult,
)
from kohakuterrarium.terrarium.drive.requests import DrivePatch, DriveTransitionProposal
from kohakuterrarium.terrarium.drive.sink import SettlementStatus
from kohakuterrarium.terrarium.drive.snapshot import EnabledRegistrySnapshot
from kohakuterrarium.terrarium.drive.store import SqliteDriveRepository

from tests.unit.terrarium.drive._harness import (
    ADMIN,
    OTHER,
    USER,
    WORKER,
    Clock,
    FakeSink,
    ScriptedRng,
    build_manager,
    creature_request,
    graph_request,
    ids,
    kinds,
    make_config,
)

ADMIN2 = ActorRef("service", "ops2")


class _VerifierReg(GenericDriveRegistration):
    """A ``verified``-kind registration with a configurable verifier mode."""

    name = "verified"
    kind = "verified"

    def __init__(self, mode: str, *, approve: bool = True, raise_exc: bool = False):
        super().__init__()
        self._mode = mode
        self._approve = approve
        self._raise = raise_exc

    def descriptor(self) -> DriveRegistrationDescriptor:
        return DriveRegistrationDescriptor(
            name=self.name,
            kind=self.kind,
            schema_version=1,
            required_roles=frozenset({"spec", "transition", "readiness"}),
            optional_roles=frozenset({"projection", "verifier", "prompt"}),
            verifier_mode=self._mode,
        )

    def verify_terminal(self, proposal, context) -> VerificationResult:
        if self._raise:
            raise RuntimeError("verifier boom")
        return VerificationResult(approved=self._approve)


def _verified_manager(mode, **kw):
    snap = EnabledRegistrySnapshot.build([_VerifierReg(mode, **kw)])
    return build_manager(snapshot=snap)


class _ContinueReg(GenericDriveRegistration):
    """A ``cont``-kind registration that re-arms until a turn budget is hit."""

    name = "cont"
    kind = "cont"

    def __init__(
        self,
        *,
        max_turns: int | None = None,
        raise_exc: bool = False,
        raise_after: int | None = None,
    ):
        super().__init__()
        self._max = max_turns
        self._raise = raise_exc
        # ``raise_after`` raises only once ``turns_used`` reaches it, so the
        # initial admission (turns_used=0) can succeed and a *continuation*
        # readiness failure is exercised distinctly from an initial one.
        self._raise_after = raise_after

    def descriptor(self) -> DriveRegistrationDescriptor:
        return DriveRegistrationDescriptor(
            name=self.name,
            kind=self.kind,
            schema_version=1,
            required_roles=frozenset({"spec", "transition", "readiness"}),
            optional_roles=frozenset({"projection", "verifier", "prompt"}),
            verifier_mode="none",
        )

    def readiness(self, drive, dependencies, now, *, turns_used: int = 0) -> Readiness:
        if self._raise:
            raise RuntimeError("readiness boom")
        if self._raise_after is not None and turns_used >= self._raise_after:
            raise RuntimeError("readiness boom")
        if self._max is not None and turns_used >= self._max:
            return Readiness(ready=False, re_arm=False, reason="budget exhausted")
        return Readiness(ready=True, re_arm=True)


class _InitialRearmReg(GenericDriveRegistration):
    """Goal-style autonomy: one ``initial`` grant, then continuation ONLY via
    ``re_arm`` — plain ``ready`` stays False for every admission."""

    name = "goalish"
    kind = "goalish"

    def __init__(self, *, max_turns: int | None = None):
        super().__init__()
        self._max = max_turns

    def descriptor(self) -> DriveRegistrationDescriptor:
        return DriveRegistrationDescriptor(
            name=self.name,
            kind=self.kind,
            schema_version=1,
            required_roles=frozenset({"spec", "transition", "readiness"}),
            optional_roles=frozenset({"projection", "verifier", "prompt"}),
            verifier_mode="none",
        )

    def readiness(self, drive, dependencies, now, *, turns_used: int = 0) -> Readiness:
        if turns_used == 0:
            return Readiness(ready=False, initial=True)
        if self._max is not None and turns_used >= self._max:
            return Readiness(ready=False, re_arm=False, reason="budget exhausted")
        return Readiness(ready=False, re_arm=True)


class _NotReadyReg(GenericDriveRegistration):
    """A ``blocked``-kind registration whose readiness is unambiguously false.

    ``ready=False`` with no ``initial`` grant: the Drive must never earn a
    delivery on create *or* scan (design §5.2 admission-time readiness).
    """

    name = "notready"
    kind = "notready"

    def descriptor(self) -> DriveRegistrationDescriptor:
        return DriveRegistrationDescriptor(
            name=self.name,
            kind=self.kind,
            schema_version=1,
            required_roles=frozenset({"spec", "transition", "readiness"}),
            optional_roles=frozenset({"projection", "verifier", "prompt"}),
            verifier_mode="none",
        )

    def readiness(self, drive, dependencies, now, *, turns_used: int = 0) -> Readiness:
        return Readiness(ready=False, reason="never ready")


class _DeferrableReg(GenericDriveRegistration):
    """A ``deferrable``-kind registration whose readiness is admittable until
    ``blocked`` is flipped on, then denies (``ready=False`` with no ``initial``).

    Flipping ``blocked`` turns admission — including recovery admission — into a
    temporary DEFERRAL without ever raising (which would fail-closed and block),
    so a test can hold recovery off at one reconcile and let it through at the
    next. ``re_arm`` is False so a recovered turn settles once, not in a loop.
    """

    name = "deferrable"
    kind = "deferrable"

    def __init__(self) -> None:
        super().__init__()
        self.blocked = False

    def descriptor(self) -> DriveRegistrationDescriptor:
        return DriveRegistrationDescriptor(
            name=self.name,
            kind=self.kind,
            schema_version=1,
            required_roles=frozenset({"spec", "transition", "readiness"}),
            optional_roles=frozenset({"projection", "verifier", "prompt"}),
            verifier_mode="none",
        )

    def readiness(self, drive, dependencies, now, *, turns_used: int = 0) -> Readiness:
        if self.blocked:
            return Readiness(ready=False, reason="deferred")
        return Readiness(ready=True, re_arm=False)


class _ReopenReg(GenericDriveRegistration):
    """A ``reopen``-kind registration that approves the terminal-reopen edge."""

    name = "reopen"
    kind = "reopen"

    def descriptor(self) -> DriveRegistrationDescriptor:
        return DriveRegistrationDescriptor(
            name=self.name,
            kind=self.kind,
            schema_version=1,
            required_roles=frozenset({"spec", "transition", "readiness"}),
            optional_roles=frozenset({"projection", "verifier", "prompt"}),
            verifier_mode="none",
        )

    def extra_transitions(self):
        return frozenset({(DriveStatus.COMPLETED, DriveStatus.ACTIVE)})


def _cont_manager(
    *, max_turns=None, raise_exc=False, raise_after=None, config=None, clock=None
):
    snap = EnabledRegistrySnapshot.build(
        [
            _ContinueReg(
                max_turns=max_turns, raise_exc=raise_exc, raise_after=raise_after
            )
        ]
    )
    return build_manager(snapshot=snap, config=config, clock=clock)


async def _round(h) -> None:
    await h.manager.dispatcher.dispatch_once()
    await h.manager.dispatcher.drain()


def _acked(deliveries) -> list:
    return [d for d in deliveries if d.state == "acknowledged"]


async def _worker_verified(h) -> str:
    rec = await h.manager.create_drive(
        creature_request(kind="verified"), actor=WORKER, graph_id="g1"
    )
    return rec.drive_id


# ── authorization + CAS on ordinary mutations ─────────────────────────


class TestAuthorizationAndCas:
    async def test_owner_can_update_foreign_cannot(self):
        h = build_manager()
        rec = await h.manager.create_drive(
            creature_request(), actor=WORKER, graph_id="g1"
        )
        await h.manager.update_drive(
            rec.drive_id, DrivePatch(title="v2"), expected_revision=1, actor=WORKER
        )
        assert (await h.manager.get_drive(rec.drive_id)).title == "v2"
        with pytest.raises(DrivePermissionError):
            await h.manager.update_drive(
                rec.drive_id, DrivePatch(title="x"), expected_revision=2, actor=OTHER
            )

    async def test_stale_revision_conflicts(self):
        h = build_manager()
        rec = await h.manager.create_drive(
            creature_request(), actor=WORKER, graph_id="g1"
        )
        with pytest.raises(DriveConflictError):
            await h.manager.update_drive(
                rec.drive_id, DrivePatch(title="x"), expected_revision=99, actor=WORKER
            )

    async def test_create_idempotency_key_returns_original(self):
        h = build_manager()
        r1 = await h.manager.create_drive(
            creature_request(idempotency_key="k1"), actor=WORKER, graph_id="g1"
        )
        r2 = await h.manager.create_drive(
            creature_request(idempotency_key="k1"), actor=WORKER, graph_id="g1"
        )
        assert r1.drive_id == r2.drive_id


# ── owner vs assignee (foreign-owned assignee) ────────────────────────


class TestOwnerAssigneeSplit:
    async def _graph_drive(self, h) -> str:
        rec = await h.manager.create_drive(
            graph_request(), actor=WORKER, graph_id="g1", is_privileged=True
        )
        return rec.drive_id

    async def test_assignee_can_report_and_propose(self):
        h = build_manager()
        did = await self._graph_drive(h)
        await h.manager.report_progress(
            did, summary="halfway", evidence={"pct": 50}, actor=WORKER
        )
        assert len(await h.manager.list_progress(did)) == 1
        result = await h.manager.propose_transition(
            did, DriveStatus.COMPLETED, actor=WORKER, evidence={"done": True}
        )
        assert isinstance(result, DriveRecord)
        assert (await h.manager.get_drive(did)).status is DriveStatus.COMPLETED

    async def test_assignee_cannot_cancel_or_reassign(self):
        h = build_manager()
        did = await self._graph_drive(h)
        with pytest.raises(DrivePermissionError):
            await h.manager.transition(
                did, DriveStatus.CANCELLED, expected_revision=1, actor=WORKER
            )
        with pytest.raises(DrivePermissionError):
            await h.manager.reassign(
                did, "other", "g1", expected_revision=1, actor=WORKER
            )


# ── terminal proposal pipeline (§4.2) ─────────────────────────────────


class TestTerminalProposals:
    async def test_verifier_none_auto_accepts(self):
        h = build_manager()
        rec = await h.manager.create_drive(
            creature_request(), actor=WORKER, graph_id="g1"
        )
        result = await h.manager.propose_transition(
            rec.drive_id, DriveStatus.COMPLETED, actor=WORKER
        )
        assert result.status is DriveStatus.COMPLETED

    async def test_transition_refuses_verified_terminal_targets(self):
        # completion/failure must go through the verifier pipeline, and retire
        # through the admin path — never a direct transition() status write.
        h = build_manager()
        rec = await h.manager.create_drive(
            creature_request(), actor=WORKER, graph_id="g1"
        )
        for target in (DriveStatus.COMPLETED, DriveStatus.FAILED, DriveStatus.RETIRED):
            with pytest.raises(DriveValidationError):
                await h.manager.transition(
                    rec.drive_id, target, expected_revision=1, actor=WORKER
                )
        assert (await h.manager.get_drive(rec.drive_id)).status is DriveStatus.ACTIVE

    async def test_transition_allows_cancel(self):
        h = build_manager()
        rec = await h.manager.create_drive(
            creature_request(), actor=WORKER, graph_id="g1"
        )
        cancelled = await h.manager.transition(
            rec.drive_id, DriveStatus.CANCELLED, expected_revision=1, actor=WORKER
        )
        assert cancelled.status is DriveStatus.CANCELLED

    async def test_extension_verifier_approves(self):
        h = _verified_manager("extension", approve=True)
        did = await _worker_verified(h)
        result = await h.manager.propose_transition(
            did, DriveStatus.COMPLETED, actor=WORKER
        )
        assert result.status is DriveStatus.COMPLETED

    async def test_extension_verifier_rejection_leaves_drive_unchanged(self):
        h = _verified_manager("extension", approve=False)
        did = await _worker_verified(h)
        with pytest.raises(DriveTransitionError):
            await h.manager.propose_transition(did, DriveStatus.COMPLETED, actor=WORKER)
        assert (await h.manager.get_drive(did)).status is DriveStatus.ACTIVE
        audit_ops = [a.operation for a in await h.repo.list_audit(did)]
        assert "verify_rejected" in audit_ops

    async def test_extension_verifier_raise_fails_closed(self):
        h = _verified_manager("extension", raise_exc=True)
        did = await _worker_verified(h)
        with pytest.raises(DriveTransitionError):
            await h.manager.propose_transition(did, DriveStatus.COMPLETED, actor=WORKER)
        assert (await h.manager.get_drive(did)).status is DriveStatus.ACTIVE
        assert "verify_rejected" in [a.operation for a in await h.repo.list_audit(did)]

    async def test_actor_mode_waits_for_authorized_approver(self):
        h = _verified_manager("actor")
        did = await _worker_verified(h)
        proposal = await h.manager.propose_transition(
            did, DriveStatus.COMPLETED, actor=WORKER
        )
        assert isinstance(proposal, DriveTransitionProposal)
        assert (await h.manager.get_drive(did)).status is DriveStatus.ACTIVE
        # an actor without verify capability may not finalize
        with pytest.raises(DrivePermissionError):
            await h.manager.approve_proposal(proposal.proposal_id, actor=OTHER)
        record = await h.manager.approve_proposal(
            proposal.proposal_id, actor=ADMIN, is_privileged=True
        )
        assert record.status is DriveStatus.COMPLETED

    async def test_two_party_requires_distinct_approver(self):
        h = _verified_manager("two_party")
        did = await _worker_verified(h)
        proposal = await h.manager.propose_transition(
            did, DriveStatus.COMPLETED, actor=ADMIN, is_privileged=True
        )
        with pytest.raises(DrivePermissionError):
            await h.manager.approve_proposal(
                proposal.proposal_id, actor=ADMIN, is_privileged=True
            )
        record = await h.manager.approve_proposal(
            proposal.proposal_id, actor=ADMIN2, is_privileged=True
        )
        assert record.status is DriveStatus.COMPLETED


# ── §8.6 availability gating ──────────────────────────────────────────


class TestAvailabilityGating:
    async def test_admin_transitions_open_but_semantic_ops_fail_closed(self):
        h = build_manager()
        rec = await h.manager.create_drive(
            creature_request(), actor=WORKER, graph_id="g1"
        )
        did = rec.drive_id
        # registration for the kind is no longer enabled
        h.manager.set_snapshot(EnabledRegistrySnapshot.build([]))
        # pause / cancel stay usable so the record stays administratable
        paused = await h.manager.transition(
            did, DriveStatus.PAUSED, expected_revision=1, actor=WORKER
        )
        assert paused.status is DriveStatus.PAUSED
        # create / update / wake require an available registration
        with pytest.raises(DriveRegistrationDisabledError):
            await h.manager.create_drive(
                creature_request(), actor=WORKER, graph_id="g1"
            )
        with pytest.raises(DriveRegistrationDisabledError):
            await h.manager.update_drive(
                did, DrivePatch(title="x"), expected_revision=2, actor=WORKER
            )
        with pytest.raises(DriveRegistrationDisabledError):
            await h.manager.wake_drive(did, actor=WORKER)


# ── owner transfer ────────────────────────────────────────────────────


class TestOwnerTransfer:
    async def test_transfer_moves_owner_keeps_created_by(self):
        h = build_manager()
        rec = await h.manager.create_drive(
            creature_request(), actor=WORKER, graph_id="g1"
        )
        moved = await h.manager.transfer_owner(
            rec.drive_id, USER, expected_revision=1, actor=ADMIN, is_privileged=True
        )
        assert moved.owner == USER
        assert moved.created_by == WORKER
        assert moved.revision == 2
        assert "transfer_owner" in [
            a.operation for a in await h.repo.list_audit(rec.drive_id)
        ]
        assert "drive_owner_transferred" in kinds(h.observations)

    async def test_non_privileged_owner_cannot_transfer(self):
        h = build_manager()
        rec = await h.manager.create_drive(
            creature_request(), actor=WORKER, graph_id="g1"
        )
        with pytest.raises(DrivePermissionError):
            await h.manager.transfer_owner(
                rec.drive_id, USER, expected_revision=1, actor=WORKER
            )


# ── dead-letter replay ────────────────────────────────────────────────


class TestReplay:
    async def test_replay_mints_new_delivery_with_lineage(self):
        h = build_manager(
            sink=FakeSink(default=SettlementStatus.ERROR),
            config=make_config(retry=DriveRetryConfig(max_attempts=1)),
        )
        rec = await h.manager.create_drive(
            creature_request(), actor=WORKER, graph_id="g1"
        )
        did = rec.drive_id
        await h.manager.dispatcher.dispatch_once()
        await h.manager.dispatcher.drain()
        dead = (await h.repo.list_deliveries(did))[0]
        assert dead.state == "dead_letter"
        new = await h.manager.replay_dead_letter(
            dead.delivery_id, actor=ADMIN, is_privileged=True
        )
        assert new.delivery_id != dead.delivery_id
        assert new.state == "pending" and new.reason == "retry"
        outbox = await h.repo.list_outbox(include_dispatched=True)
        assert any(e.payload.get("replayed_from") == dead.delivery_id for e in outbox)


# ── reconcile resume ──────────────────────────────────────────────────


class TestReconcile:
    async def test_reconcile_resumes_active_drive(self):
        h = build_manager()
        rec = await h.manager.create_drive(
            creature_request(), actor=WORKER, graph_id="g1"
        )
        did = rec.drive_id
        await h.manager.dispatcher.dispatch_once()
        await h.manager.dispatcher.drain()
        assert (await h.repo.list_deliveries(did))[0].state == "acknowledged"
        await h.manager.reconcile(creature_id="worker")
        deliveries = await h.repo.list_deliveries(did)
        assert len(deliveries) == 2
        resume = [d for d in deliveries if d.reason == "resume"]
        assert len(resume) == 1 and resume[0].state == "pending"

    async def test_reconcile_resume_is_readiness_gated(self):
        # R1-05: reconcile resume must consult registration readiness, exactly
        # like create/scan — an unready registration is not redelivered on resume.
        snap = EnabledRegistrySnapshot.build([_NotReadyReg()])
        h = build_manager(snapshot=snap)
        rec = await h.manager.create_drive(
            creature_request(kind="notready"), actor=WORKER, graph_id="g1"
        )
        assert await h.repo.list_deliveries(rec.drive_id) == ()  # unadmitted on create
        await h.manager.reconcile(creature_id="worker")
        assert await h.repo.list_deliveries(rec.drive_id) == ()  # still gated

    async def test_reconcile_recovery_is_readiness_gated(self):
        # R1-05 policy: recovery of an uncertain delivery is readiness-gated too —
        # it does NOT override readiness. Because recovery is not admittable, the
        # uncertain row is RETAINED (not superseded): superseding a deferred
        # attempt would strand its recovery intent and downgrade a later reconcile
        # to a warning-less resume. No new recovery/resume delivery is minted.
        snap = EnabledRegistrySnapshot.build([_NotReadyReg()])
        h = build_manager(snapshot=snap)
        rec = await h.manager.create_drive(
            creature_request(kind="notready"), actor=WORKER, graph_id="g1"
        )
        did = rec.drive_id
        uncertain = await h.repo.enqueue_delivery(did, reason="ready")
        await h.repo.mark_delivery(uncertain.delivery_id, "admitted")  # never acked
        await h.manager.reconcile(creature_id="worker")
        deliveries = await h.repo.list_deliveries(did)
        # Only the retained uncertain attempt exists — nothing new was minted and
        # the uncertain row was NOT superseded.
        assert len(deliveries) == 1
        assert deliveries[0].delivery_id == uncertain.delivery_id
        assert deliveries[0].state == "admitted"
        assert not any(d.reason in ("recovery", "resume") for d in deliveries)

    async def test_reconcile_orphans_invalid_topology_target(self):
        # R1-06: reconcile revalidates a persisted assignment against live
        # topology; an invalid target is orphaned+blocked, never delivered.
        h = build_manager()
        rec = await h.manager.create_drive(
            graph_request(),
            actor=USER,
            graph_id="g1",
            is_privileged=True,
        )
        before = len(await h.repo.list_deliveries(rec.drive_id))
        h.manager._topology_validator = lambda assignment: False  # target invalid
        await h.manager.reconcile(graph_id="g1")
        drive = await h.manager.get_drive(rec.drive_id)
        assert drive.status is DriveStatus.BLOCKED
        assignment = await h.manager.get_assignment(rec.drive_id)
        assert assignment.assignment_state == "orphaned"
        assert len(await h.repo.list_deliveries(rec.drive_id)) == before  # none added
        # A second reconcile does NOT re-orphan (no revision/audit churn).
        blocked_rev = drive.revision
        await h.manager.reconcile(graph_id="g1")
        assert (await h.manager.get_drive(rec.drive_id)).revision == blocked_rev

    async def test_reconcile_delivers_when_topology_valid(self):
        # The positive side: a validator that accepts the assignment leaves
        # reconcile free to resume-deliver a ready Drive (R1-06).
        h = build_manager()
        rec = await h.manager.create_drive(
            graph_request(),
            actor=USER,
            graph_id="g1",
            is_privileged=True,
        )
        await h.manager.dispatcher.dispatch_once()
        await h.manager.dispatcher.drain()
        h.manager._topology_validator = lambda assignment: True
        await h.manager.reconcile(graph_id="g1")
        drive = await h.manager.get_drive(rec.drive_id)
        assert drive.status is DriveStatus.ACTIVE
        assert any(
            d.reason == "resume" for d in await h.repo.list_deliveries(rec.drive_id)
        )


# ── backpressure on create ────────────────────────────────────────────


class TestCreateBackpressure:
    async def test_active_per_creature_limit_raises(self):
        h = build_manager(config=make_config(max_active_per_creature=2))
        await h.manager.create_drive(
            creature_request(title="a"), actor=WORKER, graph_id="g1"
        )
        await h.manager.create_drive(
            creature_request(title="b"), actor=WORKER, graph_id="g1"
        )
        with pytest.raises(DriveBackpressureError):
            await h.manager.create_drive(
                creature_request(title="c"), actor=WORKER, graph_id="g1"
            )

    async def test_pending_per_graph_limit_raises(self):
        h = build_manager(config=make_config(max_pending_per_graph=1))
        await h.manager.create_drive(
            creature_request(title="a"), actor=WORKER, graph_id="g1"
        )
        with pytest.raises(DriveBackpressureError):
            await h.manager.create_drive(
                creature_request(title="b"), actor=WORKER, graph_id="g1"
            )


# ── spec / evidence guards + allowed_actions ──────────────────────────


class TestGuardsAndActions:
    async def test_non_json_spec_fails_closed(self):
        h = build_manager()
        with pytest.raises(DriveValidationError):
            await h.manager.create_drive(
                creature_request(spec={"bad": {1, 2, 3}}), actor=WORKER, graph_id="g1"
            )

    async def test_oversize_evidence_rejected(self):
        h = build_manager(config=make_config(evidence_max_bytes=64))
        rec = await h.manager.create_drive(
            creature_request(), actor=WORKER, graph_id="g1"
        )
        with pytest.raises(DriveValidationError):
            await h.manager.report_progress(
                rec.drive_id, summary="s", evidence={"blob": "a" * 500}, actor=WORKER
            )

    async def test_progress_open_when_registration_unavailable(self):
        h = build_manager()
        rec = await h.manager.create_drive(
            creature_request(), actor=WORKER, graph_id="g1"
        )
        h.manager.set_snapshot(EnabledRegistrySnapshot.build([]))
        await h.manager.report_progress(
            rec.drive_id, summary="still here", evidence={}, actor=WORKER
        )
        assert len(await h.manager.list_progress(rec.drive_id)) == 1

    async def test_allowed_actions_reflect_owner_and_foreign(self):
        h = build_manager()
        rec = await h.manager.create_drive(
            creature_request(), actor=WORKER, graph_id="g1"
        )
        assignment = await h.manager.get_assignment(rec.drive_id)
        owner_actions = h.manager.allowed_actions(WORKER, rec, assignment)
        assert "update" in owner_actions and "read" in owner_actions
        foreign_actions = h.manager.allowed_actions(USER, rec, assignment)
        assert foreign_actions == ("read",)


# ── readiness-driven continuation (design §4.4, §5.3, §11.4) ──────────


class TestContinuation:
    async def test_continue_when_ready_rearms_across_settlements(self):
        # A registration that re-arms must produce a fresh delivery per settled
        # turn until its turn budget stops it; the generic dispatcher never
        # completes the Drive on its own.
        h = _cont_manager(max_turns=3)
        rec = await h.manager.create_drive(
            creature_request(kind="cont"), actor=WORKER, graph_id="g1"
        )
        did = rec.drive_id
        for _ in range(5):
            await _round(h)
        acked = _acked(await h.repo.list_deliveries(did))
        assert len(acked) == 3  # budget caps continuation at 3 turns
        assert (await h.manager.get_drive(did)).status is DriveStatus.ACTIVE

    async def test_manual_wake_after_ack_mints_fresh_generation(self):
        # A manual wake on a Drive whose current generation already reached the
        # creature must mint a NEW generation, else §5.3 dedupe supersedes it.
        h = build_manager()  # generic: no autonomous re-arm
        rec = await h.manager.create_drive(
            creature_request(), actor=WORKER, graph_id="g1"
        )
        did = rec.drive_id
        await _round(h)
        assert len(_acked(await h.repo.list_deliveries(did))) == 1
        await h.manager.wake_drive(did, actor=WORKER)
        await _round(h)
        acked = _acked(await h.repo.list_deliveries(did))
        assert len(acked) == 2
        assert any(d.reason == "manual_wake" for d in acked)

    async def test_reconcile_rearms_settled_continuation_after_restart(self):
        # Restart parity (§6.1 + §11.4): an acknowledged re_arm-style
        # generation must re-arm through reconcile too, not only via the live
        # ack callback — else continue_when_ready pursuit dies across resume.
        clock = Clock()
        snap = EnabledRegistrySnapshot.build([_InitialRearmReg(max_turns=3)])
        cfg = make_config(readiness_cooldown_s=30.0)
        h = build_manager(snapshot=snap, config=cfg, clock=clock)
        rec = await h.manager.create_drive(
            creature_request(kind="goalish"), actor=WORKER, graph_id="g1"
        )
        did = rec.drive_id
        await _round(h)
        # Cooldown defers the live re-arm: acked generation, nothing pending —
        # the exact between-generations state a process restart lands in.
        assert len(_acked(await h.repo.list_deliveries(did))) == 1
        assert not any(
            d.state in ("pending", "claimed", "admitted")
            for d in await h.repo.list_deliveries(did)
        )
        restarted = build_manager(
            snapshot=snap, config=cfg, clock=clock, repo=h.repo
        ).manager
        clock.advance(60.0)
        await restarted.reconcile(all=True)
        resumed = [d for d in await h.repo.list_deliveries(did) if d.reason == "resume"]
        assert resumed, "settled continuation was not re-armed by reconcile"

    async def test_production_goal_restart_rearms_generation_not_superseded(self):
        # R1-05 with the REAL GoalDriveRegistration in continue_when_ready mode
        # (readiness returns ready=True AND re_arm=True). After a settled
        # generation, a restart's reconcile must RE-ARM to generation+1 and have
        # dispatch ACKNOWLEDGE it — not enqueue a stale-generation resume duplicate
        # that dispatch supersedes.
        clock = Clock()
        snap = EnabledRegistrySnapshot.build([GoalDriveRegistration()])
        cfg = make_config(readiness_cooldown_s=30.0)
        h = build_manager(snapshot=snap, config=cfg, clock=clock)
        rec = await h.manager.create_drive(
            creature_request(
                kind="goal",
                spec={"objective": "watch the logs", "autonomy": "continue_when_ready"},
            ),
            actor=WORKER,
            graph_id="g1",
        )
        did = rec.drive_id
        await _round(h)  # deliver + ack generation 0; cooldown defers the live re-arm
        assert len(_acked(await h.repo.list_deliveries(did))) == 1
        assert not any(
            d.state in ("pending", "claimed", "admitted")
            for d in await h.repo.list_deliveries(did)
        )

        restarted_h = build_manager(snapshot=snap, config=cfg, clock=clock, repo=h.repo)
        restarted = restarted_h.manager
        clock.advance(60.0)  # cooldown elapsed
        await restarted.reconcile(all=True)
        await restarted.dispatcher.dispatch_once()
        await restarted.dispatcher.drain()

        deliveries = await h.repo.list_deliveries(did)
        # The continuation resumed as an ACKNOWLEDGED generation+1 delivery …
        acked_gen1 = [
            d
            for d in deliveries
            if d.readiness_generation == 1 and d.state == "acknowledged"
        ]
        assert len(acked_gen1) == 1
        # … and NOT as a stale-generation resume duplicate that dispatch supersedes.
        assert not any(d.state == "superseded" for d in deliveries)
        assert sorted(d.readiness_generation for d in _acked(deliveries)) == [0, 1]

    async def test_goal_restart_during_cooldown_emits_no_stale_resume(self):
        # R1-05 defect: a settled continue_when_ready Goal restarted WHILE STILL
        # inside its readiness cooldown must NOT fall back to a stale gen-0
        # resume (which dispatch would only supersede). Reconcile must recognise
        # the re-arm as deferred, enqueue nothing, and let the next scan re-arm
        # generation+1 once the cooldown elapses. Unlike the restart-parity test
        # above, the clock is NOT advanced past the cooldown before reconcile.
        clock = Clock()
        snap = EnabledRegistrySnapshot.build([GoalDriveRegistration()])
        cfg = make_config(readiness_cooldown_s=30.0)
        h = build_manager(snapshot=snap, config=cfg, clock=clock)
        rec = await h.manager.create_drive(
            creature_request(
                kind="goal",
                spec={"objective": "watch the logs", "autonomy": "continue_when_ready"},
            ),
            actor=WORKER,
            graph_id="g1",
        )
        did = rec.drive_id
        await _round(h)  # deliver + ack gen 0; the cooldown defers the live re-arm
        assert len(_acked(await h.repo.list_deliveries(did))) == 1
        assert not any(
            d.state in ("pending", "claimed", "admitted")
            for d in await h.repo.list_deliveries(did)
        )

        restarted_h = build_manager(snapshot=snap, config=cfg, clock=clock, repo=h.repo)
        restarted = restarted_h.manager
        # Reconcile while STILL inside the cooldown window (no clock advance).
        await restarted.reconcile(all=True)
        after_reconcile = await h.repo.list_deliveries(did)
        # No stale gen-0 resume was enqueued: the deferred re-arm waits for a scan.
        assert not any(
            d.reason == "resume"
            and d.readiness_generation == 0
            and d.state == "pending"
            for d in after_reconcile
        )
        await restarted.dispatcher.dispatch_once()
        await restarted.dispatcher.drain()
        # Nothing stale was enqueued, so dispatch supersedes nothing.
        assert not any(
            d.state == "superseded" for d in await h.repo.list_deliveries(did)
        )

        # Once the cooldown elapses, the next scan re-arms to generation+1 and acks.
        clock.advance(60.0)
        await restarted._scan_ready()
        await restarted.dispatcher.dispatch_once()
        await restarted.dispatcher.drain()
        deliveries = await h.repo.list_deliveries(did)
        acked_gen1 = [
            d
            for d in deliveries
            if d.readiness_generation == 1 and d.state == "acknowledged"
        ]
        assert len(acked_gen1) == 1
        assert not any(d.state == "superseded" for d in deliveries)
        assert sorted(d.readiness_generation for d in _acked(deliveries)) == [0, 1]

    async def test_manual_goal_uncertain_recovery_readmits_after_restart(self):
        # R1-05 / §6.1 with the REAL manual-mode GoalDriveRegistration: an initial
        # delivery ADMITTED but never acknowledged (uncertain) must be recovered on
        # restart. Reconcile supersedes the uncertain attempt; that superseded row
        # must NOT consume the one-per-epoch initial grant, so recovery re-admits a
        # delivery carrying the §6.1 uncertain-attempt (drive_recovery) semantics.
        snap = EnabledRegistrySnapshot.build([GoalDriveRegistration()])
        h = build_manager(snapshot=snap)
        rec = await h.manager.create_drive(
            creature_request(
                kind="goal", spec={"objective": "watch", "autonomy": "manual"}
            ),
            actor=WORKER,
            graph_id="g1",
        )
        did = rec.drive_id
        # Admit the initial delivery but never settle it → an uncertain attempt.
        h.sink.no_settle = True
        await h.manager.dispatcher.dispatch_once()
        admitted = [
            d for d in await h.repo.list_deliveries(did) if d.state == "admitted"
        ]
        assert len(admitted) == 1 and admitted[0].acknowledged_at is None

        # Restart with a fresh, settling sink; reconcile recovers, dispatch acks.
        restarted_h = build_manager(snapshot=snap, repo=h.repo)
        restarted = restarted_h.manager
        await restarted.reconcile(all=True)
        recovery = [
            d for d in await h.repo.list_deliveries(did) if d.reason == "recovery"
        ]
        assert len(recovery) == 1  # recovery admitted despite a prior epoch delivery
        await restarted.dispatcher.dispatch_once()
        await restarted.dispatcher.drain()

        deliveries = await h.repo.list_deliveries(did)
        assert any(d.state == "superseded" for d in deliveries)  # the uncertain attempt
        acked_recovery = [
            d
            for d in deliveries
            if d.reason == "recovery" and d.state == "acknowledged"
        ]
        assert len(acked_recovery) == 1
        event = restarted_h.sink.delivered[-1].event
        assert event.type == "drive_recovery"  # §6.1 uncertain-attempt event
        assert RECOVERY_WARNING in (event.prompt_override or "")

    async def test_uncertain_recovery_is_crash_atomic(self):
        # §6.1: superseding the uncertain attempt and enqueuing its recovery
        # delivery must be ONE transaction. A crash between them (here the commit
        # that first writes the pending recovery delivery fails) must leave NO
        # durable half-state — neither a superseded uncertain nor a recovery — so
        # the retry still delivers a recovery, never a warning-less resume.
        snap = EnabledRegistrySnapshot.build([GoalDriveRegistration()])
        h = build_manager(snapshot=snap)
        rec = await h.manager.create_drive(
            creature_request(
                kind="goal", spec={"objective": "watch", "autonomy": "manual"}
            ),
            actor=WORKER,
            graph_id="g1",
        )
        did = rec.drive_id
        # Admit the initial delivery but never settle it → an uncertain attempt.
        h.sink.no_settle = True
        await h.manager.dispatcher.dispatch_once()
        admitted = [
            d for d in await h.repo.list_deliveries(did) if d.state == "admitted"
        ]
        assert len(admitted) == 1 and admitted[0].acknowledged_at is None

        # Fail the commit that first writes a pending recovery delivery — the
        # crash point between the supersede and the recovery enqueue.
        fault = {"on": True}
        original_apply = h.repo._apply

        def faulting_apply(mutation):
            if fault["on"] and any(
                d.reason == "recovery" and d.state == "pending"
                for d in mutation.deliveries
            ):
                raise RuntimeError("crash between supersede and recovery enqueue")
            return original_apply(mutation)

        h.repo._apply = faulting_apply

        restarted_h = build_manager(snapshot=snap, repo=h.repo)
        restarted = restarted_h.manager
        with pytest.raises(RuntimeError):
            await restarted.reconcile(all=True)

        # Atomicity: the failed recovery commit rolled back BOTH halves — the
        # uncertain attempt is still admitted and no recovery row exists.
        after_crash = await h.repo.list_deliveries(did)
        assert not any(d.reason == "recovery" for d in after_crash)
        assert not any(d.state == "superseded" for d in after_crash)
        still_admitted = [d for d in after_crash if d.state == "admitted"]
        assert len(still_admitted) == 1
        assert still_admitted[0].delivery_id == admitted[0].delivery_id

        # Retry after the failpoint clears: recovery is produced + acknowledged
        # carrying the §6.1 uncertain-attempt semantics — never a plain resume.
        fault["on"] = False
        await restarted.reconcile(all=True)
        await restarted.dispatcher.dispatch_once()
        await restarted.dispatcher.drain()

        deliveries = await h.repo.list_deliveries(did)
        assert any(d.state == "superseded" for d in deliveries)  # the uncertain attempt
        acked_recovery = [
            d
            for d in deliveries
            if d.reason == "recovery" and d.state == "acknowledged"
        ]
        assert len(acked_recovery) == 1
        assert not any(d.reason == "resume" for d in deliveries)
        event = restarted_h.sink.delivered[-1].event
        assert event.type == "drive_recovery"
        assert RECOVERY_WARNING in (event.prompt_override or "")

    async def test_deferred_recovery_retains_uncertain_then_recovers_when_cleared(
        self,
    ):
        # §6.1 / R1-05 DEFERRED case: a REAL uncertain attempt whose recovery is
        # not admittable at reconcile (readiness defers admission) must NOT be
        # superseded — else a later reconcile downgrades the restart to a plain,
        # warning-less ``resume``. The uncertain row is retained; once the
        # deferral clears, reconcile recovers it as a ``drive_recovery`` carrying
        # the §6.1 RECOVERY_WARNING, and no ``resume`` row is ever produced.
        reg = _DeferrableReg()
        snap = EnabledRegistrySnapshot.build([reg])
        h = build_manager(snapshot=snap)
        rec = await h.manager.create_drive(
            creature_request(kind="deferrable"), actor=WORKER, graph_id="g1"
        )
        did = rec.drive_id
        # Admit the initial delivery but never settle it → an uncertain attempt.
        h.sink.no_settle = True
        await h.manager.dispatcher.dispatch_once()
        admitted = [
            d for d in await h.repo.list_deliveries(did) if d.state == "admitted"
        ]
        assert len(admitted) == 1 and admitted[0].acknowledged_at is None
        uncertain_id = admitted[0].delivery_id

        # DEFER recovery: readiness now denies admission. Reconcile must retain
        # the uncertain row untouched and mint neither recovery nor resume.
        reg.blocked = True
        await h.manager.reconcile(creature_id="worker")
        after_deferred = await h.repo.list_deliveries(did)
        assert len(after_deferred) == 1
        assert after_deferred[0].delivery_id == uncertain_id
        assert after_deferred[0].state == "admitted"  # retained, NOT superseded
        assert not any(d.reason in ("recovery", "resume") for d in after_deferred)

        # Clear the deferral: reconcile now supersedes the uncertain attempt and
        # enqueues recovery; dispatch delivers + acknowledges it.
        reg.blocked = False
        h.sink.no_settle = False
        await h.manager.reconcile(creature_id="worker")
        await h.manager.dispatcher.dispatch_once()
        await h.manager.dispatcher.drain()

        deliveries = await h.repo.list_deliveries(did)
        acked_recovery = [
            d
            for d in deliveries
            if d.reason == "recovery" and d.state == "acknowledged"
        ]
        assert len(acked_recovery) == 1
        assert any(
            d.delivery_id == uncertain_id and d.state == "superseded"
            for d in deliveries
        )
        assert not any(d.reason == "resume" for d in deliveries)
        event = h.sink.delivered[-1].event
        assert event.type == "drive_recovery"
        assert RECOVERY_WARNING in (event.prompt_override or "")

    async def test_rearm_delivery_admits_not_superseded(self):
        # The continuation delivery carries generation+1, so the §5.3 logical
        # dedupe admits it instead of superseding it behind the settled one.
        h = _cont_manager(max_turns=2)
        rec = await h.manager.create_drive(
            creature_request(kind="cont"), actor=WORKER, graph_id="g1"
        )
        did = rec.drive_id
        await _round(h)  # turn 1 -> re-arm turn 2
        await _round(h)  # turn 2 -> budget stops
        deliveries = await h.repo.list_deliveries(did)
        assert len(_acked(deliveries)) == 2
        assert not any(d.state == "superseded" for d in deliveries)
        gens = sorted(d.readiness_generation for d in deliveries)
        assert gens == [0, 1]

    async def test_readiness_cooldown_is_honored_exactly(self):
        clock = Clock()
        h = _cont_manager(config=make_config(readiness_cooldown_s=10), clock=clock)
        rec = await h.manager.create_drive(
            creature_request(kind="cont"), actor=WORKER, graph_id="g1"
        )
        did = rec.drive_id
        await _round(h)  # deliver+ack #1; the cooldown blocks an immediate re-arm
        assert len(await h.repo.list_deliveries(did)) == 1
        clock.advance(9)
        await h.manager._scan_ready()
        assert len(await h.repo.list_deliveries(did)) == 1  # still within cooldown
        clock.advance(1)  # exactly at the cooldown boundary
        await h.manager._scan_ready()
        assert len(await h.repo.list_deliveries(did)) == 2  # re-armed

    async def test_readiness_error_blocks_drive_no_delivery(self):
        # readiness raises only on continuation (turns_used>=1): the first turn
        # delivers, then the re-eval readiness() fails closed and blocks.
        h = _cont_manager(raise_after=1)
        rec = await h.manager.create_drive(
            creature_request(kind="cont"), actor=WORKER, graph_id="g1"
        )
        did = rec.drive_id
        await _round(h)  # deliver+ack #1, then the re-eval readiness() raises
        assert (await h.manager.get_drive(did)).status is DriveStatus.BLOCKED
        deliveries = await h.repo.list_deliveries(did)
        assert len(_acked(deliveries)) == 1  # only the first ever delivered
        assert len(deliveries) == 1  # no continuation delivery was minted
        assert "drive_readiness_error" in kinds(h.observations)


# ── §6.1 transaction-current recovery admission (round-3c concurrent gap) ──


class TestRecoveryAdmissionInTransaction:
    """Recovery admission is decided against TXN-CURRENT state, not a preflight.

    Round-3c made SEQUENTIAL deferred recovery correct. These pin the
    CONCURRENT / preflight-vs-commit gap: between the external ``_recovery_admitted``
    preflight and the ``build`` commit the Drive can be blocked or another
    reconcile can insert a recovery, so the in-txn re-check must re-read status,
    the assignment, live/recovery rows, backpressure, and re-validate the
    versioned readiness token before superseding the uncertain attempt.
    """

    async def _uncertain(self, h) -> tuple[str, str]:
        """Create a Drive and drive it to a genuine uncertain attempt (admitted,
        never acknowledged). Returns ``(drive_id, uncertain_delivery_id)``."""
        rec = await h.manager.create_drive(
            creature_request(), actor=WORKER, graph_id="g1"
        )
        did = rec.drive_id
        h.sink.no_settle = True
        await h.manager.dispatcher.dispatch_once()
        admitted = [
            d for d in await h.repo.list_deliveries(did) if d.state == "admitted"
        ]
        assert len(admitted) == 1 and admitted[0].acknowledged_at is None
        return did, admitted[0].delivery_id

    async def test_block_between_preflight_and_commit_defers_recovery(self):
        # (i) The Drive transitions to BLOCKED after the external preflight but
        # before the commit. The in-txn re-check must see the blocked status and
        # admit NO recovery; the uncertain intent is retained untouched.
        h = build_manager()
        did, uncertain_id = await self._uncertain(h)

        real = h.manager._recovery_admitted
        interposed = {"done": False}

        async def gated(*args, **kwargs):
            token = await real(*args, **kwargs)  # preflight against ACTIVE state
            if not interposed["done"]:
                interposed["done"] = True
                cur = await h.manager.get_drive(did)
                await h.repo.transition_drive(
                    did,
                    DriveStatus.BLOCKED,
                    expected_revision=cur.revision,
                    actor=SYSTEM_ACTOR,
                    operation="interpose_block",
                )
            return token

        h.manager._recovery_admitted = gated
        await h.manager.reconcile(creature_id="worker")

        deliveries = await h.repo.list_deliveries(did)
        assert not any(d.reason in ("recovery", "resume") for d in deliveries)
        retained = [d for d in deliveries if d.delivery_id == uncertain_id]
        assert len(retained) == 1
        assert retained[0].state == "admitted"  # uncertain intent retained
        assert (await h.manager.get_drive(did)).status is DriveStatus.BLOCKED

    async def test_concurrent_reconciles_admit_exactly_one_recovery(self):
        # (ii) Two reconciles race the SAME uncertain attempt: both clear the
        # external preflight before either commits. The in-txn unique recovery
        # key must let exactly ONE recovery through — no duplication.
        h = build_manager()
        did, uncertain_id = await self._uncertain(h)

        real = h.manager._recovery_admitted
        both_ready = asyncio.Event()
        seen = {"n": 0}

        async def gated(*args, **kwargs):
            # Preflight both reconciles against the pre-recovery state, then hold
            # until BOTH are past preflight so their commits genuinely race.
            token = await real(*args, **kwargs)
            seen["n"] += 1
            if seen["n"] >= 2:
                both_ready.set()
            await both_ready.wait()
            return token

        h.manager._recovery_admitted = gated
        await asyncio.gather(
            h.manager.reconcile(creature_id="worker"),
            h.manager.reconcile(creature_id="worker"),
        )

        deliveries = await h.repo.list_deliveries(did)
        recoveries = [d for d in deliveries if d.reason == "recovery"]
        assert len(recoveries) == 1  # unique recovery key enforced in-txn
        assert any(
            d.delivery_id == uncertain_id and d.state == "superseded"
            for d in deliveries
        )


# ── terminal-proposal persistence + stale approval (R1-08) ────────────


class TestProposalPersistence:
    def _two_party(self):
        return EnabledRegistrySnapshot.build([_VerifierReg("two_party")])

    def _sqlite_manager(self, path, snap, *, clock, seed):
        repo = SqliteDriveRepository(path, clock=clock, id_factory=ids(f"r{seed}"))
        manager = DriveManager(
            repo,
            snap,
            make_config(),
            FakeSink(),
            clock=clock,
            rng=ScriptedRng(),
            id_factory=ids(f"m{seed}"),
        )
        return repo, manager

    async def test_stale_revision_before_approval_conflicts(self):
        # A proposal is pinned to the revision at creation: any canonical change
        # before approval must conflict, never finalize the newer Drive.
        h = _verified_manager("two_party")
        did = await _worker_verified(h)
        proposal = await h.manager.propose_transition(
            did, DriveStatus.COMPLETED, actor=WORKER
        )
        await h.manager.update_drive(
            did, DrivePatch(title="changed"), expected_revision=1, actor=WORKER
        )
        with pytest.raises(DriveConflictError):
            await h.manager.approve_proposal(
                proposal.proposal_id, actor=ADMIN, is_privileged=True
            )
        assert (await h.manager.get_drive(did)).status is DriveStatus.ACTIVE

    async def test_proposal_survives_manager_resume(self, tmp_path):
        clock = Clock()
        path = str(tmp_path / "d.sqlite")
        snap = self._two_party()
        repo, m1 = self._sqlite_manager(path, snap, clock=clock, seed=1)
        try:
            rec = await m1.create_drive(
                creature_request(kind="verified"), actor=WORKER, graph_id="g1"
            )
            did = rec.drive_id
            proposal = await m1.propose_transition(
                did, DriveStatus.COMPLETED, actor=WORKER
            )
            pid = proposal.proposal_id
        finally:
            repo.close_blocking()

        repo2, m2 = self._sqlite_manager(path, snap, clock=clock, seed=2)
        try:
            await m2._rebuild_pending_proposals()
            record = await m2.approve_proposal(pid, actor=ADMIN, is_privileged=True)
            assert record.status is DriveStatus.COMPLETED
            assert await repo2.get_proposal(pid) is None  # removed on finalize
        finally:
            repo2.close_blocking()

    async def test_two_party_distinct_actor_holds_after_resume(self, tmp_path):
        clock = Clock()
        path = str(tmp_path / "d.sqlite")
        snap = self._two_party()
        repo, m1 = self._sqlite_manager(path, snap, clock=clock, seed=1)
        try:
            rec = await m1.create_drive(
                creature_request(kind="verified"), actor=WORKER, graph_id="g1"
            )
            did = rec.drive_id
            proposal = await m1.propose_transition(
                did, DriveStatus.COMPLETED, actor=ADMIN, is_privileged=True
            )
            pid = proposal.proposal_id
        finally:
            repo.close_blocking()

        repo2, m2 = self._sqlite_manager(path, snap, clock=clock, seed=2)
        try:
            await m2._rebuild_pending_proposals()
            # the resumed proposal remembers its proposer: same actor is rejected.
            with pytest.raises(DrivePermissionError):
                await m2.approve_proposal(pid, actor=ADMIN, is_privileged=True)
            record = await m2.approve_proposal(pid, actor=ADMIN2, is_privileged=True)
            assert record.status is DriveStatus.COMPLETED
        finally:
            repo2.close_blocking()

    async def test_proposal_removed_atomically_on_finalize(self):
        h = _verified_manager("two_party")
        did = await _worker_verified(h)
        proposal = await h.manager.propose_transition(
            did, DriveStatus.COMPLETED, actor=WORKER
        )
        await h.manager.approve_proposal(
            proposal.proposal_id, actor=ADMIN, is_privileged=True
        )
        assert await h.repo.get_proposal(proposal.proposal_id) is None
        assert proposal.proposal_id not in h.manager._pending_proposals


# ── registration-approved extra transitions (R1-25, design §3.3) ──────


class TestExtraTransitions:
    async def _complete(self, h, kind):
        rec = await h.manager.create_drive(
            creature_request(kind=kind), actor=WORKER, graph_id="g1"
        )
        did = rec.drive_id
        completed = await h.manager.propose_transition(
            did, DriveStatus.COMPLETED, actor=WORKER
        )
        assert completed.status is DriveStatus.COMPLETED
        return did, completed

    async def test_registration_extra_edge_allows_terminal_reopen(self):
        snap = EnabledRegistrySnapshot.build([_ReopenReg()])
        h = build_manager(snapshot=snap)
        did, completed = await self._complete(h, "reopen")
        reopened = await h.manager.transition(
            did, DriveStatus.ACTIVE, expected_revision=completed.revision, actor=WORKER
        )
        assert reopened.status is DriveStatus.ACTIVE
        assert reopened.revision > completed.revision

    async def test_generic_rejects_terminal_reopen(self):
        h = build_manager()  # generic declares no extra edges
        did, completed = await self._complete(h, "generic")
        with pytest.raises(DriveTransitionError):
            await h.manager.transition(
                did,
                DriveStatus.ACTIVE,
                expected_revision=completed.revision,
                actor=WORKER,
            )

    async def test_reopen_revision_fences_stale_delivery(self):
        from kohakuterrarium.terrarium.drive.policy import is_delivery_stale

        snap = EnabledRegistrySnapshot.build([_ReopenReg()])
        h = build_manager(snapshot=snap)
        rec = await h.manager.create_drive(
            creature_request(kind="reopen"), actor=WORKER, graph_id="g1"
        )
        did = rec.drive_id
        old_delivery = (await h.repo.list_deliveries(did))[0]
        completed = await h.manager.propose_transition(
            did, DriveStatus.COMPLETED, actor=WORKER
        )
        reopened = await h.manager.transition(
            did, DriveStatus.ACTIVE, expected_revision=completed.revision, actor=WORKER
        )
        assignment = await h.repo.get_assignment(did)
        # the pre-reopen delivery is fenced by the advanced revision.
        assert (
            is_delivery_stale(
                old_delivery,
                reopened,
                assignment,
                current_readiness_generation=0,
            )
            is True
        )


# ── central payload limits (R1-09, design §13) ───────────────────────


def _payload_bytes(size: int) -> dict:
    # A dict whose ``json.dumps(..., ensure_ascii=False)`` is exactly ``size``
    # bytes: fixed overhead of ``{"d": "..."}`` is 9 chars.
    return {"d": "a" * (size - 9)}


class TestPayloadLimits:
    """The manager enforces one central per-field byte + JSON-safety limit on
    create / update / proposal-evidence / progress (the manager cap is
    authoritative, independent of any registration's own cap)."""

    async def test_create_spec_at_limit_ok_over_rejected(self):
        h = build_manager(config=make_config(spec_max_bytes=100))
        await h.manager.create_drive(
            creature_request(spec=_payload_bytes(100)), actor=WORKER, graph_id="g1"
        )
        with pytest.raises(DriveValidationError):
            await h.manager.create_drive(
                creature_request(spec=_payload_bytes(101)), actor=WORKER, graph_id="g1"
            )

    async def test_create_presentation_and_metadata_limits(self):
        h = build_manager(
            config=make_config(presentation_max_bytes=60, metadata_max_bytes=40)
        )
        with pytest.raises(DriveValidationError):
            await h.manager.create_drive(
                creature_request(presentation=_payload_bytes(61)),
                actor=WORKER,
                graph_id="g1",
            )
        with pytest.raises(DriveValidationError):
            await h.manager.create_drive(
                creature_request(metadata=_payload_bytes(41)),
                actor=WORKER,
                graph_id="g1",
            )

    async def test_update_patch_spec_over_limit_rejected(self):
        h = build_manager(config=make_config(spec_max_bytes=100))
        rec = await h.manager.create_drive(
            creature_request(), actor=WORKER, graph_id="g1"
        )
        with pytest.raises(DriveValidationError):
            await h.manager.update_drive(
                rec.drive_id,
                DrivePatch(spec=_payload_bytes(101)),
                expected_revision=1,
                actor=WORKER,
            )

    async def test_progress_evidence_over_limit_rejected(self):
        h = build_manager(config=make_config(evidence_max_bytes=80))
        rec = await h.manager.create_drive(
            creature_request(), actor=WORKER, graph_id="g1"
        )
        with pytest.raises(DriveValidationError):
            await h.manager.report_progress(
                rec.drive_id,
                summary="s",
                evidence=_payload_bytes(81),
                actor=WORKER,
            )

    async def test_proposal_evidence_over_limit_rejected(self):
        h = build_manager(config=make_config(evidence_max_bytes=80))
        rec = await h.manager.create_drive(
            creature_request(), actor=WORKER, graph_id="g1"
        )
        with pytest.raises(DriveValidationError):
            await h.manager.propose_transition(
                rec.drive_id,
                DriveStatus.COMPLETED,
                actor=WORKER,
                evidence=_payload_bytes(81),
            )

    async def test_non_json_safe_payload_rejected(self):
        h = build_manager()
        with pytest.raises(DriveValidationError):
            await h.manager.create_drive(
                creature_request(metadata={"bad": object()}),
                actor=WORKER,
                graph_id="g1",
            )

    async def test_runtime_cap_above_generic_default_is_authoritative(self):
        # R1-09: a runtime spec cap LARGER than the generic registration's
        # historical independent default must be effective — the generic no
        # longer imposes a lower cap, so a spec within the runtime cap is
        # accepted and only limit+1 is rejected.
        big = DEFAULT_SPEC_MAX_BYTES + 4000
        h = build_manager(config=make_config(spec_max_bytes=big))
        await h.manager.create_drive(
            creature_request(spec=_payload_bytes(big)), actor=WORKER, graph_id="g1"
        )
        with pytest.raises(DriveValidationError):
            await h.manager.create_drive(
                creature_request(spec=_payload_bytes(big + 1)),
                actor=WORKER,
                graph_id="g1",
            )

    async def test_every_field_at_limit_ok_over_rejected(self):
        # R1-09: each configured payload field is capped at exactly the runtime
        # limit on create; ``policy_options`` is bounded by the metadata cap.
        h = build_manager(
            config=make_config(
                spec_max_bytes=120,
                presentation_max_bytes=110,
                metadata_max_bytes=100,
            )
        )
        await h.manager.create_drive(
            creature_request(
                spec=_payload_bytes(120),
                presentation=_payload_bytes(110),
                metadata=_payload_bytes(100),
                policy_options=_payload_bytes(100),
            ),
            actor=WORKER,
            graph_id="g1",
        )
        for field, over in (
            ("spec", 121),
            ("presentation", 111),
            ("metadata", 101),
            ("policy_options", 101),
        ):
            with pytest.raises(DriveValidationError):
                await h.manager.create_drive(
                    creature_request(**{field: _payload_bytes(over)}),
                    actor=WORKER,
                    graph_id="g1",
                )

    async def test_update_presentation_and_metadata_at_limit_ok_over_rejected(self):
        # R1-09: the patch path caps presentation/metadata at the same runtime
        # limits (at-limit accepted, limit+1 rejected).
        h = build_manager(
            config=make_config(presentation_max_bytes=110, metadata_max_bytes=100)
        )
        rec = await h.manager.create_drive(
            creature_request(), actor=WORKER, graph_id="g1"
        )
        ok = await h.manager.update_drive(
            rec.drive_id,
            DrivePatch(presentation=_payload_bytes(110), metadata=_payload_bytes(100)),
            expected_revision=1,
            actor=WORKER,
        )
        assert ok.revision == 2
        with pytest.raises(DriveValidationError):
            await h.manager.update_drive(
                rec.drive_id,
                DrivePatch(presentation=_payload_bytes(111)),
                expected_revision=2,
                actor=WORKER,
            )
        with pytest.raises(DriveValidationError):
            await h.manager.update_drive(
                rec.drive_id,
                DrivePatch(metadata=_payload_bytes(101)),
                expected_revision=2,
                actor=WORKER,
            )

    async def test_progress_and_proposal_evidence_at_limit_ok(self):
        # R1-09: evidence on the progress AND terminal-proposal paths accepts an
        # at-limit payload and rejects limit+1.
        h = build_manager(config=make_config(evidence_max_bytes=90))
        rec = await h.manager.create_drive(
            creature_request(), actor=WORKER, graph_id="g1"
        )
        await h.manager.report_progress(
            rec.drive_id, summary="s", evidence=_payload_bytes(90), actor=WORKER
        )
        with pytest.raises(DriveValidationError):
            await h.manager.report_progress(
                rec.drive_id, summary="s", evidence=_payload_bytes(91), actor=WORKER
            )
        with pytest.raises(DriveValidationError):
            await h.manager.propose_transition(
                rec.drive_id,
                DriveStatus.COMPLETED,
                actor=WORKER,
                evidence=_payload_bytes(91),
            )


# ── admission-time readiness (R1-05, design §5.2) ─────────────────────


class TestReadinessAdmission:
    """A registration's readiness governs whether a Drive earns a delivery at
    admission (create / scan), separate from the post-settlement ``re_arm``
    continuation, with a manual wake as an explicit override."""

    async def test_unready_registration_not_admitted_on_create(self):
        snap = EnabledRegistrySnapshot.build([_NotReadyReg()])
        h = build_manager(snapshot=snap)
        rec = await h.manager.create_drive(
            creature_request(kind="notready"), actor=WORKER, graph_id="g1"
        )
        # readiness=False and no initial grant: no delivery is minted on create.
        assert await h.repo.list_deliveries(rec.drive_id) == ()

    async def test_unready_registration_not_admitted_on_scan(self):
        snap = EnabledRegistrySnapshot.build([_NotReadyReg()])
        h = build_manager(snapshot=snap)
        rec = await h.manager.create_drive(
            creature_request(kind="notready"), actor=WORKER, graph_id="g1"
        )
        await h.manager._scan_ready()
        assert await h.repo.list_deliveries(rec.drive_id) == ()

    async def test_ready_registration_delivers_once(self):
        # generic readiness is trivially ready: exactly one initial delivery.
        h = build_manager()
        rec = await h.manager.create_drive(
            creature_request(), actor=WORKER, graph_id="g1"
        )
        await _round(h)
        assert len(_acked(await h.repo.list_deliveries(rec.drive_id))) == 1

    async def test_readiness_error_at_create_blocks_before_any_delivery(self):
        # A readiness that fails at the very first admission fails closed: the
        # Drive is blocked and NO initial delivery escapes.
        h = _cont_manager(raise_exc=True)
        rec = await h.manager.create_drive(
            creature_request(kind="cont"), actor=WORKER, graph_id="g1"
        )
        did = rec.drive_id
        assert await h.repo.list_deliveries(did) == ()
        assert (await h.manager.get_drive(did)).status is DriveStatus.BLOCKED
        assert "drive_readiness_error" in kinds(h.observations)

    async def test_goal_manual_delivers_initial_but_not_continuation(self):
        from kohakuterrarium.terrarium.drive.goal import GoalDriveRegistration

        snap = EnabledRegistrySnapshot.build([GoalDriveRegistration()])
        h = build_manager(snapshot=snap)
        rec = await h.manager.create_drive(
            creature_request(
                kind="goal", spec={"objective": "watch", "autonomy": "manual"}
            ),
            actor=WORKER,
            graph_id="g1",
        )
        did = rec.drive_id
        # manual mode grants exactly one initial event, then never re-arms.
        for _ in range(4):
            await _round(h)
        assert len(_acked(await h.repo.list_deliveries(did))) == 1
        assert (await h.manager.get_drive(did)).status is DriveStatus.ACTIVE

    async def test_goal_continue_delivers_and_rearms(self):
        from kohakuterrarium.terrarium.drive.goal import GoalDriveRegistration

        snap = EnabledRegistrySnapshot.build([GoalDriveRegistration()])
        h = build_manager(snapshot=snap)
        rec = await h.manager.create_drive(
            creature_request(
                kind="goal",
                spec={
                    "objective": "watch",
                    "autonomy": "continue_when_ready",
                    "budgets": {"max_turns": 2},
                },
            ),
            actor=WORKER,
            graph_id="g1",
        )
        did = rec.drive_id
        for _ in range(4):
            await _round(h)
        assert len(_acked(await h.repo.list_deliveries(did))) == 2  # budget caps at 2

    async def test_manual_wake_overrides_unready_registration(self):
        # A manual wake is an explicit authorized override: it delivers even to
        # a registration whose readiness is false.
        snap = EnabledRegistrySnapshot.build([_NotReadyReg()])
        h = build_manager(snapshot=snap)
        rec = await h.manager.create_drive(
            creature_request(kind="notready"), actor=WORKER, graph_id="g1"
        )
        did = rec.drive_id
        assert await h.repo.list_deliveries(did) == ()
        await h.manager.wake_drive(did, actor=WORKER)
        await _round(h)
        acked = _acked(await h.repo.list_deliveries(did))
        assert len(acked) == 1
        assert any(d.reason == "manual_wake" for d in acked)

    async def test_generation_map_rebuilds_after_reopen(self, tmp_path):
        clock = Clock()
        path = str(tmp_path / "d.sqlite")
        snap = EnabledRegistrySnapshot.build([_ContinueReg(max_turns=3)])
        repo = SqliteDriveRepository(path, clock=clock, id_factory=ids("r"))
        manager = DriveManager(
            repo,
            snap,
            make_config(),
            FakeSink(),
            clock=clock,
            rng=ScriptedRng(),
            id_factory=ids("m"),
        )
        try:
            rec = await manager.create_drive(
                creature_request(kind="cont"), actor=WORKER, graph_id="g1"
            )
            did = rec.drive_id
            for _ in range(3):
                await manager.dispatcher.dispatch_once()
                await manager.dispatcher.drain()
            deliveries = await repo.list_deliveries(did)
            max_gen = max(d.readiness_generation for d in deliveries)
            assert max_gen >= 1
        finally:
            repo.close_blocking()

        reopened = SqliteDriveRepository(path, clock=clock, id_factory=ids("r2"))
        try:
            fresh = DriveManager(
                reopened,
                snap,
                make_config(),
                FakeSink(),
                clock=clock,
                rng=ScriptedRng(),
                id_factory=ids("m2"),
            )
            assert fresh._current_readiness_generation(did) == 0  # empty pre-rebuild
            await fresh._rebuild_readiness_generations()
            assert fresh._current_readiness_generation(did) == max_gen
        finally:
            reopened.close_blocking()


# ── operator capability elevation (design §3.6, §13) ──────────────────


class TestOperatorGrant:
    async def test_non_operator_create_for_graph_denied(self):
        # Nothing in the request payload confers graph authority; a plain user
        # actor creating a graph-owned Drive is denied without an elevation.
        h = build_manager()
        with pytest.raises(DrivePermissionError):
            await h.manager.create_drive(
                graph_request(), actor=USER, graph_id="g1", operator=False
            )

    async def test_operator_elevation_allows_create_and_is_audited(self):
        h = build_manager()
        rec = await h.manager.create_drive(
            graph_request(), actor=USER, graph_id="g1", operator=True
        )
        assert rec.owner == USER
        audit_ops = [a.operation for a in await h.repo.list_audit(rec.drive_id)]
        assert "operator_grant" in audit_ops

    async def test_operator_elevation_allows_assign_and_is_audited(self):
        h = build_manager()
        rec = await h.manager.create_drive(
            graph_request(), actor=WORKER, graph_id="g1", is_privileged=True
        )
        did = rec.drive_id
        # the owner (USER) lacks the ASSIGN capability; assignment is graph authority
        with pytest.raises(DrivePermissionError):
            await h.manager.assign(did, "other", "g1", expected_revision=1, actor=USER)
        await h.manager.assign(
            did, "other", "g1", expected_revision=1, actor=USER, operator=True
        )
        assert "operator_grant" in [a.operation for a in await h.repo.list_audit(did)]

    async def test_operator_flag_defaults_off(self):
        # is_privileged means creature privilege only; operator is a separate,
        # explicit lever. A plain create writes no operator_grant audit row.
        h = build_manager()
        rec = await h.manager.create_drive(
            creature_request(), actor=WORKER, graph_id="g1"
        )
        assert "operator_grant" not in [
            a.operation for a in await h.repo.list_audit(rec.drive_id)
        ]

    # -- R1-40: operator-grant audit rides the elevated mutation's txn --------
    #
    # These fault-inject the OLD separate-audit path: if the operator_grant
    # audit were still a second transaction it would go through `_write_audit`
    # and raise here, either failing the op after the mutation already committed
    # (create/assign) or committing the audit before a failed finalize
    # (approve). Riding the same mutation, none of these call `_write_audit`.

    def _forbid_separate_audit(self, manager):
        async def _boom(*a, **k):
            raise AssertionError(
                "operator grant must ride the mutation, not _write_audit"
            )

        manager._write_audit = _boom

    async def test_operator_grant_create_is_atomic(self):
        h = build_manager()
        self._forbid_separate_audit(h.manager)
        rec = await h.manager.create_drive(
            graph_request(), actor=USER, graph_id="g1", operator=True
        )
        audits = await h.repo.list_audit(rec.drive_id)
        ops = [a.operation for a in audits]
        assert "create" in ops and "operator_grant" in ops
        grant = next(a for a in audits if a.operation == "operator_grant")
        assert grant.revision == rec.revision  # same committed revision

    async def test_operator_grant_assign_is_atomic(self):
        h = build_manager()
        rec = await h.manager.create_drive(
            graph_request(), actor=WORKER, graph_id="g1", is_privileged=True
        )
        did = rec.drive_id
        self._forbid_separate_audit(h.manager)
        record = await h.manager.assign(
            did, "other", "g1", expected_revision=1, actor=USER, operator=True
        )
        grant = next(
            a for a in await h.repo.list_audit(did) if a.operation == "operator_grant"
        )
        assert grant.revision == record.revision

    async def test_operator_grant_approve_is_atomic(self):
        h = _verified_manager("two_party")
        did = await _worker_verified(h)  # verified-kind, owned + proposable by WORKER
        proposal = await h.manager.propose_transition(
            did, DriveStatus.COMPLETED, actor=WORKER
        )
        self._forbid_separate_audit(h.manager)
        record = await h.manager.approve_proposal(
            proposal.proposal_id, actor=USER, operator=True
        )
        assert record.status is DriveStatus.COMPLETED
        grant = next(
            a for a in await h.repo.list_audit(did) if a.operation == "operator_grant"
        )
        # the grant is committed with the terminal transition, not before it.
        assert grant.revision == record.revision
