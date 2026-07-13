"""Unit tests for :mod:`terrarium.drive.tools` — the five self-service tools.

Each tool is exercised against a REAL Drive-enabled engine + DriveManager +
in-memory repository (the only stub is the LLM-free fake agent). Pins: happy
path, ACL denial for a foreign-owned assigned drive, CAS conflict, disabled
kind, and — the security invariant — the ActorRef comes from the trusted caller
context, never from tool arguments.
"""

from pathlib import Path

import pytest

from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.terrarium.creature_host import Creature
from kohakuterrarium.terrarium.drive.config import (
    DriveRuntimeConfig,
    default_registrations,
)
from kohakuterrarium.terrarium.drive.models import ActorRef
from kohakuterrarium.terrarium.drive.requests import CreateDriveRequest
from kohakuterrarium.terrarium.drive.tools import (
    DriveCreateTool,
    DriveReportTool,
    DriveStatusTool,
    DriveTransitionTool,
    DriveUpdateTool,
)
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.testing.terrarium import _FakeAgent

pytestmark = pytest.mark.timeout(30)

USER = ActorRef("user", "alice")


async def _engine_with_worker():
    engine = Terrarium(
        drive_config=DriveRuntimeConfig(enabled=True),
        drive_registrations=default_registrations(),
    )
    await engine.__aenter__()
    creature = Creature(
        creature_id="worker", name="worker", agent=_FakeAgent(name="worker")
    )
    await engine.add_creature(creature)
    return engine, creature


def _ctx(engine, creature) -> ToolContext:
    env = engine._environments[creature.graph_id]
    return ToolContext(
        agent_name=creature.name,
        session=None,
        working_dir=Path("."),
        environment=env,
    )


class TestDriveCreate:
    async def test_creates_caller_owned_drive(self):
        engine, worker = await _engine_with_worker()
        try:
            ctx = _ctx(engine, worker)
            res = await DriveCreateTool()._execute(
                {"title": "watch the deploy", "spec": {"instruction": "monitor"}},
                context=ctx,
            )
            assert res.error is None, res.error
            body = res.metadata["drive"]
            assert body["kind"] == "generic"
            assert body["status"] == "active"
            assert body["owner"] == "creature:worker"
            assert body["scope_type"] == "creature"
            # The caller can manage its own drive.
            assert "update" in body["allowed_actions"]
            assert res.output.startswith(f"Drive {body['drive_id']}: watch the deploy")
            assert not res.output.lstrip().startswith("{")
        finally:
            await engine.shutdown()

    async def test_actor_is_never_taken_from_args(self):
        # Passing owner/actor/scope in args must be ignored — ownership is
        # forced to the trusted caller identity (design §9.3, rule §4.15).
        engine, worker = await _engine_with_worker()
        try:
            ctx = _ctx(engine, worker)
            res = await DriveCreateTool()._execute(
                {
                    "title": "sneaky",
                    "owner": "user:root",
                    "actor": "service:admin",
                    "scope_type": "graph",
                    "scope_id": "some_other_graph",
                },
                context=ctx,
            )
            assert res.error is None, res.error
            body = res.metadata["drive"]
            record = await engine.drives.manager.get_drive(body["drive_id"])
            assert record.owner == ActorRef("creature", "worker")
            assert record.created_by == ActorRef("creature", "worker")
            assert record.scope_type == "creature"
            assert record.scope_id == "worker"
        finally:
            await engine.shutdown()

    async def test_goal_kind_validates_its_spec(self):
        engine, worker = await _engine_with_worker()
        try:
            ctx = _ctx(engine, worker)
            res = await DriveCreateTool()._execute(
                {"title": "goal drive", "kind": "goal"}, context=ctx
            )
            assert res.error is not None
            assert "objective" in res.error
        finally:
            await engine.shutdown()

    async def test_missing_title_rejected(self):
        engine, worker = await _engine_with_worker()
        try:
            res = await DriveCreateTool()._execute({}, context=_ctx(engine, worker))
            assert res.error is not None and "title" in res.error
        finally:
            await engine.shutdown()

    async def test_no_drive_runtime_fails_closed(self):
        # A ToolContext whose environment lacks the Drive service handle
        # (Drive-disabled engine) fails closed.
        engine = Terrarium(drive_config=DriveRuntimeConfig(enabled=False))
        await engine.__aenter__()
        try:
            creature = Creature(
                creature_id="solo", name="solo", agent=_FakeAgent(name="solo")
            )
            await engine.add_creature(creature)
            res = await DriveCreateTool()._execute(
                {"title": "x"}, context=_ctx(engine, creature)
            )
            assert res.error is not None
            assert "Drive runtime is not enabled" in res.error
        finally:
            await engine.shutdown()


