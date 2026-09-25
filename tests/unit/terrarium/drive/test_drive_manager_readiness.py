"""Waiting-drive wake conditions through the real readiness scan."""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from kohakuterrarium.terrarium.drive.config import default_registrations
from kohakuterrarium.terrarium.drive.models import DriveStatus
from kohakuterrarium.terrarium.drive.registration import (
    GenericDriveRegistration,
    Readiness,
)
from kohakuterrarium.terrarium.drive.registration_options import (
    apply_registration_wake_policy,
)
from kohakuterrarium.terrarium.drive.snapshot import EnabledRegistrySnapshot

from tests.unit.terrarium.drive._harness import (
    WORKER,
    build_manager,
    creature_request,
    make_config,
)


class _ConditionalRegistration(GenericDriveRegistration):
    name = "conditional"
    kind = "conditional"

    def __init__(self, *, initial):
        super().__init__()
        self.initial = initial

    def readiness(self, drive, dependencies, now):
        return Readiness(
            ready=now >= datetime.fromisoformat(drive.spec["release_at"]),
            initial=self.initial,
        )


class _NoReadinessRegistration(GenericDriveRegistration):
    readiness = None

    def descriptor(self):
        return replace(super().descriptor(), required_roles=frozenset({"spec"}))


class _UnavailableRegistration(GenericDriveRegistration):
    readiness = None


class _ActiveOnlyRegistration(GenericDriveRegistration):
    name = "active_only_registration"
    kind = "active_only"

    def __init__(self):
        super().__init__()
        self.observed_statuses = []

    def readiness(self, drive, dependencies, now):
        self.observed_statuses.append(drive.status)
        return Readiness(
            ready=drive.status is DriveStatus.ACTIVE
            and now >= datetime.fromisoformat(drive.spec["release_at"])
        )


@pytest.mark.parametrize("opt_in", [False, True])
async def test_readiness_remains_an_active_delivery_gate_after_resume(opt_in):
    registration = _ActiveOnlyRegistration()
    apply_registration_wake_policy(registration, unconditional_wake=opt_in)
    h = build_manager(
        snapshot=EnabledRegistrySnapshot.build([registration]),
    )
    record = await h.manager.create_drive(
        creature_request(
            kind="active_only",
            spec={"release_at": (h.clock() + timedelta(seconds=60)).isoformat()},
        ),
        actor=WORKER,
        graph_id="g1",
        initial_status=DriveStatus.WAITING,
    )
    await h.manager._scan_ready()
    assert DriveStatus.WAITING not in registration.observed_statuses
    assert not await h.manager.list_deliveries(record.drive_id)

    current = await h.manager.get_drive(record.drive_id)
    assert current.status is (DriveStatus.ACTIVE if opt_in else DriveStatus.WAITING)
    if not opt_in:
        await h.manager.transition(
            record.drive_id,
            DriveStatus.ACTIVE,
            expected_revision=current.revision,
            actor=WORKER,
        )
    assert not await h.manager.list_deliveries(record.drive_id)
    h.clock.advance(60)
    await h.manager._scan_ready()
    await h.manager.dispatcher.dispatch_once()
    await h.manager.dispatcher.drain()
    assert [d.state for d in await h.manager.list_deliveries(record.drive_id)] == [
        "acknowledged"
    ]
    assert registration.observed_statuses
    assert set(registration.observed_statuses) == {DriveStatus.ACTIVE}


@pytest.mark.parametrize(
    ("kind", "autonomy"),
    [("generic", "manual"), ("goal", "manual"), ("goal", "continue_when_ready")],
)
@pytest.mark.parametrize("initial_waiting", [False, True])
async def test_unconditional_waiting_survives_scans_and_reconcile(
    kind, autonomy, initial_waiting
):
    h = build_manager(
        snapshot=EnabledRegistrySnapshot.build(default_registrations()),
        config=make_config(readiness_cooldown_s=30),
    )
    record = await h.manager.create_drive(
        creature_request(
            kind=kind,
            spec={
                "objective": "Await background results",
                "autonomy": autonomy,
            },
        ),
        actor=WORKER,
        graph_id="g1",
        initial_status=DriveStatus.WAITING if initial_waiting else DriveStatus.ACTIVE,
    )
    if not initial_waiting:
        await h.manager.dispatcher.dispatch_once()
        await h.manager.dispatcher.drain()
        await h.manager.transition(
            record.drive_id,
            DriveStatus.WAITING,
            expected_revision=record.revision,
            actor=WORKER,
        )
    waiting = await h.manager.get_drive(record.drive_id)
    before = await h.manager.list_deliveries(record.drive_id)
    for _ in range(3):
        h.clock.advance(60)
        await h.manager._scan_ready()
        await h.manager.dispatcher.dispatch_once()
        await h.manager.dispatcher.drain()
        assert (
            await h.manager.get_drive(record.drive_id)
        ).status is DriveStatus.WAITING

    restarted = build_manager(repo=h.repo, snapshot=h.snapshot, clock=h.clock)
    await restarted.manager.reconcile(creature_id="worker")
    await restarted.manager._scan_ready()
    current = await restarted.manager.get_drive(record.drive_id)
    assert current.status is DriveStatus.WAITING
    assert current.revision == waiting.revision
    assert await restarted.manager.list_deliveries(record.drive_id) == before

    await restarted.manager.wake_drive(record.drive_id, actor=WORKER)
    await restarted.manager.dispatcher.dispatch_once()
    await restarted.manager.dispatcher.drain()
    assert (
        await restarted.manager.get_drive(record.drive_id)
    ).status is DriveStatus.ACTIVE
    acked = [
        d
        for d in await restarted.manager.list_deliveries(record.drive_id)
        if d.state == "acknowledged"
    ]
    assert len(acked) == (1 if initial_waiting else 2)
    assert acked[-1].reason == "manual_wake"


