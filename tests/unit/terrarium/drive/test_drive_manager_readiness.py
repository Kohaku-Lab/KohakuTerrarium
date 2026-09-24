"""Waiting-drive wake conditions through the real readiness scan."""

from datetime import timedelta

import pytest

from kohakuterrarium.terrarium.drive.config import default_registrations
from kohakuterrarium.terrarium.drive.models import DriveStatus
from kohakuterrarium.terrarium.drive.snapshot import EnabledRegistrySnapshot

from tests.unit.terrarium.drive._harness import (
    WORKER,
    build_manager,
    creature_request,
    make_config,
)


@pytest.mark.parametrize("kind", ["generic", "goal"])
@pytest.mark.parametrize("initial_waiting", [False, True])
async def test_unconditional_waiting_survives_scans_and_reconcile(
    kind, initial_waiting
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
                "autonomy": "continue_when_ready",
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
async def test_waiting_wakes_only_after_explicit_conditions_are_ready(conditions):
    h = build_manager()
    dependency = await h.manager.create_drive(
        creature_request(title="dependency"), actor=WORKER, graph_id="g1"
    )
    await h.manager.dispatcher.dispatch_once()
    await h.manager.dispatcher.drain()
    record = await h.manager.create_drive(
        creature_request(
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
