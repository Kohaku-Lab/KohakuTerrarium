"""Unit tests for :mod:`kohakuterrarium.api.routes.sessions_v2.drives`.

The route is a thin adapter over the Studio Drive façade; these drive it against
a fake :class:`TerrariumService` that returns real :class:`DriveView` records or
raises real Drive errors, so the whole route → façade path (actor derivation,
row redaction, detail, HTTP status mapping) is exercised. Status matrix per
impl-plan Phase J: 409 conflict / 403 permission / 422 registration+transition /
404 not found.
"""

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.app import drive_error_handler
from kohakuterrarium.api.auth.dependencies import get_optional_user
from kohakuterrarium.api.auth.models import User
from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.sessions_v2 import drives as drives_mod
from kohakuterrarium.terrarium.drive.errors import (
    DriveConflictError,
    DriveError,
    DriveNotFoundError,
    DrivePermissionError,
    DriveRegistrationDisabledError,
    DriveTransitionError,
)
from kohakuterrarium.terrarium.drive.models import (
    ActorRef,
    DriveDelivery,
    DriveProgress,
    DriveRecord,
    DriveStatus,
)
from kohakuterrarium.terrarium.drive.wire_service import DriveView

_NOW = datetime(2026, 7, 12, tzinfo=timezone.utc)
_OWNER = ActorRef("service", "ops")


def _record(
    drive_id="d1", *, owner=_OWNER, revision=3, spec=None, **over
) -> DriveRecord:
    base = dict(
        drive_id=drive_id,
        kind="generic",
        schema_version=1,
        revision=revision,
        title="watch deploy",
        spec=spec if spec is not None else {"instruction": "monitor"},
        presentation={},
        metadata={},
        scope_type="graph",
        scope_id="g1",
        origin_scope_id="g1",
        status=DriveStatus.ACTIVE,
        status_reason=None,
        priority=0,
        policy_name="generic",
        created_by=owner,
        owner=owner,
        owner_scope="service",
        created_at=_NOW,
        updated_by=owner,
        updated_at=_NOW,
        lifecycle_epoch=0,
    )
    base.update(over)
    return DriveRecord(**base)


def _view(record=None, *, actions=("read", "update", "transition")) -> DriveView:
    return DriveView(
        record=record or _record(),
        assignee_creature_id="worker",
        assignment_state="assigned",
        availability="available",
        durability="ephemeral",
        allowed_actions=tuple(actions),
    )


