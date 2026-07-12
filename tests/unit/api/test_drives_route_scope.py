"""R1-02: live Drive ID routes must bind to their ``{session_id}`` graph, and
progress/delivery history reads must enforce record ACL.

Exercises the REAL ``LocalTerrariumService`` (not the fake route service) with
two disconnected graphs. A Drive created in graph A must be unreachable and
unmutatable through graph B's URL, and its progress/delivery evidence must be
denied to a non-owner / non-operator caller.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.auth.models import User
from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.auth.dependencies import get_optional_user
from kohakuterrarium.api.routes.sessions_v2 import drives as drives_mod
from kohakuterrarium.api.app import drive_error_handler
from kohakuterrarium.terrarium.creature_host import Creature
from kohakuterrarium.terrarium.drive.config import (
    DriveRuntimeConfig,
    default_registrations,
)
from kohakuterrarium.terrarium.drive.errors import DriveError
from kohakuterrarium.terrarium.drive.models import ActorRef
from kohakuterrarium.terrarium.drive.registration import (
    DriveRegistrationDescriptor,
    GenericDriveRegistration,
)
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.service import LocalTerrariumService
from kohakuterrarium.testing.terrarium import _FakeAgent

pytestmark = pytest.mark.timeout(30)

OWNER = ActorRef("user", "owner")
# The actor the local operator console (user=None, L4 off) presents.
LOCAL = ActorRef("user", "local")


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


async def _two_graph_service(*, registrations=None):
    """A Drive-enabled engine holding two disconnected graphs (A and B)."""
    engine = Terrarium(
        drive_config=DriveRuntimeConfig(enabled=True),
        drive_registrations=registrations or default_registrations(),
    )
    await engine.__aenter__()
    root_a = Creature(
        creature_id="root_a",
        name="root_a",
        agent=_FakeAgent(name="a"),
        is_privileged=True,
    )
    await engine.add_creature(root_a)
    root_b = Creature(
        creature_id="root_b",
        name="root_b",
        agent=_FakeAgent(name="b"),
        is_privileged=True,
    )
    await engine.add_creature(root_b)
    assert root_a.graph_id != root_b.graph_id
    return LocalTerrariumService(engine), engine, root_a.graph_id, root_b.graph_id


def _client(service, *, user=None):
    app = FastAPI()
    app.add_exception_handler(DriveError, drive_error_handler)
    app.dependency_overrides[get_service] = lambda: service
    app.dependency_overrides[get_optional_user] = lambda: user
    app.include_router(drives_mod.router, prefix="/api/sessions")
    return TestClient(app, raise_server_exceptions=False)


async def _make_drive(service, graph_id, *, owner=OWNER, kind="generic"):
    from kohakuterrarium.studio.sessions import drives as facade

    view = await facade.create_record(
        service,
        graph_id=graph_id,
        actor=owner,
        body={"kind": kind, "title": "in A", "owner": owner.format()},
        is_privileged=True,
        operator=True,
    )
    return view["drive_id"]


async def test_cross_graph_proposal_approval_is_rejected(tmp_path):
    # A pending terminal proposal on a Drive in graph A must not be approvable
    # through graph B's URL, even when the caller owns a Drive in B that
    # satisfies the URL's Drive-in-graph check (R1-02 confused-deputy). The
    # proposal must survive the rejected approval and still approve via its own
    # graph's URL.
    service, engine, gid_a, gid_b = await _two_graph_service(
        registrations=[GenericDriveRegistration(), _ActorVerifierReg()]
    )
    try:
        client = _client(service)  # user=None → local operator console
        dA = await _make_drive(service, gid_a, owner=LOCAL, kind="verified")

        pend = client.post(
            f"/api/sessions/{gid_a}/drives/{dA}/propose",
            json={"target_status": "completed"},
        )
        assert pend.status_code == 200 and pend.json().get("pending") is True
        pid = pend.json()["proposal_id"]

        dB = await _make_drive(service, gid_b, owner=LOCAL)
        # (a) proposal's Drive is not the path Drive (a Drive the caller owns in B).
        hijack = client.post(
            f"/api/sessions/{gid_b}/drives/{dB}/approve",
            json={"proposal_id": pid},
        )
        assert hijack.status_code == 404

        # (b) the path Drive is correct but reached through the wrong graph URL.
        wrong_graph = client.post(
            f"/api/sessions/{gid_b}/drives/{dA}/approve",
            json={"proposal_id": pid},
        )
        assert wrong_graph.status_code == 404

        # No mutation leaked: the proposal is still pending on its own Drive.
        ok = client.post(
            f"/api/sessions/{gid_a}/drives/{dA}/approve",
            json={"proposal_id": pid},
        )
        assert ok.status_code == 200 and ok.json()["status"] == "completed"
    finally:
        await engine.shutdown()


async def test_id_routes_are_graph_bound(tmp_path):
    service, engine, gid_a, gid_b = await _two_graph_service()
    try:
        did = await _make_drive(service, gid_a)
        client = _client(service)  # user=None → local operator

        # Same Drive, wrong graph in the path → not found, everywhere.
        assert client.get(f"/api/sessions/{gid_b}/drives/{did}").status_code == 404
        assert (
            client.get(f"/api/sessions/{gid_b}/drives/{did}/progress").status_code
            == 404
        )
        assert (
            client.get(f"/api/sessions/{gid_b}/drives/{did}/deliveries").status_code
            == 404
        )
        r = client.patch(
            f"/api/sessions/{gid_b}/drives/{did}",
            json={"expected_revision": 1, "title": "hijacked"},
        )
        assert r.status_code == 404
        r = client.post(
            f"/api/sessions/{gid_b}/drives/{did}/transition",
            json={"target_status": "paused", "expected_revision": 1},
        )
        assert r.status_code == 404

        # No mutation leaked through: the real graph still sees the old title.
        got = client.get(f"/api/sessions/{gid_a}/drives/{did}")
        assert got.status_code == 200 and got.json()["title"] == "in A"

        # The correct graph still works.
        assert client.get(f"/api/sessions/{gid_a}/drives/{did}").status_code == 200
    finally:
        await engine.shutdown()


async def test_progress_delivery_history_requires_authorization(tmp_path):
    service, engine, gid_a, gid_b = await _two_graph_service()
    try:
        did = await _make_drive(service, gid_a)

        # Non-owner, non-operator user cannot read progress/delivery evidence.
        stranger = User(
            id=999,
            username="mallory",
            role="user",
            is_active=True,
            created_at="2026-01-01",
            last_login_at=None,
        )
        c_stranger = _client(service, user=stranger)
        assert (
            c_stranger.get(f"/api/sessions/{gid_a}/drives/{did}/progress").status_code
            == 403
        )
        assert (
            c_stranger.get(f"/api/sessions/{gid_a}/drives/{did}/deliveries").status_code
            == 403
        )

        # The local operator (user=None) can.
        c_op = _client(service)
        assert (
            c_op.get(f"/api/sessions/{gid_a}/drives/{did}/progress").status_code == 200
        )
        assert (
            c_op.get(f"/api/sessions/{gid_a}/drives/{did}/deliveries").status_code
            == 200
        )
    finally:
        await engine.shutdown()
