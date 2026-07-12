"""Unit tests for :mod:`kohakuterrarium.studio.sessions.drives`.

The façade is exercised against a REAL Drive-enabled ``LocalTerrariumService``
(its genuine dependency): parse/build helpers, row redaction, detail
authorization, and the record operations end to end.
"""

from datetime import datetime, timezone

import pytest

from kohakuterrarium.studio.sessions import drives as facade
from kohakuterrarium.terrarium.creature_host import Creature
from kohakuterrarium.terrarium.drive.config import (
    DriveRuntimeConfig,
    default_registrations,
)
from kohakuterrarium.terrarium.drive.errors import DriveValidationError
from kohakuterrarium.terrarium.drive.models import ActorRef, DriveStatus
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.service import LocalTerrariumService
from kohakuterrarium.testing.terrarium import _FakeAgent

pytestmark = pytest.mark.timeout(30)

OPERATOR = ActorRef("user", "ops")
OTHER = ActorRef("user", "bob")


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
    return LocalTerrariumService(engine), engine, root.graph_id


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------


class TestParsing:
    def test_parse_status_and_statuses(self):
        assert facade.parse_status("active") is DriveStatus.ACTIVE
        assert facade.parse_statuses("active,blocked") == frozenset(
            {DriveStatus.ACTIVE, DriveStatus.BLOCKED}
        )
        assert facade.parse_statuses(None) is None
        with pytest.raises(DriveValidationError):
            facade.parse_status("nonsense")

    def test_parse_actor(self):
        assert facade.parse_actor("user:alice") == ActorRef("user", "alice")
        assert facade.parse_actor(None) is None
        assert facade.parse_actor(ActorRef("service", "x")) == ActorRef("service", "x")

    def test_build_create_request_defaults_owner_to_actor(self):
        req = facade.build_create_request(
            graph_id="g1",
            actor=OPERATOR,
            body={"kind": "generic", "title": "t"},
        )
        assert req.owner == OPERATOR
        assert req.created_by == OPERATOR  # actor, never a body field
        assert req.scope_type == "graph" and req.scope_id == "g1"
        assert req.owner_scope == "actor"

    def test_build_create_request_ignores_forged_created_by(self):
        req = facade.build_create_request(
            graph_id="g1",
            actor=OPERATOR,
            body={"kind": "generic", "title": "t", "created_by": "user:evil"},
        )
        assert req.created_by == OPERATOR

    def test_build_patch_only_set_fields_and_null_dicts(self):
        patch = facade.build_patch({"title": "x", "spec": None})
        changes = patch.changes()
        assert changes["title"] == "x"
        assert changes["spec"] == {}  # null dict field normalized, not None
        assert "priority" not in changes  # untouched stays UNSET

    def test_build_create_request_parses_iso_datetime(self):
        req = facade.build_create_request(
            graph_id="g1",
            actor=OPERATOR,
            body={
                "kind": "generic",
                "title": "t",
                "not_before": "2026-07-12T00:00:00Z",
            },
        )
        assert req.not_before == datetime(2026, 7, 12, tzinfo=timezone.utc)

    def test_creature_scope_requires_explicit_scope_id(self):
        # R1-33: a creature-scoped create must carry an explicit creature
        # scope_id — it must NOT silently default to the graph id.
        with pytest.raises(DriveValidationError):
            facade.build_create_request(
                graph_id="g1",
                actor=OPERATOR,
                body={"kind": "generic", "title": "t", "scope_type": "creature"},
            )

    def test_creature_scope_uses_supplied_scope_id(self):
        req = facade.build_create_request(
            graph_id="g1",
            actor=OPERATOR,
            body={
                "kind": "generic",
                "title": "t",
                "scope_type": "creature",
                "scope_id": "worker",
            },
        )
        assert req.scope_type == "creature"
        assert req.scope_id == "worker"

    def test_graph_scope_still_defaults_scope_id_to_graph(self):
        req = facade.build_create_request(
            graph_id="g1",
            actor=OPERATOR,
            body={"kind": "generic", "title": "t", "scope_type": "graph"},
        )
        assert req.scope_type == "graph"
        assert req.scope_id == "g1"


# ---------------------------------------------------------------------------
# redaction + record ops against a real service
# ---------------------------------------------------------------------------