class _FakeService:
    """A minimal DriveServiceProtocol stub returning DriveViews / raising errors."""

    _DEFAULT = object()

    def __init__(self, *, view=_DEFAULT, raise_on=None, listed=None):
        self._view = _view() if view is self._DEFAULT else view
        self._raise = raise_on or {}
        self._listed = (
            listed
            if listed is not None
            else (() if self._view is None else (self._view,))
        )
        self.calls: dict = {}

    def _maybe_raise(self, op):
        if op in self._raise:
            raise self._raise[op]

    async def get_drive(self, drive_id, *, actor, is_privileged=False):
        self._maybe_raise("get_drive")
        return None if self._view is None else self._view

    async def assert_drive_in_graph(
        self, drive_id, graph_id, *, actor, is_privileged=False
    ):
        self._maybe_raise("assert_drive_in_graph")
        self.calls["assert_drive_in_graph"] = {
            "drive_id": drive_id,
            "graph_id": graph_id,
        }

    async def list_drives(self, *, actor, is_privileged=False, **kw):
        self._maybe_raise("list_drives")
        self.calls["list_drives"] = {"actor": actor, **kw}
        return tuple(self._listed)

    async def create_drive(
        self, request, *, graph_id, actor, is_privileged=False, operator=False
    ):
        self._maybe_raise("create_drive")
        self.calls["create_drive"] = {
            "request": request,
            "actor": actor,
            "operator": operator,
        }
        return self._view

    async def update_drive(
        self,
        drive_id,
        patch,
        *,
        expected_revision,
        actor,
        idempotency_key=None,
        is_privileged=False,
    ):
        self._maybe_raise("update_drive")
        self.calls["update_drive"] = {
            "expected_revision": expected_revision,
            "actor": actor,
        }
        return self._view

    async def assign_drive(
        self,
        drive_id,
        cid,
        gid,
        *,
        expected_revision,
        actor,
        idempotency_key=None,
        is_privileged=False,
        operator=False,
    ):
        self._maybe_raise("assign_drive")
        return self._view

    async def transfer_drive_owner(
        self,
        drive_id,
        new_owner,
        *,
        expected_revision,
        actor,
        idempotency_key=None,
        is_privileged=False,
    ):
        self._maybe_raise("transfer_drive_owner")
        return self._view

    async def unassign_drive(
        self,
        drive_id,
        *,
        expected_revision,
        actor,
        idempotency_key=None,
        is_privileged=False,
    ):
        self._maybe_raise("unassign_drive")
        return self._view

    async def propose_drive_transition(
        self,
        drive_id,
        target,
        *,
        actor,
        evidence=None,
        reason=None,
        expected_revision=None,
        is_privileged=False,
    ):
        self._maybe_raise("propose_drive_transition")
        if getattr(self, "_pending_proposal", False):
            return {
                "proposal_id": "prop1",
                "drive_id": drive_id,
                "target_status": target.value,
                "pending": True,
            }
        return self._view

    async def approve_drive_proposal(
        self,
        proposal_id,
        *,
        actor,
        is_privileged=False,
        operator=False,
        drive_id=None,
        graph_id=None,
    ):
        self._maybe_raise("approve_drive_proposal")
        self.calls["approve"] = {
            "proposal_id": proposal_id,
            "operator": operator,
            "drive_id": drive_id,
            "graph_id": graph_id,
        }
        return self._view

    async def transition_drive(
        self,
        drive_id,
        target,
        *,
        expected_revision,
        actor,
        status_reason=None,
        idempotency_key=None,
        is_privileged=False,
    ):
        self._maybe_raise("transition_drive")
        return self._view

    async def report_drive_progress(
        self,
        drive_id,
        *,
        summary,
        evidence,
        actor,
        idempotency_key=None,
        is_privileged=False,
    ):
        self._maybe_raise("report_drive_progress")
        return DriveProgress(
            progress_id="p1",
            drive_id=drive_id,
            actor=actor,
            summary=summary,
            created_at=_NOW,
            evidence=evidence or {},
        )

    async def list_drive_deliveries(self, drive_id, *, actor, is_privileged=False):
        self._maybe_raise("list_drive_deliveries")
        return (
            DriveDelivery(
                delivery_id="dl1",
                drive_id=drive_id,
                drive_revision=3,
                lifecycle_epoch=0,
                assignment_id="a1",
                assignee_creature_id="worker",
                reason="ready",
                state="dead_letter",
                attempt=2,
                available_at=_NOW,
                created_at=_NOW,
            ),
        )

    async def list_drive_progress(self, drive_id, *, actor, is_privileged=False):
        self._maybe_raise("list_drive_progress")
        return ()

    async def replay_drive_delivery(
        self, delivery_id, *, actor, is_privileged=False, graph_id=None
    ):
        self._maybe_raise("replay_drive_delivery")
        return DriveDelivery(
            delivery_id=delivery_id,
            drive_id="d1",
            drive_revision=3,
            lifecycle_epoch=0,
            assignment_id="a1",
            assignee_creature_id="worker",
            reason="recovery",
            state="pending",
            attempt=3,
            available_at=_NOW,
            created_at=_NOW,
        )


def _client(service, *, user=None):
    app = FastAPI()
    app.add_exception_handler(DriveError, drive_error_handler)
    app.dependency_overrides[get_service] = lambda: service
    app.dependency_overrides[get_optional_user] = lambda: user
    app.include_router(drives_mod.router, prefix="/api/sessions")
    return TestClient(app, raise_server_exceptions=False)


class TestReadsAndRedaction:
    def test_list_rows_redact_spec(self):
        client = _client(_FakeService())
        resp = client.get("/api/sessions/g1/drives")
        assert resp.status_code == 200
        rows = resp.json()["drives"]
        assert len(rows) == 1
        assert "spec" not in rows[0] and "presentation" not in rows[0]
        assert rows[0]["allowed_actions"] == ["read", "update", "transition"]

    def test_detail_includes_spec_for_operator(self):
        client = _client(_FakeService())  # user=None -> local operator (privileged)
        resp = client.get("/api/sessions/g1/drives/d1")
        assert resp.status_code == 200
        assert resp.json()["spec"] == {"instruction": "monitor"}

    def test_detail_redacted_for_unauthorized_user(self):
        # A non-admin user who is not the owner sees the row shape, not the spec.
        user = User(
            id=7,
            username="bob",
            role="user",
            is_active=True,
            created_at="",
            last_login_at=None,
        )
        client = _client(_FakeService(), user=user)
        resp = client.get("/api/sessions/g1/drives/d1")
        assert resp.status_code == 200
        assert "spec" not in resp.json()

    def test_missing_drive_404(self):
        client = _client(_FakeService(view=None))
        resp = client.get("/api/sessions/g1/drives/ghost")
        assert resp.status_code == 404

    def test_deliveries_serialized(self):
        client = _client(_FakeService())
        resp = client.get("/api/sessions/g1/drives/d1/deliveries")
        assert resp.status_code == 200
        d = resp.json()["deliveries"][0]
        assert d["state"] == "dead_letter" and d["attempt"] == 2


