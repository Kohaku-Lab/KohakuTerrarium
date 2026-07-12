"""Unit tests for :mod:`terrarium.drive.service` — the TerrariumService Drive surface.

Two halves:

- :class:`LocalTerrariumService` drives a REAL Drive-enabled engine through the
  full read/write DTO surface, folding records into :class:`DriveView`s with
  actor-scoped ``allowed_actions``;
- the Remote / MultiNode services must expose EVERY Drive method as a typed
  :class:`CrossNodeDriveNotSupportedError` stub (never ``AttributeError``) so
  Phase H's routing has an explicit disposition to replace.
"""

import pytest

from kohakuterrarium.terrarium.creature_host import Creature
from kohakuterrarium.terrarium.drive.config import (
    DriveRuntimeConfig,
    default_registrations,
)
from kohakuterrarium.terrarium.drive.errors import (
    CrossNodeDriveNotSupportedError,
    DriveError,
)
from kohakuterrarium.terrarium.drive.models import ActorRef, DriveStatus
from kohakuterrarium.terrarium.drive.requests import CreateDriveRequest, DrivePatch
from kohakuterrarium.terrarium.drive.service_protocol import (
    DriveServiceUnsupportedMixin,
)
from kohakuterrarium.terrarium.drive.wire_service import DriveRuntimeStatus, DriveView
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.service import LocalTerrariumService
from kohakuterrarium.testing.terrarium import _FakeAgent

pytestmark = pytest.mark.timeout(30)

ADMIN = ActorRef("service", "ops")


async def _service_with_graph():
    engine = Terrarium(
        drive_config=DriveRuntimeConfig(enabled=True),
        drive_registrations=default_registrations(),
    )
    await engine.__aenter__()
    root = Creature(
        creature_id="root",
        name="root",
        agent=_FakeAgent(name="root"),
        is_privileged=True,
    )
    await engine.add_creature(root)
    worker = Creature(
        creature_id="worker", name="worker", agent=_FakeAgent(name="worker")
    )
    await engine.add_creature(worker, graph=root.graph_id)
    return LocalTerrariumService(engine), engine, root


def _create_req(graph_id, **over) -> CreateDriveRequest:
    base = dict(
        kind="generic",
        title="watch",
        scope_type="graph",
        scope_id=graph_id,
        owner=ADMIN,
        owner_scope="service",
        created_by=ADMIN,
        spec={"x": 1},
    )
    base.update(over)
    return CreateDriveRequest(**base)


