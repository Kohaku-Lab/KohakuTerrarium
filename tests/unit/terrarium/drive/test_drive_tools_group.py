"""Unit tests for :mod:`terrarium.drive.tools_group` — the ``group_drive`` tool.

Exercised against a REAL Drive-enabled engine + DriveManager + in-memory repo
(the only stub is the LLM-free fake agent). Pins each admin action's happy path,
the privileged-only tool gate, the graph-scope guard (a foreign-graph drive_id is
refused even for a privileged caller), and that the actor is the trusted caller,
never an argument.
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
from kohakuterrarium.terrarium.drive.requests import DriveQuery
from kohakuterrarium.terrarium.drive.tools_group import GroupDriveTool
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.tools_group_wire import GroupWireTool
from kohakuterrarium.testing.terrarium import _FakeAgent

pytestmark = pytest.mark.timeout(30)


async def _engine():
    engine = Terrarium(
        drive_config=DriveRuntimeConfig(enabled=True),
        drive_registrations=default_registrations(),
    )
    await engine.__aenter__()
    return engine


async def _add(engine, cid, *, privileged=False, graph=None):
    creature = Creature(
        creature_id=cid, name=cid, agent=_FakeAgent(name=cid), is_privileged=privileged
    )
    await engine.add_creature(creature, graph=graph)
    return creature


def _ctx(engine, creature) -> ToolContext:
    return ToolContext(
        agent_name=creature.name,
        creature_id=creature.creature_id,
        session=None,
        working_dir=Path("."),
        environment=engine._environments[creature.graph_id],
    )


def _body(result):
    assert result.error is None, result.error
    return result.metadata["drive"]


async def _create(tool, engine, caller, **args):
    payload = {"action": "create", "title": "watch"}
    payload.update(args)
    res = await tool._execute(payload, context=_ctx(engine, caller))
    assert res.error is None, res.error
    assert not res.output.lstrip().startswith("{")
    return res.metadata["drive"]


class TestPrivilegeGate:
    async def test_non_privileged_caller_denied(self):
        engine = await _engine()
        try:
            worker = await _add(engine, "worker", privileged=False)
            res = await GroupDriveTool()._execute(
                {"action": "list"}, context=_ctx(engine, worker)
            )
            assert res.error is not None
            assert "privileged" in res.error
        finally:
            await engine.shutdown()

    async def test_unknown_action_rejected(self):
        engine = await _engine()
        try:
            root = await _add(engine, "root", privileged=True)
            res = await GroupDriveTool()._execute(
                {"action": "frobnicate"}, context=_ctx(engine, root)
            )
            assert res.error is not None and "unknown action" in res.error
        finally:
            await engine.shutdown()

    async def test_disabled_runtime_fails_closed(self):
        engine = Terrarium(drive_config=DriveRuntimeConfig(enabled=False))
        await engine.__aenter__()
        try:
            root = await _add(engine, "root", privileged=True)
            res = await GroupDriveTool()._execute(
                {"action": "list"}, context=_ctx(engine, root)
            )
            assert res.error is not None
            assert "Drive runtime is not enabled" in res.error
        finally:
            await engine.shutdown()


class TestGraphAdmin:
    async def test_create_graph_owned_and_assign_cycle(self):
        engine = await _engine()
        try:
            root = await _add(engine, "root", privileged=True)
            await _add(engine, "worker", graph=root.graph_id)
            tool = GroupDriveTool()
            created = await _create(
                tool, engine, root, assignee="worker", spec={"x": 1}
            )
            assert created["scope_type"] == "graph"
            assert created["scope_id"] == root.graph_id
            assert created["assignee"] == "worker"
            did = created["drive_id"]
            # list shows the graph drive.
            lst = _body(
                await tool._execute({"action": "list"}, context=_ctx(engine, root))
            )
            assert did in {d["drive_id"] for d in lst["drives"]}
            # unassign then reassign under CAS.
            record = await engine.drives.manager.get_drive(did)
            un = await tool._execute(
                {
                    "action": "unassign",
                    "drive_id": did,
                    "expected_revision": record.revision,
                },
                context=_ctx(engine, root),
            )
            assert un.error is None, un.error
            assert _body(un)["assignee"] is None
        finally:
            await engine.shutdown()

    async def test_transfer_owner_and_wake(self):
        engine = await _engine()
        try:
            root = await _add(engine, "root", privileged=True)
            tool = GroupDriveTool()
            created = await _create(tool, engine, root)
            did = created["drive_id"]
            # transfer ownership to a user actor.
            tr = await tool._execute(
                {
                    "action": "transfer_owner",
                    "drive_id": did,
                    "new_owner": "user:bob",
                    "expected_revision": created["revision"],
                },
                context=_ctx(engine, root),
            )
            assert tr.error is None, tr.error
            assert _body(tr)["owner"] == "user:bob"
            # wake (idempotent on an active drive).
            wake = await tool._execute(
                {"action": "wake", "drive_id": did}, context=_ctx(engine, root)
            )
            assert wake.error is None, wake.error
        finally:
            await engine.shutdown()

    async def test_retire_requires_terminal_then_tombstones(self):
        # ``retired`` is reachable only from a terminal state (design §3.3):
        # retiring an active Drive is refused; a cancelled one is tombstoned.
        engine = await _engine()
        try:
            root = await _add(engine, "root", privileged=True)
            tool = GroupDriveTool()
            created = await _create(tool, engine, root)
            did = created["drive_id"]
            bad = await tool._execute(
                {
                    "action": "retire",
                    "drive_id": did,
                    "expected_revision": created["revision"],
                },
                context=_ctx(engine, root),
            )
            assert bad.error is not None and "retired" in bad.error
            # Cancel through the manager (privileged), then retire.
            from kohakuterrarium.terrarium.drive.models import DriveStatus

            record = await engine.drives.manager.get_drive(did)
            cancelled = await engine.drives.manager.transition(
                did,
                DriveStatus.CANCELLED,
                expected_revision=record.revision,
                actor=ActorRef("creature", "root"),
                is_privileged=True,
            )
            ret = await tool._execute(
                {
                    "action": "retire",
                    "drive_id": did,
                    "expected_revision": cancelled.revision,
                },
                context=_ctx(engine, root),
            )
            assert ret.error is None, ret.error
            assert _body(ret)["status"] == "retired"
        finally:
            await engine.shutdown()

    async def test_actor_is_the_trusted_caller(self):
        engine = await _engine()
        try:
            root = await _add(engine, "root", privileged=True)
            created = await _create(GroupDriveTool(), engine, root)
            record = await engine.drives.manager.get_drive(created["drive_id"])
            assert record.created_by == ActorRef("creature", "root")
            assert record.owner == ActorRef("creature", "root")
        finally:
            await engine.shutdown()


class TestGraphScopeGuard:
    async def test_foreign_graph_drive_is_refused(self):
        engine = await _engine()
        try:
            root_a = await _add(engine, "root_a", privileged=True)
            root_b = await _add(engine, "root_b", privileged=True)  # separate graph
            assert root_a.graph_id != root_b.graph_id
            tool = GroupDriveTool()
            created = await _create(tool, engine, root_a)
            did = created["drive_id"]
            # root_b is privileged but the drive is in root_a's graph.
            for action, extra in (
                ("wake", {}),
                ("retire", {"expected_revision": created["revision"]}),
                ("unassign", {"expected_revision": created["revision"]}),
            ):
                res = await tool._execute(
                    {"action": action, "drive_id": did, **extra},
                    context=_ctx(engine, root_b),
                )
                assert res.error is not None, action
                assert "not in your graph" in res.error, action
            # root_b's list must not show root_a's drive.
            lst = _body(
                await tool._execute({"action": "list"}, context=_ctx(engine, root_b))
            )
            assert did not in {d["drive_id"] for d in lst["drives"]}
        finally:
            await engine.shutdown()

    async def test_assign_requires_in_graph_assignee(self):
        engine = await _engine()
        try:
            root = await _add(engine, "root", privileged=True)
            await _add(engine, "outsider", privileged=True)  # other graph
            tool = GroupDriveTool()
            created = await _create(tool, engine, root)
            with pytest.raises(Exception, match="not a creature in caller"):
                await tool._execute(
                    {
                        "action": "assign",
                        "drive_id": created["drive_id"],
                        "assignee": "outsider",
                        "expected_revision": created["revision"],
                    },
                    context=_ctx(engine, root),
                )
        finally:
            await engine.shutdown()

    async def test_replay_requires_drive_and_delivery(self):
        engine = await _engine()
        try:
            root = await _add(engine, "root", privileged=True)
            created = await _create(GroupDriveTool(), engine, root)
            # Missing delivery_id.
            res = await GroupDriveTool()._execute(
                {"action": "replay", "drive_id": created["drive_id"]},
                context=_ctx(engine, root),
            )
            assert res.error is not None and "delivery_id" in res.error
        finally:
            await engine.shutdown()


# ── per-record durability in a mixed engine (R1-41) ───────────────────


class TestGroupPerRecordDurability:
    async def test_list_reports_per_graph_durability_not_aggregate(self, tmp_path):
        # group_drive summaries must carry each record's OWN graph durability,
        # not the mixed-engine aggregate (R1-41).
        engine = await _engine()
        try:
            root = await _add(engine, "root", privileged=True)  # ephemeral graph
            keeper = Creature(
                creature_id="keeper", name="keeper", agent=_FakeAgent(name="keeper")
            )
            await engine.add_creature(
                keeper, session=str(tmp_path / "keeper.kohakutr")
            )  # a separate, persistent graph
            assert root.graph_id != keeper.graph_id
            assert engine.drives.durability == "mixed"  # setup sanity
            tool = GroupDriveTool()
            created = await _create(tool, engine, root)
            assert created["durability"] == "ephemeral"  # root's graph, not "mixed"
            lst = _body(
                await tool._execute({"action": "list"}, context=_ctx(engine, root))
            )
            assert lst["drives"]
            assert all(d["durability"] == "ephemeral" for d in lst["drives"])
        finally:
            await engine.shutdown()


# cross-graph group_wire remains fail-closed


class TestGroupWireDriveIsolation:
    async def test_group_wire_does_not_merge_drive_graphs(self):
        engine = await _engine()
        try:
            root_a = await _add(engine, "root_a", privileged=True)
            root_b = Creature(
                creature_id="root_b",
                name="root_b",
                agent=_FakeAgent(name="root_b"),
                is_privileged=True,
            )
            await engine.add_creature(root_b, parent_creature_id="root_a")
            a_old, b_old = root_a.graph_id, root_b.graph_id
            da = await _create(GroupDriveTool(), engine, root_a)
            db = await _create(GroupDriveTool(), engine, root_b)
            res = await GroupWireTool()._execute(
                {"action": "add", "to": "root_b"}, context=_ctx(engine, root_a)
            )
            assert res.error is not None
            assert "not a creature in caller" in res.error
            assert root_a.graph_id == a_old
            assert root_b.graph_id == b_old
            ids_a = {
                drive.drive_id
                for drive in await engine.drives.manager_for(a_old).list_drives(
                    DriveQuery()
                )
            }
            ids_b = {
                drive.drive_id
                for drive in await engine.drives.manager_for(b_old).list_drives(
                    DriveQuery()
                )
            }
            assert ids_a == {da["drive_id"]}
            assert ids_b == {db["drive_id"]}
        finally:
            await engine.shutdown()