class TestDriveUpdateStatusReport:
    async def test_update_happy_and_cas_conflict(self):
        engine, worker = await _engine_with_worker()
        try:
            ctx = _ctx(engine, worker)
            created = (
                await DriveCreateTool()._execute({"title": "t"}, context=ctx)
            ).metadata["drive"]
            did, rev = created["drive_id"], created["revision"]
            # Happy update at the current revision.
            ok = await DriveUpdateTool()._execute(
                {"drive_id": did, "expected_revision": rev, "title": "renamed"},
                context=ctx,
            )
            assert ok.error is None, ok.error
            assert ok.metadata["drive"]["title"] == "renamed"
            # Stale revision -> distinct conflict error.
            conflict = await DriveUpdateTool()._execute(
                {"drive_id": did, "expected_revision": rev, "title": "again"},
                context=ctx,
            )
            assert conflict.error is not None
            assert "conflict" in conflict.error
        finally:
            await engine.shutdown()

    async def test_status_lists_owned_drive(self):
        engine, worker = await _engine_with_worker()
        try:
            ctx = _ctx(engine, worker)
            created = (
                await DriveCreateTool()._execute({"title": "listme"}, context=ctx)
            ).metadata["drive"]
            res = await DriveStatusTool()._execute({}, context=ctx)
            assert res.error is None
            body = res.metadata["drive"]
            ids = {d["drive_id"] for d in body["drives"]}
            assert created["drive_id"] in ids
            # get-by-id path.
            one = await DriveStatusTool()._execute(
                {"drive_id": created["drive_id"]}, context=ctx
            )
            assert one.metadata["drive"]["drive_id"] == created["drive_id"]
        finally:
            await engine.shutdown()

    async def test_report_progress(self):
        engine, worker = await _engine_with_worker()
        try:
            ctx = _ctx(engine, worker)
            created = (
                await DriveCreateTool()._execute({"title": "t"}, context=ctx)
            ).metadata["drive"]
            res = await DriveReportTool()._execute(
                {
                    "drive_id": created["drive_id"],
                    "summary": "halfway",
                    "evidence": {"pct": 50},
                },
                context=ctx,
            )
            assert res.error is None, res.error
            assert "progress_id" in res.metadata["drive"]
        finally:
            await engine.shutdown()


class TestDriveTransition:
    async def test_pause_then_propose_completion(self):
        engine, worker = await _engine_with_worker()
        try:
            ctx = _ctx(engine, worker)
            created = (
                await DriveCreateTool()._execute({"title": "t"}, context=ctx)
            ).metadata["drive"]
            did = created["drive_id"]
            # Control transition needs expected_revision.
            paused = await DriveTransitionTool()._execute(
                {
                    "drive_id": did,
                    "status": "paused",
                    "expected_revision": created["revision"],
                },
                context=ctx,
            )
            assert paused.error is None, paused.error
            assert paused.metadata["drive"]["status"] == "paused"
            # Resume, then propose completion (generic kind = verifier none ->
            # accepted immediately).
            record = await engine.drives.manager.get_drive(did)
            await DriveTransitionTool()._execute(
                {
                    "drive_id": did,
                    "status": "active",
                    "expected_revision": record.revision,
                },
                context=ctx,
            )
            record = await engine.drives.manager.get_drive(did)
            done = await DriveTransitionTool()._execute(
                {
                    "drive_id": did,
                    "status": "completed",
                    "expected_revision": record.revision,
                    "evidence": {"done": True},
                },
                context=ctx,
            )
            assert done.error is None, done.error
            body = done.metadata["drive"]
            assert body["proposal"] == "accepted"
            assert body["status"] == "completed"
        finally:
            await engine.shutdown()

    async def test_control_transition_requires_revision(self):
        engine, worker = await _engine_with_worker()
        try:
            ctx = _ctx(engine, worker)
            created = (
                await DriveCreateTool()._execute({"title": "t"}, context=ctx)
            ).metadata["drive"]
            res = await DriveTransitionTool()._execute(
                {"drive_id": created["drive_id"], "status": "paused"}, context=ctx
            )
            assert res.error is not None and "expected_revision" in res.error
        finally:
            await engine.shutdown()


