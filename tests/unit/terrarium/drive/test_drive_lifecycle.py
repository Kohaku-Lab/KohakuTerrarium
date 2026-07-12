"""Behaviour contract for creature-lifecycle reconciliation (design §6.1-6.3).

Stop (defer, no failure count), removal (orphan-and-block for creature scope,
unassign or deterministic auto-assign for graph scope, explicit cancel), and the
restoration-ready reconcile, plus the pure classification helpers.
"""

from kohakuterrarium.terrarium.drive.lifecycle import (
    classify_uncertain,
    plan_removal,
    select_auto_assignee,
)
from kohakuterrarium.terrarium.drive.models import DriveStatus

from tests.unit.terrarium.drive._harness import (
    WORKER,
    build_manager,
    creature_request,
    graph_request,
    kinds,
)


async def _worker_drive(h) -> str:
    rec = await h.manager.create_drive(creature_request(), actor=WORKER, graph_id="g1")
    return rec.drive_id


async def _graph_drive(h) -> str:
    rec = await h.manager.create_drive(
        graph_request(), actor=WORKER, graph_id="g1", is_privileged=True
    )
    return rec.drive_id


# ── pure classification ───────────────────────────────────────────────


class TestClassification:
    async def test_classify_uncertain(self):
        h = build_manager()
        did = await _worker_drive(h)
        pending = (await h.repo.list_deliveries(did))[0]
        assert classify_uncertain(pending) is False
        await h.repo.mark_delivery(pending.delivery_id, "admitted")
        assert classify_uncertain((await h.repo.list_deliveries(did))[0]) is True
        await h.repo.mark_delivery(pending.delivery_id, "acknowledged")
        assert classify_uncertain((await h.repo.list_deliveries(did))[0]) is False

    async def test_plan_removal_creature_vs_graph(self):
        h = build_manager()
        creature_rec = await h.manager.get_drive(await _worker_drive(h))
        graph_rec = await h.manager.get_drive(await _graph_drive(h))
        creature_plan = plan_removal(creature_rec)
        assert creature_plan.assignment_state == "orphaned"
        assert creature_plan.drive_status is DriveStatus.BLOCKED
        graph_plan = plan_removal(graph_rec, auto_assign=True)
        assert graph_plan.assignment_state == "unassigned"
        assert graph_plan.reassign is True

    def test_select_auto_assignee_is_deterministic(self):
        assert select_auto_assignee(frozenset({"b", "a"}), exclude="b") == "a"
        assert select_auto_assignee(frozenset({"b"}), exclude="b") is None


# ── stop (temporary) ──────────────────────────────────────────────────


class TestStopped:
    async def test_stop_defers_claim_without_failure_count(self):
        h = build_manager()
        did = await _worker_drive(h)
        await h.repo.claim_deliveries("drive-dispatcher", 10, lease_seconds=30)
        assert (await h.repo.list_deliveries(did))[0].state == "claimed"
        await h.manager.on_creature_stopped("worker")
        delivery = (await h.repo.list_deliveries(did))[0]
        assert delivery.state == "pending" and delivery.attempt == 0


# ── removal (permanent) ───────────────────────────────────────────────


class TestRemoved:
    async def test_creature_scoped_orphans_and_blocks(self):
        h = build_manager()
        did = await _worker_drive(h)
        await h.manager.on_creature_removed("worker")
        assert (await h.manager.get_drive(did)).status is DriveStatus.BLOCKED
        assert (await h.repo.get_assignment(did)).assignment_state == "orphaned"
        assert "drive_orphaned" in kinds(h.observations)

    async def test_graph_scoped_manual_unassigns(self):
        h = build_manager()
        did = await _graph_drive(h)
        await h.manager.on_creature_removed(
            "worker", graph_member_ids=frozenset({"other"})
        )
        assignment = await h.repo.get_assignment(did)
        assert assignment.assignment_state == "unassigned"
        assert (await h.manager.get_drive(did)).status is DriveStatus.ACTIVE

    async def test_graph_scoped_auto_assign_picks_member(self):
        h = build_manager()
        did = await _graph_drive(h)
        await h.manager.on_creature_removed(
            "worker", graph_member_ids=frozenset({"other"}), auto_assign=True
        )
        assignment = await h.repo.get_assignment(did)
        assert assignment.assignee_creature_id == "other"
        assert assignment.assignment_state == "assigned"

    async def test_cancel_policy_cancels(self):
        h = build_manager()
        did = await _worker_drive(h)
        await h.manager.on_creature_removed("worker", on_assignee_removed="cancel")
        assert (await h.manager.get_drive(did)).status is DriveStatus.CANCELLED


# ── restoration-ready reconcile ───────────────────────────────────────


class TestRestorationReady:
    async def test_restoration_ready_resumes_active_drive(self):
        h = build_manager()
        did = await _worker_drive(h)
        await h.manager.dispatcher.dispatch_once()
        await h.manager.dispatcher.drain()
        await h.manager.on_creature_restoration_ready("worker")
        resume = [d for d in await h.repo.list_deliveries(did) if d.reason == "resume"]
        assert len(resume) == 1