class TestActorFromContextNotBody:
    def test_created_by_is_authenticated_actor(self):
        svc = _FakeService()
        client = _client(svc)  # user=None -> user:local
        resp = client.post(
            "/api/sessions/g1/drives",
            json={
                "kind": "generic",
                "title": "t",
                "created_by": "user:evil",
                "owner": "user:evil",
            },
        )
        assert resp.status_code == 200
        # The forged 'created_by' in the body is ignored; the request the service
        # received carries the authenticated actor.
        request = svc.calls["create_drive"]["request"]
        assert request.created_by == ActorRef("user", "local")

    def test_authenticated_user_actor(self):
        svc = _FakeService()
        user = User(
            id=42,
            username="alice",
            role="admin",
            is_active=True,
            created_at="",
            last_login_at=None,
        )
        client = _client(svc, user=user)
        resp = client.post(
            "/api/sessions/g1/drives", json={"kind": "generic", "title": "t"}
        )
        assert resp.status_code == 200
        assert svc.calls["create_drive"]["actor"] == ActorRef("user", "42")
        assert svc.calls["create_drive"]["operator"] is True


class TestStatusMatrix:
    def test_conflict_409(self):
        svc = _FakeService(
            raise_on={
                "update_drive": DriveConflictError(
                    "stale", expected_revision=1, actual_revision=3
                )
            }
        )
        client = _client(svc)
        resp = client.patch(
            "/api/sessions/g1/drives/d1", json={"expected_revision": 1, "title": "x"}
        )
        assert resp.status_code == 409

    def test_permission_403(self):
        svc = _FakeService(raise_on={"transition_drive": DrivePermissionError("nope")})
        client = _client(svc)
        resp = client.post(
            "/api/sessions/g1/drives/d1/transition",
            json={"target_status": "paused", "expected_revision": 3},
        )
        assert resp.status_code == 403

    def test_registration_disabled_422(self):
        svc = _FakeService(
            raise_on={"create_drive": DriveRegistrationDisabledError("goal off")}
        )
        client = _client(svc)
        resp = client.post(
            "/api/sessions/g1/drives", json={"kind": "goal", "title": "t"}
        )
        assert resp.status_code == 422

    def test_invalid_transition_422(self):
        svc = _FakeService(raise_on={"transition_drive": DriveTransitionError("bad")})
        client = _client(svc)
        resp = client.post(
            "/api/sessions/g1/drives/d1/transition",
            json={"target_status": "completed", "expected_revision": 3},
        )
        assert resp.status_code == 422

    def test_not_found_404(self):
        svc = _FakeService(raise_on={"update_drive": DriveNotFoundError("gone")})
        client = _client(svc)
        resp = client.patch(
            "/api/sessions/g1/drives/ghost", json={"expected_revision": 1}
        )
        assert resp.status_code == 404


class TestMutations:
    def test_progress_append(self):
        client = _client(_FakeService())
        resp = client.post(
            "/api/sessions/g1/drives/d1/progress",
            json={"summary": "halfway", "evidence": {"pct": 50}},
        )
        assert resp.status_code == 200
        assert resp.json()["summary"] == "halfway"

    def test_replay_returns_new_delivery(self):
        client = _client(_FakeService())
        resp = client.post("/api/sessions/g1/drives/deliveries/dl1/replay")
        assert resp.status_code == 200
        assert resp.json()["reason"] == "recovery"

    def test_unassign(self):
        client = _client(_FakeService())
        resp = client.post(
            "/api/sessions/g1/drives/d1/unassign", json={"expected_revision": 3}
        )
        assert resp.status_code == 200
        assert resp.json()["drive_id"] == "d1"

    def test_propose_immediate_returns_detail(self):
        client = _client(_FakeService())
        resp = client.post(
            "/api/sessions/g1/drives/d1/propose",
            json={"target_status": "completed", "evidence": {"ok": 1}},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"  # the fake view

    def test_propose_pending_returns_envelope(self):
        svc = _FakeService()
        svc._pending_proposal = True
        client = _client(svc)
        resp = client.post(
            "/api/sessions/g1/drives/d1/propose",
            json={"target_status": "completed"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["pending"] is True and body["proposal_id"] == "prop1"

    def test_approve_uses_proposal_id_and_operator(self):
        svc = _FakeService()
        client = _client(svc)  # user=None -> operator
        resp = client.post(
            "/api/sessions/g1/drives/d1/approve", json={"proposal_id": "prop1"}
        )
        assert resp.status_code == 200
        # The path Drive id + graph ride through so the service binds the
        # proposal to them before finalizing (R1-02).
        assert svc.calls["approve"] == {
            "proposal_id": "prop1",
            "operator": True,
            "drive_id": "d1",
            "graph_id": "g1",
        }