@pytest.mark.parametrize("conditions", ["time", "dependency", "both"])
@pytest.mark.parametrize("kind", ["generic", "goal"])
@pytest.mark.parametrize("opt_in", [False, True])
async def test_waiting_wakes_only_after_explicit_conditions_are_ready(
    conditions, kind, opt_in
):
    registrations = default_registrations()
    for registration in registrations:
        apply_registration_wake_policy(
            registration, unconditional_wake=opt_in and registration.kind == kind
        )
    h = build_manager(
        snapshot=EnabledRegistrySnapshot.build(registrations),
    )
    dependency = await h.manager.create_drive(
        creature_request(title="dependency"), actor=WORKER, graph_id="g1"
    )
    await h.manager.dispatcher.dispatch_once()
    await h.manager.dispatcher.drain()
    record = await h.manager.create_drive(
        creature_request(
            kind=kind,
            spec={"objective": "Honor the wake condition"},
            not_before=(
                h.clock() + timedelta(seconds=60)
                if conditions != "dependency"
                else None
            ),
            dependency_ids=(dependency.drive_id,) if conditions != "time" else (),
        ),
        actor=WORKER,
        graph_id="g1",
        initial_status=DriveStatus.WAITING,
    )
    await h.manager._scan_ready()
    assert (await h.manager.get_drive(record.drive_id)).status is DriveStatus.WAITING
    h.clock.advance(59)
    await h.manager._scan_ready()
    assert (await h.manager.get_drive(record.drive_id)).status is DriveStatus.WAITING
    h.clock.advance(1)
    if conditions != "time":
        await h.manager._scan_ready()
        assert (
            await h.manager.get_drive(record.drive_id)
        ).status is DriveStatus.WAITING
        await h.manager.propose_transition(
            dependency.drive_id,
            DriveStatus.COMPLETED,
            expected_revision=dependency.revision,
            actor=WORKER,
            evidence={"done": True},
        )
    await h.manager._scan_ready()
    await h.manager.dispatcher.dispatch_once()
    await h.manager.dispatcher.drain()
    assert (await h.manager.get_drive(record.drive_id)).status is DriveStatus.ACTIVE
    deliveries = await h.manager.list_deliveries(record.drive_id)
    assert [d.state for d in deliveries] == ["acknowledged"]
    assert deliveries[0].reason == (
        "ready" if conditions == "time" else "dependency_ready"
    )


@pytest.mark.parametrize("initial", [False, True])
async def test_opt_in_preserves_initial_delivery_grant(initial):
    registration = _ConditionalRegistration(initial=initial)
    apply_registration_wake_policy(registration, unconditional_wake=True)
    h = build_manager(
        snapshot=EnabledRegistrySnapshot.build([registration]),
    )
    record = await h.manager.create_drive(
        creature_request(
            kind="conditional",
            spec={"release_at": (h.clock() + timedelta(seconds=60)).isoformat()},
        ),
        actor=WORKER,
        graph_id="g1",
        initial_status=DriveStatus.WAITING,
    )
    await h.manager._scan_ready()
    assert (await h.manager.get_drive(record.drive_id)).status is DriveStatus.ACTIVE
    assert len(await h.manager.list_deliveries(record.drive_id)) == int(initial)
    h.clock.advance(60)
    await h.manager._scan_ready()
    await h.manager.dispatcher.dispatch_once()
    await h.manager.dispatcher.drain()
    assert (await h.manager.get_drive(record.drive_id)).status is DriveStatus.ACTIVE
    assert [d.state for d in await h.manager.list_deliveries(record.drive_id)] == [
        "acknowledged"
    ]


async def test_wake_policy_is_isolated_between_registrations():
    registration = _ActiveOnlyRegistration()
    other = GenericDriveRegistration()
    apply_registration_wake_policy(other, unconditional_wake=True)
    h = build_manager(
        snapshot=EnabledRegistrySnapshot.build([registration, other]),
    )
    record = await h.manager.create_drive(
        creature_request(
            kind="active_only", spec={"release_at": h.clock().isoformat()}
        ),
        actor=WORKER,
        graph_id="g1",
        initial_status=DriveStatus.WAITING,
    )
    await h.manager._scan_ready()
    assert (await h.manager.get_drive(record.drive_id)).status is DriveStatus.WAITING
    assert registration.observed_statuses == []
    assert not await h.manager.list_deliveries(record.drive_id)


@pytest.mark.parametrize(
    "registrations", [[], [_NoReadinessRegistration()], [_UnavailableRegistration()]]
)
async def test_waiting_without_available_readiness_has_no_implicit_wake(registrations):
    h = build_manager()
    record = await h.manager.create_drive(
        creature_request(),
        actor=WORKER,
        graph_id="g1",
        initial_status=DriveStatus.WAITING,
    )
    restarted = build_manager(
        repo=h.repo,
        clock=h.clock,
        snapshot=EnabledRegistrySnapshot.build(registrations),
    )
    h.clock.advance(60)
    await restarted.manager._scan_ready()
    current = await restarted.manager.get_drive(record.drive_id)
    assert current.status is DriveStatus.WAITING
    assert current.revision == record.revision
    assert not await restarted.manager.list_deliveries(record.drive_id)
