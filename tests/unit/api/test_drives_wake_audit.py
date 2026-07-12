"""Follow-up: /wake route (frontend R1-38) + ACL'd graph-bound audit read.

Both are graph-bound per R1-02 and the audit read is record-ACL gated like
progress/delivery. Exercised against the REAL ``LocalTerrariumService`` with two
disconnected graphs.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.app import drive_error_handler
from kohakuterrarium.api.auth.dependencies import get_optional_user
from kohakuterrarium.api.auth.models import User
from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.sessions_v2 import drives as drives_mod
from kohakuterrarium.terrarium.creature_host import Creature
from kohakuterrarium.terrarium.drive.config import (
    DriveRuntimeConfig,
    default_registrations,
)
from kohakuterrarium.terrarium.drive.errors import DriveError
from kohakuterrarium.terrarium.drive.models import ActorRef
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.service import LocalTerrariumService
from kohakuterrarium.testing.terrarium import _FakeAgent

pytestmark = pytest.mark.timeout(30)

OWNER = ActorRef("user", "owner")


async def _two_graph_service():
    engine = Terrarium(
        drive_config=DriveRuntimeConfig(enabled=True),
        drive_registrations=default_registrations(),
    )
    await engine.__aenter__()
    a = Creature(
        creature_id="a", name="a", agent=_FakeAgent(name="a"), is_privileged=True
    )
    await engine.add_creature(a)
    b = Creature(
        creature_id="b", name="b", agent=_FakeAgent(name="b"), is_privileged=True
    )
    await engine.add_creature(b)
    return LocalTerrariumService(engine), engine, a.graph_id, b.graph_id


def _client(service, *, user=None):
    app = FastAPI()
    app.add_exception_handler(DriveError, drive_error_handler)
    app.dependency_overrides[get_service] = lambda: service
    app.dependency_overrides[get_optional_user] = lambda: user
    app.include_router(drives_mod.router, prefix="/api/sessions")
    return TestClient(app, raise_server_exceptions=False)


async def _make_drive(service, graph_id):
    from kohakuterrarium.studio.sessions import drives as facade

    view = await facade.create_record(
        service,
        graph_id=graph_id,
        actor=OWNER,
        body={"kind": "generic", "title": "d", "owner": OWNER.format()},
        is_privileged=True,
        operator=True,
    )
    return view["drive_id"], view["revision"]


async def test_wake_route_graph_bound(tmp_path):
    service, engine, gid_a, gid_b = await _two_graph_service()
    try:
        did, rev = await _make_drive(service, gid_a)
        client = _client(service)  # local operator

        # Wake in the correct graph succeeds.
        r = client.post(
            f"/api/sessions/{gid_a}/drives/{did}/wake",
            json={"expected_revision": rev},
        )
        assert r.status_code == 200, r.text

        # Wake through the wrong graph URL is not-found (no mutation leaked).
        r = client.post(
            f"/api/sessions/{gid_b}/drives/{did}/wake",
            json={"expected_revision": rev},
        )
        assert r.status_code == 404
    finally:
        await engine.shutdown()


async def test_audit_route_acl_and_graph_bound(tmp_path):
    service, engine, gid_a, gid_b = await _two_graph_service()
    try:
        did, _ = await _make_drive(service, gid_a)

        # Owner/operator sees the create audit entry.
        op = _client(service)
        r = op.get(f"/api/sessions/{gid_a}/drives/{did}/audit")
        assert r.status_code == 200, r.text
        audit = r.json()["audit"]
        assert audit and any(a["operation"] for a in audit)
        assert all(a["drive_id"] == did for a in audit)

        # A non-owner / non-operator is denied the audit evidence.
        stranger = User(
            id=7,
            username="mallory",
            role="user",
            is_active=True,
            created_at="2026-01-01",
            last_login_at=None,
        )
        r = _client(service, user=stranger).get(
            f"/api/sessions/{gid_a}/drives/{did}/audit"
        )
        assert r.status_code == 403

        # Cross-graph audit read is not-found.
        r = op.get(f"/api/sessions/{gid_b}/drives/{did}/audit")
        assert r.status_code == 404
    finally:
        await engine.shutdown()
