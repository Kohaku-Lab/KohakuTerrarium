"""Contract tests for :class:`RemoteDriveServiceMixin` over the wire path.

Rather than mock the transport, a fake ``_req`` dispatches straight into the
worker-side :func:`handle_drive_request` against a REAL Drive-enabled engine, so
each test exercises the whole loop: pack request DTO -> worker unpack -> local
Drive op -> pack result -> controller unpack (and the typed-error envelope on
failure). This is the per-method Remote disposition the impl plan requires.
"""

import pytest

from kohakuterrarium.laboratory._internal.app import AppMessage
from kohakuterrarium.laboratory.adapters.terrarium_runtime_drive import (
    handle_drive_request,
)
from kohakuterrarium.terrarium.creature_host import Creature
from kohakuterrarium.terrarium.drive.config import (
    DriveRuntimeConfig,
    default_registrations,
)
from kohakuterrarium.terrarium.drive.errors import (
    CrossNodeDriveNotSupportedError,
    DriveConflictError,
    DriveNotFoundError,
)
from kohakuterrarium.terrarium.drive.models import ActorRef, DriveStatus
from kohakuterrarium.terrarium.drive.remote_ops import RemoteDriveServiceMixin
from kohakuterrarium.terrarium.drive.requests import CreateDriveRequest, DrivePatch
from kohakuterrarium.terrarium.drive.wire_service import DriveRuntimeStatus, DriveView
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.service import LocalTerrariumService
from kohakuterrarium.testing.terrarium import _FakeAgent

pytestmark = pytest.mark.timeout(30)

ADMIN = ActorRef("service", "ops")
USER = ActorRef("user", "alice")


class _LocalBackedRemote(RemoteDriveServiceMixin):
    """A RemoteDriveServiceMixin whose ``_req`` runs the real worker dispatch."""

    def __init__(self, local_service: LocalTerrariumService) -> None:
        self._local = local_service

    async def _req(self, type_, body):
        msg = AppMessage(
            namespace="terrarium.runtime",
            type=type_,
            body=body,
            sender_node="_host",
            request_id="r1",
            in_reply_to=None,
        )
        return await handle_drive_request(self._local, msg)


async def _remote_with_graph():
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
    return _LocalBackedRemote(LocalTerrariumService(engine)), engine, root


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


class TestRemoteReadWriteCycle:
    async def test_create_get_update_list_over_wire(self):
        remote, engine, root = await _remote_with_graph()
        try:
            gid = root.graph_id
            view = await remote.create_drive(
                _create_req(gid), graph_id=gid, actor=ADMIN, is_privileged=True
            )
            assert isinstance(view, DriveView)
            assert view.record.status is DriveStatus.ACTIVE
            did = view.record.drive_id
            got = await remote.get_drive(did, actor=ADMIN, is_privileged=True)
            assert got is not None and got.record.drive_id == did
            listed = await remote.list_drives(
                actor=ADMIN, graph_id=gid, is_privileged=True
            )
            assert did in {v.record.drive_id for v in listed}
            updated = await remote.update_drive(
                did,
                DrivePatch(title="renamed"),
                expected_revision=view.record.revision,
                actor=ADMIN,
                is_privileged=True,
            )
            assert updated.record.title == "renamed"
        finally:
            await engine.shutdown()

    async def test_get_missing_returns_none(self):
        remote, engine, _ = await _remote_with_graph()
        try:
            assert await remote.get_drive("ghost", actor=ADMIN) is None
        finally:
            await engine.shutdown()

    async def test_assign_transition_report_progress(self):
        remote, engine, root = await _remote_with_graph()
        try:
            gid = root.graph_id
            view = await remote.create_drive(
                _create_req(gid), graph_id=gid, actor=ADMIN, is_privileged=True
            )
            did = view.record.drive_id
            assigned = await remote.assign_drive(
                did,
                "worker",
                gid,
                expected_revision=view.record.revision,
                actor=ADMIN,
                is_privileged=True,
            )
            assert assigned.assignee_creature_id == "worker"
            progress = await remote.report_drive_progress(
                did,
                summary="looking",
                evidence={"n": 1},
                actor=ADMIN,
                is_privileged=True,
            )
            assert progress.summary == "looking"
            paused = await remote.transition_drive(
                did,
                DriveStatus.PAUSED,
                expected_revision=assigned.record.revision,
                actor=ADMIN,
                is_privileged=True,
            )
            assert paused.record.status is DriveStatus.PAUSED
            deliveries = await remote.list_drive_deliveries(did)
            assert isinstance(deliveries, tuple)
            progresses = await remote.list_drive_progress(did)
            assert any(p.summary == "looking" for p in progresses)
        finally:
            await engine.shutdown()

    async def test_runtime_status_and_registrations_over_wire(self):
        remote, engine, _ = await _remote_with_graph()
        try:
            status = await remote.drive_runtime_status()
            assert isinstance(status, DriveRuntimeStatus)
            assert status.enabled is True
            regs = await remote.list_drive_registrations()
            assert any(r.get("name") == "generic" for r in regs)
        finally:
            await engine.shutdown()


class TestRemoteTypedErrors:
    async def test_stale_revision_conflict_survives_wire(self):
        remote, engine, root = await _remote_with_graph()
        try:
            gid = root.graph_id
            view = await remote.create_drive(
                _create_req(gid), graph_id=gid, actor=ADMIN, is_privileged=True
            )
            with pytest.raises(DriveConflictError):
                await remote.update_drive(
                    view.record.drive_id,
                    DrivePatch(title="x"),
                    expected_revision=999,
                    actor=ADMIN,
                    is_privileged=True,
                )
        finally:
            await engine.shutdown()

    async def test_update_missing_drive_raises_not_found(self):
        remote, engine, _ = await _remote_with_graph()
        try:
            with pytest.raises(DriveNotFoundError):
                await remote.update_drive(
                    "ghost",
                    DrivePatch(title="x"),
                    expected_revision=1,
                    actor=ADMIN,
                    is_privileged=True,
                )
        finally:
            await engine.shutdown()

    async def test_reconfigure_is_typed_unsupported(self):
        remote, engine, _ = await _remote_with_graph()
        try:
            with pytest.raises(CrossNodeDriveNotSupportedError):
                await remote.reconfigure_drive_runtime((), actor=ADMIN)
        finally:
            await engine.shutdown()
