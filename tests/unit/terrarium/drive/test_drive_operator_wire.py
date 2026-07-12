"""R1-15: operator authority must thread through the Remote/MultiNode/worker wire.

The Local service accepts ``operator`` for create/assign/approve, but the
cross-node signatures dropped it — so a Studio/API call passing ``operator=``
either ``TypeError``\\ ed on lab-host or silently lost the operator grant. These
pin that an operator-only actor (non-privileged) can drive create/assign over
the wire, and that omitting operator is still denied.
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
from kohakuterrarium.terrarium.drive.errors import DriveError, DrivePermissionError
from kohakuterrarium.terrarium.drive.models import ActorRef, DriveStatus
from kohakuterrarium.terrarium.drive.registration import (
    DriveRegistrationDescriptor,
    GenericDriveRegistration,
)
from kohakuterrarium.terrarium.drive.remote_ops import RemoteDriveServiceMixin
from kohakuterrarium.terrarium.drive.requests import CreateDriveRequest
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.service import LocalTerrariumService
from kohakuterrarium.testing.terrarium import _FakeAgent

pytestmark = pytest.mark.timeout(30)

USER = ActorRef("user", "u1")  # a plain, non-privileged principal


class _ActorVerifierReg(GenericDriveRegistration):
    """A ``verified``-kind registration whose terminal proposal awaits approval."""

    name = "verified"
    kind = "verified"

    def descriptor(self) -> DriveRegistrationDescriptor:
        return DriveRegistrationDescriptor(
            name=self.name,
            kind=self.kind,
            schema_version=1,
            required_roles=frozenset({"spec", "transition", "readiness"}),
            optional_roles=frozenset({"projection", "verifier", "prompt"}),
            verifier_mode="actor",
        )


class _Remote(RemoteDriveServiceMixin):
    """Remote surface backed by a local worker handler (host-originated)."""

    def __init__(self, local: LocalTerrariumService) -> None:
        self._local = local

    async def _req(self, type_, body):
        msg = AppMessage(
            namespace="terrarium.runtime",
            type=type_,
            body=body,
            sender_node="_host",
            request_id="r",
            in_reply_to=None,
        )
        return await handle_drive_request(self._local, msg)


async def _worker():
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
    return engine, root.graph_id


def _req(gid):
    return CreateDriveRequest(
        kind="generic",
        title="w",
        scope_type="graph",
        scope_id=gid,
        owner=USER,
        owner_scope="actor",
        created_by=USER,
        spec={},
    )


async def test_operator_threads_through_remote_create_and_assign():
    engine, gid = await _worker()
    remote = _Remote(LocalTerrariumService(engine))
    try:
        # Operator-only actor (not privileged) can create a graph-scoped Drive.
        view = await remote.create_drive(
            _req(gid), graph_id=gid, actor=USER, is_privileged=False, operator=True
        )
        did = view.record.drive_id
        assert view.record.owner == USER

        # ... and can assign it to a graph member.
        assigned = await remote.assign_drive(
            did,
            "worker",
            gid,
            expected_revision=view.record.revision,
            actor=USER,
            is_privileged=False,
            operator=True,
        )
        assert assigned.assignee_creature_id == "worker"
    finally:
        await engine.shutdown()


async def test_without_operator_the_same_actor_is_denied():
    engine, gid = await _worker()
    remote = _Remote(LocalTerrariumService(engine))
    try:
        with pytest.raises(DrivePermissionError):
            await remote.create_drive(
                _req(gid),
                graph_id=gid,
                actor=USER,
                is_privileged=False,
                operator=False,
            )
    finally:
        await engine.shutdown()


async def test_approve_signature_accepts_operator():
    engine, gid = await _worker()
    remote = _Remote(LocalTerrariumService(engine))
    try:
        # No such proposal → a typed DriveError, NOT a TypeError from a
        # dropped ``operator`` kwarg.
        with pytest.raises(DriveError):
            await remote.approve_drive_proposal(
                "nope", actor=USER, is_privileged=False, operator=True
            )
    finally:
        await engine.shutdown()


async def _verifier_worker():
    engine = Terrarium(
        drive_config=DriveRuntimeConfig(enabled=True),
        drive_registrations=[GenericDriveRegistration(), _ActorVerifierReg()],
    )
    await engine.__aenter__()
    root = Creature(
        creature_id="root",
        name="root",
        agent=_FakeAgent(name="root"),
        is_privileged=True,
    )
    await engine.add_creature(root)
    return engine, root.graph_id


def _verified_req(gid):
    return CreateDriveRequest(
        kind="verified",
        title="w",
        scope_type="graph",
        scope_id=gid,
        owner=USER,
        owner_scope="actor",
        created_by=USER,
        spec={},
    )


async def test_remote_operator_approval_succeeds_and_audits_grant():
    # R1-15: an operator-authorized proposal approval must round-trip over the
    # worker wire and write the same ``operator_grant`` audit row a local approve
    # would — not only create/assign/denial.
    engine, gid = await _verifier_worker()
    remote = _Remote(LocalTerrariumService(engine))
    try:
        view = await remote.create_drive(
            _verified_req(gid), graph_id=gid, actor=USER, operator=True
        )
        did = view.record.drive_id

        # Owner proposes a terminal transition; an ``actor``-mode verifier defers
        # to an authorized approver, so this is a pending proposal, not a commit.
        pending = await remote.propose_drive_transition(
            did, DriveStatus.COMPLETED, actor=USER
        )
        assert pending.get("pending") is True
        proposal_id = pending["proposal_id"]

        approved = await remote.approve_drive_proposal(
            proposal_id, actor=USER, operator=True
        )
        assert approved.record.status is DriveStatus.COMPLETED

        audit_ops = [
            a.operation for a in await remote.list_drive_audit(did, actor=USER)
        ]
        assert "operator_grant" in audit_ops
    finally:
        await engine.shutdown()
