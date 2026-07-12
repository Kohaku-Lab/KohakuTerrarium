"""R1-06: Drive assignment must validate its target against live topology.

Manager/repository validation alone does not establish that the assignee
creature exists, that it belongs to the named graph, or that the named graph is
the Drive's canonical graph. The service boundary (which owns the engine) does.
"""

import pytest

from kohakuterrarium.terrarium.creature_host import Creature
from kohakuterrarium.terrarium.drive.config import (
    DriveRuntimeConfig,
    default_registrations,
)
from kohakuterrarium.terrarium.drive.errors import DriveValidationError
from kohakuterrarium.terrarium.drive.models import ActorRef
from kohakuterrarium.terrarium.drive.requests import CreateDriveRequest
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.service import LocalTerrariumService
from kohakuterrarium.testing.terrarium import _FakeAgent

pytestmark = pytest.mark.timeout(30)

ADMIN = ActorRef("service", "ops")


async def _two_graphs():
    engine = Terrarium(
        drive_config=DriveRuntimeConfig(enabled=True),
        drive_registrations=default_registrations(),
    )
    await engine.__aenter__()
    root_a = Creature(
        creature_id="root_a",
        name="root_a",
        agent=_FakeAgent(name="a"),
        is_privileged=True,
    )
    await engine.add_creature(root_a)
    worker_a = Creature(
        creature_id="worker_a", name="worker_a", agent=_FakeAgent(name="wa")
    )
    await engine.add_creature(worker_a, graph=root_a.graph_id)
    root_b = Creature(
        creature_id="root_b",
        name="root_b",
        agent=_FakeAgent(name="b"),
        is_privileged=True,
    )
    await engine.add_creature(root_b)
    return LocalTerrariumService(engine), engine, root_a.graph_id, root_b.graph_id


async def _drive_in(svc, gid):
    req = CreateDriveRequest(
        kind="generic",
        title="w",
        scope_type="graph",
        scope_id=gid,
        owner=ADMIN,
        owner_scope="service",
        created_by=ADMIN,
        spec={},
    )
    view = await svc.create_drive(req, graph_id=gid, actor=ADMIN, is_privileged=True)
    return view.record.drive_id, view.record.revision


async def _assign(svc, did, cid, gid, rev):
    return await svc.assign_drive(
        did,
        cid,
        gid,
        expected_revision=rev,
        actor=ADMIN,
        is_privileged=True,
        operator=True,
    )


async def _create_with_assignee(svc, gid, cid):
    req = CreateDriveRequest(
        kind="generic",
        title="w",
        scope_type="graph",
        scope_id=gid,
        owner=ADMIN,
        owner_scope="service",
        created_by=ADMIN,
        spec={},
        assignee_creature_id=cid,
    )
    return await svc.create_drive(
        req, graph_id=gid, actor=ADMIN, is_privileged=True, operator=True
    )


async def test_assignment_topology_validation():
    svc, engine, gid_a, gid_b = await _two_graphs()
    try:
        did, rev = await _drive_in(svc, gid_a)

        # unknown creature
        with pytest.raises(DriveValidationError):
            await _assign(svc, did, "ghost", gid_a, rev)

        # unknown graph
        with pytest.raises(DriveValidationError):
            await _assign(svc, did, "worker_a", "no-such-graph", rev)

        # creature/graph mismatch (worker_a is in A, not B)
        with pytest.raises(DriveValidationError):
            await _assign(svc, did, "worker_a", gid_b, rev)

        # target in another graph (root_b lives in B; the Drive is in A)
        with pytest.raises(DriveValidationError):
            await _assign(svc, did, "root_b", gid_b, rev)

        # valid member assignment succeeds
        view = await _assign(svc, did, "worker_a", gid_a, rev)
        assert view.assignee_creature_id == "worker_a"
    finally:
        await engine.shutdown()


async def test_create_assignment_topology_validation():
    # R1-06 create half: a create-time assignee is validated against live
    # topology exactly like an explicit assign — an unknown or out-of-graph
    # assignee is rejected before the record is minted, a valid member succeeds.
    svc, engine, gid_a, gid_b = await _two_graphs()
    try:
        # unknown creature at create
        with pytest.raises(DriveValidationError):
            await _create_with_assignee(svc, gid_a, "ghost")

        # creature that lives in another graph (root_b is in B; the Drive is in A)
        with pytest.raises(DriveValidationError):
            await _create_with_assignee(svc, gid_a, "root_b")

        # valid graph member is accepted and assigned at creation
        view = await _create_with_assignee(svc, gid_a, "worker_a")
        assert view.assignee_creature_id == "worker_a"
    finally:
        await engine.shutdown()
