"""Shared harness for the Phase D DriveManager / dispatcher / lifecycle tests.

A ``FakeSink`` (scriptable admission + settlement, records deliveries) driven
over the REAL ``MemoryDriveRepository`` with an injected fake clock and a
deterministic ``ScriptedRng``. Not collected by pytest (no ``test_`` prefix).
"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from kohakuterrarium.terrarium.drive.config import DriveRuntimeConfig
from kohakuterrarium.terrarium.drive.manager import DriveManager
from kohakuterrarium.terrarium.drive.memory import MemoryDriveRepository
from kohakuterrarium.terrarium.drive.models import ActorRef
from kohakuterrarium.terrarium.drive.registration import GenericDriveRegistration
from kohakuterrarium.terrarium.drive.requests import CreateDriveRequest
from kohakuterrarium.terrarium.drive.sink import (
    DeliveryOutcome,
    Settlement,
    SettlementStatus,
)
from kohakuterrarium.terrarium.drive.snapshot import EnabledRegistrySnapshot

WORKER = ActorRef("creature", "worker")
OTHER = ActorRef("creature", "other")
USER = ActorRef("user", "alice")
ADMIN = ActorRef("service", "ops")


class Clock:
    def __init__(self) -> None:
        self.t = datetime(2026, 7, 11, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


class ScriptedRng:
    """Deterministic ``random()``; the default 0.5 yields zero jitter."""

    def __init__(self, values=(0.5,)) -> None:
        self._values = list(values)
        self._i = 0

    def random(self) -> float:
        value = self._values[self._i % len(self._values)]
        self._i += 1
        return value


def ids(prefix: str = "id"):
    counter = [0]

    def mint() -> str:
        counter[0] += 1
        return f"{prefix}{counter[0]:05d}"

    return mint


class FakeSink:
    """Scriptable delivery sink recording every ``deliver`` call."""

    def __init__(self, *, default: SettlementStatus = SettlementStatus.OK) -> None:
        self.delivered: list = []
        self.stopped: set[str] = set()
        self.foreign: set[str] = set()
        self.raise_on: set[str] = set()
        self.no_settle = False
        self.default = default
        self.per_delivery: dict[str, SettlementStatus] = {}
        self.gate: asyncio.Event | None = None

    async def deliver(self, creature_id, event, *, delivery_id) -> DeliveryOutcome:
        self.delivered.append(
            SimpleNamespace(
                creature_id=creature_id, event=event, delivery_id=delivery_id
            )
        )
        if creature_id in self.raise_on:
            raise RuntimeError("sink boom")
        if creature_id in self.stopped:
            return DeliveryOutcome.rejected_stopped()
        if self.no_settle:
            return DeliveryOutcome.accepted(None)
        status = self.per_delivery.get(delivery_id, self.default)
        gate = self.gate

        async def _settle() -> Settlement:
            if gate is not None:
                await gate.wait()
            return Settlement(status)

        return DeliveryOutcome.accepted(_settle)

    def has_queued_foreign_work(self, creature_id) -> bool:
        return creature_id in self.foreign


def make_snapshot(*registrations) -> EnabledRegistrySnapshot:
    regs = list(registrations) or [GenericDriveRegistration()]
    return EnabledRegistrySnapshot.build(regs)


def make_config(**over) -> DriveRuntimeConfig:
    base = {"enabled": True}
    base.update(over)
    return DriveRuntimeConfig(**base)


def build_manager(
    *,
    config: DriveRuntimeConfig | None = None,
    sink: FakeSink | None = None,
    snapshot: EnabledRegistrySnapshot | None = None,
    clock: Clock | None = None,
    rng: ScriptedRng | None = None,
    repo: MemoryDriveRepository | None = None,
) -> SimpleNamespace:
    clock = clock or Clock()
    if repo is None:
        repo = MemoryDriveRepository(clock=clock, id_factory=ids("r"))
    snapshot = snapshot if snapshot is not None else make_snapshot()
    config = config or make_config()
    sink = sink or FakeSink()
    observations: list = []
    manager = DriveManager(
        repo,
        snapshot,
        config,
        sink,
        observer=observations.append,
        clock=clock,
        rng=rng or ScriptedRng(),
        id_factory=ids("m"),
    )
    return SimpleNamespace(
        manager=manager,
        repo=repo,
        sink=sink,
        clock=clock,
        snapshot=snapshot,
        config=config,
        observations=observations,
    )


def creature_request(**over) -> CreateDriveRequest:
    base = dict(
        kind="generic",
        title="watch",
        scope_type="creature",
        scope_id="worker",
        owner=WORKER,
        owner_scope="creature",
        created_by=WORKER,
    )
    base.update(over)
    return CreateDriveRequest(**base)


def graph_request(**over) -> CreateDriveRequest:
    base = dict(
        kind="generic",
        title="watch",
        scope_type="graph",
        scope_id="g1",
        owner=USER,
        owner_scope="graph",
        created_by=USER,
        assignee_creature_id="worker",
    )
    base.update(over)
    return CreateDriveRequest(**base)


def kinds(observations) -> list[str]:
    return [o.kind for o in observations]