class TestRecordOps:
    async def test_row_redacts_detail_exposes(self):
        svc, engine, gid = await _service_with_graph()
        try:
            created = await facade.create_record(
                svc,
                graph_id=gid,
                actor=OPERATOR,
                body={"kind": "generic", "title": "watch", "spec": {"k": "v"}},
                is_privileged=True,
                operator=True,
            )
            did = created["drive_id"]
            assert created["spec"] == {"k": "v"}  # detail keeps spec
            rows = await facade.list_records(
                svc, graph_id=gid, actor=OPERATOR, is_privileged=True
            )
            row = next(r for r in rows if r["drive_id"] == did)
            assert "spec" not in row and "metadata" not in row
            assert row["allowed_actions"]  # capability list present
        finally:
            await engine.shutdown()

    async def test_detail_redacted_for_non_owner(self):
        svc, engine, gid = await _service_with_graph()
        try:
            created = await facade.create_record(
                svc,
                graph_id=gid,
                actor=OPERATOR,
                body={"kind": "generic", "title": "watch", "spec": {"secret": 1}},
                is_privileged=True,
                operator=True,
            )
            did = created["drive_id"]
            # A different, non-privileged user is not the owner -> no spec.
            got = await facade.get_record(svc, did, actor=OTHER, is_privileged=False)
            assert got is not None and "spec" not in got
            # The owner (privileged operator) still sees it.
            owner_view = await facade.get_record(
                svc, did, actor=OPERATOR, is_privileged=True
            )
            assert owner_view["spec"] == {"secret": 1}
        finally:
            await engine.shutdown()

    async def test_full_lifecycle(self):
        svc, engine, gid = await _service_with_graph()
        try:
            created = await facade.create_record(
                svc,
                graph_id=gid,
                actor=OPERATOR,
                body={"kind": "generic", "title": "deploy"},
                is_privileged=True,
                operator=True,
            )
            did, rev = created["drive_id"], created["revision"]
            renamed = await facade.update_record(
                svc,
                did,
                expected_revision=rev,
                actor=OPERATOR,
                body={"title": "prod deploy"},
                is_privileged=True,
            )
            assert renamed["title"] == "prod deploy"
            assigned = await facade.assign_record(
                svc,
                did,
                assignee_creature_id="worker",
                assignee_graph_id=gid,
                expected_revision=renamed["revision"],
                actor=OPERATOR,
                is_privileged=True,
                operator=True,
            )
            assert assigned["assignee_creature_id"] == "worker"
            progress = await facade.report_progress_record(
                svc,
                did,
                summary="halfway",
                evidence={"pct": 50},
                actor=OPERATOR,
                is_privileged=True,
            )
            assert progress["summary"] == "halfway"
            paused = await facade.transition_record(
                svc,
                did,
                target_status="paused",
                expected_revision=assigned["revision"],
                actor=OPERATOR,
                is_privileged=True,
            )
            assert paused["status"] == "paused"
            deliveries = await facade.list_deliveries_records(
                svc, did, actor=OPERATOR, is_privileged=True
            )
            assert isinstance(deliveries, list)
        finally:
            await engine.shutdown()

    async def test_unassign_and_propose_terminal(self):
        svc, engine, gid = await _service_with_graph()
        try:
            created = await facade.create_record(
                svc,
                graph_id=gid,
                actor=OPERATOR,
                body={"kind": "generic", "title": "finishable"},
                is_privileged=True,
                operator=True,
            )
            did = created["drive_id"]
            assigned = await facade.assign_record(
                svc,
                did,
                assignee_creature_id="worker",
                assignee_graph_id=gid,
                expected_revision=created["revision"],
                actor=OPERATOR,
                is_privileged=True,
                operator=True,
            )
            unassigned = await facade.unassign_record(
                svc,
                did,
                expected_revision=assigned["revision"],
                actor=OPERATOR,
                is_privileged=True,
            )
            assert unassigned["assignee_creature_id"] is None
            # Generic registration has no verifier -> propose completes immediately.
            done = await facade.propose_terminal_record(
                svc,
                did,
                target_status="completed",
                actor=OPERATOR,
                evidence={"done": True},
                expected_revision=unassigned["revision"],
                is_privileged=True,
            )
            assert done["status"] == "completed"
        finally:
            await engine.shutdown()

    async def test_mine_filter_scopes_to_owner(self):
        svc, engine, gid = await _service_with_graph()
        try:
            await facade.create_record(
                svc,
                graph_id=gid,
                actor=OPERATOR,
                body={"kind": "generic", "title": "mine", "owner": "user:ops"},
                is_privileged=True,
                operator=True,
            )
            mine = await facade.list_records(
                svc, graph_id=gid, actor=OPERATOR, is_privileged=True, mine=True
            )
            assert all(r["owner"] == "user:ops" for r in mine)
        finally:
            await engine.shutdown()