class TestForeignOwnedAcl:
    async def test_assignee_can_report_but_not_cancel(self):
        # A user-owned drive assigned to the worker: the worker (assignee)
        # may report/propose but NOT cancel it (owner-only management).
        engine, worker = await _engine_with_worker()
        try:
            record = await engine.drives.manager.create_drive(
                CreateDriveRequest(
                    kind="generic",
                    title="user goal",
                    scope_type="graph",
                    scope_id=worker.graph_id,
                    owner=USER,
                    owner_scope="graph",
                    created_by=USER,
                    assignee_creature_id="worker",
                ),
                actor=USER,
                graph_id=worker.graph_id,
                is_privileged=True,
            )
            ctx = _ctx(engine, worker)
            # Report is allowed for the assignee.
            rep = await DriveReportTool()._execute(
                {"drive_id": record.drive_id, "summary": "on it"}, context=ctx
            )
            assert rep.error is None, rep.error
            # Cancelling a foreign-owned drive is denied.
            cancel = await DriveTransitionTool()._execute(
                {
                    "drive_id": record.drive_id,
                    "status": "cancelled",
                    "expected_revision": record.revision,
                },
                context=ctx,
            )
            assert cancel.error is not None
            assert "permission denied" in cancel.error
            # Updating a foreign-owned drive is denied too.
            upd = await DriveUpdateTool()._execute(
                {
                    "drive_id": record.drive_id,
                    "expected_revision": record.revision,
                    "title": "hijack",
                },
                context=ctx,
            )
            assert upd.error is not None and "permission denied" in upd.error
        finally:
            await engine.shutdown()


# ── per-record durability in a mixed engine (R1-41) ───────────────────


class TestPerRecordDurability:
    async def test_status_reports_per_graph_durability_not_aggregate(self, tmp_path):
        # A mixed engine (one persistent graph + one ephemeral) must label each
        # record with ITS graph's durability, never the aggregate "mixed" (R1-41).
        engine = Terrarium(
            drive_config=DriveRuntimeConfig(enabled=True),
            drive_registrations=default_registrations(),
        )
        await engine.__aenter__()
        try:
            worker = Creature(
                creature_id="worker", name="worker", agent=_FakeAgent(name="worker")
            )
            await engine.add_creature(worker)  # ephemeral graph (no store)
            keeper = Creature(
                creature_id="keeper", name="keeper", agent=_FakeAgent(name="keeper")
            )
            await engine.add_creature(
                keeper, session=str(tmp_path / "keeper.kohakutr")
            )  # a separate, persistent graph
            assert worker.graph_id != keeper.graph_id
            assert engine.drives.durability == "mixed"  # setup sanity: genuinely mixed
            ctx = _ctx(engine, worker)
            created = await DriveCreateTool()._execute({"title": "watch"}, context=ctx)
            assert created.error is None, created.error
            body = created.metadata["drive"]
            assert body["durability"] == "ephemeral"  # worker's graph, not "mixed"
            got = await DriveStatusTool()._execute(
                {"drive_id": body["drive_id"]}, context=ctx
            )
            assert got.metadata["drive"]["durability"] == "ephemeral"
        finally:
            await engine.shutdown()
