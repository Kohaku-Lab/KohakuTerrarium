"""Routing tests for :class:`MultiNodeDriveServiceMixin` (design §10).

A fake host composes two ``_LocalBackedRemote`` workers, each a REAL Drive-enabled
engine hosting its own graph. The mixin must route per-Drive writes to the home
worker, fan out reads, refuse cross-node assignment, and report a home-loss write
as a typed error (never a silent second writer).
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
)
from kohakuterrarium.terrarium.drive.models import ActorRef, DriveStatus
from kohakuterrarium.terrarium.drive.multi_node import (
    DriveHomeUnavailableError,
    DriveRouteCache,
)
from kohakuterrarium.terrarium.drive.multi_node_ops import MultiNodeDriveServiceMixin
from kohakuterrarium.terrarium.drive.remote_ops import RemoteDriveServiceMixin
from kohakuterrarium.terrarium.drive.requests import CreateDriveRequest
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.service import LocalTerrariumService
from kohakuterrarium.testing.terrarium import _FakeAgent

pytestmark = pytest.mark.timeout(30)

ADMIN = ActorRef("service", "ops")


class _LocalBackedRemote(RemoteDriveServiceMixin):
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


class _FakeMulti(MultiNodeDriveServiceMixin):
    def __init__(self) -> None:
        self._remotes: dict[str, _LocalBackedRemote] = {}
        self._drive_routes = DriveRouteCache()

    def service_for(self, node_id):
        return self._remotes[node_id]

    async def _resolve_graph_home(self, graph_id):
        for node_id, svc in self._remotes.items():
            try:
                if svc._local.engine.get_graph(graph_id) is not None:
                    return node_id
            except KeyError:
                continue
        raise KeyError(graph_id)


async def _worker(node_id: str):
    engine = Terrarium(
        drive_config=DriveRuntimeConfig(enabled=True),
        drive_registrations=default_registrations(),
    )
    await engine.__aenter__()
    root = Creature(
        creature_id=f"{node_id}-root",
        name="root",
        agent=_FakeAgent(name="root"),
        is_privileged=True,
    )
    await engine.add_creature(root)
    worker = Creature(
        creature_id=f"{node_id}-worker",
        name="worker",
        agent=_FakeAgent(name="worker"),
    )
    await engine.add_creature(worker, graph=root.graph_id)
    return engine, root.graph_id


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


async def _cluster():
    engine_a, ga = await _worker("A")
    engine_b, gb = await _worker("B")
    multi = _FakeMulti()
    multi._remotes["A"] = _LocalBackedRemote(LocalTerrariumService(engine_a))
    multi._remotes["B"] = _LocalBackedRemote(LocalTerrariumService(engine_b))
    return multi, engine_a, engine_b, ga, gb


class TestRoutedReadsWrites:
    async def test_create_routes_to_graph_home_and_read_back(self):
        multi, ea, eb, ga, gb = await _cluster()
        try:
            view = await multi.create_drive(
                _create_req(ga), graph_id=ga, actor=ADMIN, is_privileged=True
            )
            did = view.record.drive_id
            # route cache learns the home lazily via get_drive fan-out
            got = await multi.get_drive(did, actor=ADMIN, is_privileged=True)
            assert got.record.drive_id == did
            assert multi._drive_routes.get_drive_home(did) == "A"
            # write routes to home A
            updated = await multi.transition_drive(
                did,
                DriveStatus.PAUSED,
                expected_revision=view.record.revision,
                actor=ADMIN,
                is_privileged=True,
            )
            assert updated.record.status is DriveStatus.PAUSED
        finally:
            await ea.shutdown()
            await eb.shutdown()

    async def test_list_fans_out_and_dedupes(self):
        multi, ea, eb, ga, gb = await _cluster()
        try:
            va = await multi.create_drive(
                _create_req(ga), graph_id=ga, actor=ADMIN, is_privileged=True
            )
            vb = await multi.create_drive(
                _create_req(gb), graph_id=gb, actor=ADMIN, is_privileged=True
            )
            listed = await multi.list_drives(actor=ADMIN, is_privileged=True)
            ids = {v.record.drive_id for v in listed}
            assert va.record.drive_id in ids and vb.record.drive_id in ids
            # graph-scoped list targets only that graph's home
            only_a = await multi.list_drives(
                actor=ADMIN, graph_id=ga, is_privileged=True
            )
            assert {v.record.drive_id for v in only_a} == {va.record.drive_id}
        finally:
            await ea.shutdown()
            await eb.shutdown()

    async def test_runtime_status_aggregates_counts(self):
        multi, ea, eb, ga, gb = await _cluster()
        try:
            await multi.create_drive(
                _create_req(ga), graph_id=ga, actor=ADMIN, is_privileged=True
            )
            await multi.create_drive(
                _create_req(gb), graph_id=gb, actor=ADMIN, is_privileged=True
            )
            status = await multi.drive_runtime_status()
            assert status.enabled is True
            assert status.counts.get("active", 0) == 2
        finally:
            await ea.shutdown()
            await eb.shutdown()


class TestCrossNodeRefusal:
    async def test_cross_node_assignment_refused(self):
        multi, ea, eb, ga, gb = await _cluster()
        try:
            view = await multi.create_drive(
                _create_req(ga), graph_id=ga, actor=ADMIN, is_privileged=True
            )
            with pytest.raises(CrossNodeDriveNotSupportedError):
                await multi.assign_drive(
                    view.record.drive_id,
                    "B-worker",
                    gb,  # assignee graph lives on node B; Drive homed on A
                    expected_revision=view.record.revision,
                    actor=ADMIN,
                    is_privileged=True,
                )
        finally:
            await ea.shutdown()
            await eb.shutdown()

    async def test_same_node_assignment_works(self):
        multi, ea, eb, ga, gb = await _cluster()
        try:
            view = await multi.create_drive(
                _create_req(ga), graph_id=ga, actor=ADMIN, is_privileged=True
            )
            assigned = await multi.assign_drive(
                view.record.drive_id,
                "A-worker",
                ga,
                expected_revision=view.record.revision,
                actor=ADMIN,
                is_privileged=True,
            )
            assert assigned.assignee_creature_id == "A-worker"
        finally:
            await ea.shutdown()
            await eb.shutdown()

    async def test_reconfigure_is_unsupported(self):
        multi, ea, eb, ga, gb = await _cluster()
        try:
            with pytest.raises(CrossNodeDriveNotSupportedError):
                await multi.reconfigure_drive_runtime((), actor=ADMIN)
        finally:
            await ea.shutdown()
            await eb.shutdown()


class TestHomeLoss:
    async def test_write_after_home_loss_is_typed_no_second_writer(self):
        multi, ea, eb, ga, gb = await _cluster()
        try:
            view = await multi.create_drive(
                _create_req(ga), graph_id=ga, actor=ADMIN, is_privileged=True
            )
            did = view.record.drive_id
            await multi.get_drive(did, actor=ADMIN, is_privileged=True)  # warm cache
            # Home worker A drops off the cluster.
            multi._remotes.pop("A")
            multi._drive_routes.purge_node("A")
            with pytest.raises(DriveHomeUnavailableError):
                await multi.transition_drive(
                    did,
                    DriveStatus.PAUSED,
                    expected_revision=view.record.revision,
                    actor=ADMIN,
                    is_privileged=True,
                )
            # B never became a writer for A's Drive.
            assert not await multi.list_drives(actor=ADMIN, graph_id=ga)
        finally:
            await ea.shutdown()
            await eb.shutdown()