class TestLocalReadsAndWrites:
    async def test_create_get_list_update_cycle(self):
        svc, engine, root = await _service_with_graph()
        try:
            gid = root.graph_id
            view = await svc.create_drive(
                _create_req(gid), graph_id=gid, actor=ADMIN, is_privileged=True
            )
            assert isinstance(view, DriveView)
            assert view.record.status is DriveStatus.ACTIVE
            assert "update" in view.allowed_actions
            did = view.record.drive_id
            got = await svc.get_drive(did, actor=ADMIN, is_privileged=True)
            assert got.record.drive_id == did
            listed = await svc.list_drives(
                actor=ADMIN, graph_id=gid, is_privileged=True
            )
            assert did in {v.record.drive_id for v in listed}
            updated = await svc.update_drive(
                did,
                DrivePatch(title="renamed"),
                expected_revision=view.record.revision,
                actor=ADMIN,
                is_privileged=True,
            )
            assert updated.record.title == "renamed"
        finally:
            await engine.shutdown()

    async def test_assign_transition_propose_progress(self):
        svc, engine, root = await _service_with_graph()
        try:
            gid = root.graph_id
            view = await svc.create_drive(
                _create_req(gid), graph_id=gid, actor=ADMIN, is_privileged=True
            )
            did = view.record.drive_id
            assigned = await svc.assign_drive(
                did,
                "worker",
                gid,
                expected_revision=view.record.revision,
                actor=ADMIN,
                is_privileged=True,
            )
            assert assigned.assignee_creature_id == "worker"
            prog = await svc.report_drive_progress(
                did,
                summary="halfway",
                evidence={"pct": 50},
                actor=ADMIN,
                is_privileged=True,
            )
            assert prog.drive_id == did
            record = await engine.drives.manager.get_drive(did)
            paused = await svc.transition_drive(
                did,
                DriveStatus.PAUSED,
                expected_revision=record.revision,
                actor=ADMIN,
                is_privileged=True,
            )
            assert paused.record.status is DriveStatus.PAUSED
            # generic verifier=none -> propose completes immediately.
            record = await engine.drives.manager.get_drive(did)
            await svc.transition_drive(
                did,
                DriveStatus.ACTIVE,
                expected_revision=record.revision,
                actor=ADMIN,
                is_privileged=True,
            )
            record = await engine.drives.manager.get_drive(did)
            done = await svc.propose_drive_transition(
                did,
                DriveStatus.COMPLETED,
                actor=ADMIN,
                evidence={"ok": True},
                expected_revision=record.revision,
                is_privileged=True,
            )
            assert isinstance(done, DriveView)
            assert done.record.status is DriveStatus.COMPLETED
        finally:
            await engine.shutdown()

    async def test_operator_elevation_threads_through_service(self):
        # The service exposes an explicit operator elevation distinct from
        # creature privilege: a plain user is denied graph create, and the same
        # call with operator=True succeeds and is audited (design §3.6, §13).
        from kohakuterrarium.terrarium.drive.errors import DrivePermissionError

        svc, engine, root = await _service_with_graph()
        user = ActorRef("user", "alice")
        try:
            gid = root.graph_id
            req = _create_req(gid, owner=user, owner_scope="actor", created_by=user)
            with pytest.raises(DrivePermissionError):
                await svc.create_drive(req, graph_id=gid, actor=user)
            view = await svc.create_drive(req, graph_id=gid, actor=user, operator=True)
            assert view.record.owner == user
            audit = await engine.drives.manager.repository.list_audit(
                view.record.drive_id
            )
            assert "operator_grant" in [a.operation for a in audit]
        finally:
            await engine.shutdown()

    async def test_runtime_status_and_registrations(self):
        svc, engine, root = await _service_with_graph()
        try:
            await svc.create_drive(
                _create_req(root.graph_id),
                graph_id=root.graph_id,
                actor=ADMIN,
                is_privileged=True,
            )
            status = await svc.drive_runtime_status()
            assert isinstance(status, DriveRuntimeStatus)
            assert status.enabled is True
            assert status.counts.get("active") == 1
            assert {r["name"] for r in status.registrations} == {"generic"}
            regs = await svc.list_drive_registrations()
            assert regs[0]["kind"] == "generic"
        finally:
            await engine.shutdown()

    async def test_reconfigure_runtime_passthrough(self):
        svc, engine, root = await _service_with_graph()
        try:
            result = await svc.reconfigure_drive_runtime(
                default_registrations(), actor=ADMIN
            )
            assert result == "applied_live"
        finally:
            await engine.shutdown()


class TestMultiGraph:
    async def test_per_drive_ops_resolve_the_owning_graph(self):
        # Phase F partitions the runtime per graph. A service that routed through
        # ``manager_for("")`` would spawn a phantom graph and miss the drive;
        # per-drive ops must resolve the OWNING graph's manager.
        engine = Terrarium(
            drive_config=DriveRuntimeConfig(enabled=True),
            drive_registrations=default_registrations(),
        )
        await engine.__aenter__()
        try:
            a = Creature(
                creature_id="a",
                name="a",
                agent=_FakeAgent(name="a"),
                is_privileged=True,
            )
            b = Creature(
                creature_id="b",
                name="b",
                agent=_FakeAgent(name="b"),
                is_privileged=True,
            )
            await engine.add_creature(a)
            await engine.add_creature(b)  # separate graph
            assert a.graph_id != b.graph_id
            svc = LocalTerrariumService(engine)
            va = await svc.create_drive(
                _create_req(a.graph_id),
                graph_id=a.graph_id,
                actor=ADMIN,
                is_privileged=True,
            )
            vb = await svc.create_drive(
                _create_req(b.graph_id, title="other"),
                graph_id=b.graph_id,
                actor=ADMIN,
                is_privileged=True,
            )
            # get resolves the right graph for each id.
            assert (
                await svc.get_drive(va.record.drive_id, actor=ADMIN)
            ).record.title == "watch"
            assert (
                await svc.get_drive(vb.record.drive_id, actor=ADMIN)
            ).record.title == "other"
            # graph-scoped list only shows that graph's drive.
            only_a = await svc.list_drives(actor=ADMIN, graph_id=a.graph_id)
            assert {v.record.drive_id for v in only_a} == {va.record.drive_id}
            # graph-less list aggregates across graphs.
            everything = await svc.list_drives(actor=ADMIN)
            assert {va.record.drive_id, vb.record.drive_id} <= {
                v.record.drive_id for v in everything
            }
            # a per-drive mutation on b's drive routes to b's manager.
            updated = await svc.update_drive(
                vb.record.drive_id,
                DrivePatch(priority=5),
                expected_revision=vb.record.revision,
                actor=ADMIN,
                is_privileged=True,
            )
            assert updated.record.priority == 5
        finally:
            await engine.shutdown()

    async def test_get_missing_drive_returns_none(self):
        svc, engine, root = await _service_with_graph()
        try:
            assert await svc.get_drive("does-not-exist", actor=ADMIN) is None
        finally:
            await engine.shutdown()


class TestDisabledEngine:
    async def test_runtime_status_disabled(self):
        engine = Terrarium()
        async with engine:
            svc = LocalTerrariumService(engine)
            status = await svc.drive_runtime_status()
            assert status.enabled is False
            assert await svc.list_drive_registrations() == ()

    async def test_mutation_on_disabled_fails_closed(self):
        engine = Terrarium()
        async with engine:
            svc = LocalTerrariumService(engine)
            with pytest.raises(DriveError):
                await svc.get_drive("nope", actor=ADMIN)


# ---------------------------------------------------------------------------
# Remote / MultiNode typed disposition
# ---------------------------------------------------------------------------

_UNSUPPORTED_CALLS = [
    ("get_drive", ("d",), {"actor": ADMIN}),
    ("list_drives", (), {"actor": ADMIN}),
    ("list_drive_progress", ("d",), {}),
    ("list_drive_deliveries", ("d",), {}),
    ("drive_runtime_status", (), {}),
    ("list_drive_registrations", (), {}),
    ("create_drive", ("req",), {"graph_id": "g", "actor": ADMIN}),
    ("update_drive", ("d", "patch"), {"expected_revision": 1, "actor": ADMIN}),
    ("assign_drive", ("d", "w", "g"), {"expected_revision": 1, "actor": ADMIN}),
    ("unassign_drive", ("d",), {"expected_revision": 1, "actor": ADMIN}),
    ("transfer_drive_owner", ("d", ADMIN), {"expected_revision": 1, "actor": ADMIN}),
    (
        "transition_drive",
        ("d", DriveStatus.PAUSED),
        {"expected_revision": 1, "actor": ADMIN},
    ),
    ("wake_drive", ("d",), {"actor": ADMIN}),
    (
        "report_drive_progress",
        ("d",),
        {"summary": "s", "evidence": None, "actor": ADMIN},
    ),
    ("propose_drive_transition", ("d", DriveStatus.COMPLETED), {"actor": ADMIN}),
    ("approve_drive_proposal", ("p",), {"actor": ADMIN}),
    ("retire_drive", ("d",), {"expected_revision": 1, "actor": ADMIN}),
    ("replay_drive_delivery", ("x",), {"actor": ADMIN}),
    ("reconfigure_drive_runtime", ((),), {"actor": ADMIN}),
]


class _BareUnsupported(DriveServiceUnsupportedMixin):
    pass


@pytest.mark.parametrize("method,args,kwargs", _UNSUPPORTED_CALLS)
async def test_unsupported_mixin_raises_typed(method, args, kwargs):
    svc = _BareUnsupported()
    with pytest.raises(CrossNodeDriveNotSupportedError):
        await getattr(svc, method)(*args, **kwargs)


def test_remote_and_multinode_override_the_unsupported_floor():
    """Phase H replaced the stubs with real routing; the floor stays defensive.

    Every DTO method is still present, still resolves to a real implementation
    (NOT the bare :class:`DriveServiceUnsupportedMixin` stub), and the mixin is
    retained only as a defensive MRO floor. ``reconfigure_drive_runtime`` is the
    one method that stays a typed-unsupported cross-node case (registration
    instances are not serializable — design §8.6).
    """
    from kohakuterrarium.terrarium.multi_node_service import MultiNodeTerrariumService
    from kohakuterrarium.terrarium.remote_service import RemoteTerrariumService

    assert issubclass(RemoteTerrariumService, DriveServiceUnsupportedMixin)
    assert issubclass(MultiNodeTerrariumService, DriveServiceUnsupportedMixin)
    deferred = {"reconfigure_drive_runtime"}
    for method, _, _ in _UNSUPPORTED_CALLS:
        for cls in (RemoteTerrariumService, MultiNodeTerrariumService):
            resolved = getattr(cls, method)
            assert callable(resolved)
            if method in deferred:
                continue
            stub = getattr(DriveServiceUnsupportedMixin, method)
            assert (
                resolved is not stub
            ), f"{cls.__name__}.{method} still resolves to the unsupported stub"
