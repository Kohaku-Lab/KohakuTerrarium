"""Unit tests for the generic ``/drives`` built-in command.

Driven against a REAL Drive-enabled ``LocalTerrariumService`` through the trusted
``UserCommandContext`` seam. Covers self vs foreign authorization, the
missing-revision guard, unavailable runtime, and GoalPlugin independence.
"""

import pytest

from kohakuterrarium.builtins.user_commands import get_builtin_user_command
from kohakuterrarium.modules.user_command.base import UserCommandContext
from kohakuterrarium.terrarium.creature_host import Creature
from kohakuterrarium.terrarium.drive.config import (
    DriveRuntimeConfig,
    default_registrations,
)
from kohakuterrarium.terrarium.drive.models import ActorRef
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.service import LocalTerrariumService
from kohakuterrarium.testing.terrarium import _FakeAgent

pytestmark = pytest.mark.timeout(30)


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
    return LocalTerrariumService(engine), engine


def _ctx(service, *, principal="user:ops", is_operator=True, creature_id="root"):
    return UserCommandContext(
        extra={
            "service": service,
            "creature_id": creature_id,
            "principal": principal,
            "is_operator": is_operator,
        }
    )


def _cmd():
    return get_builtin_user_command("drives")


class TestRegistration:
    def test_registered_as_builtin(self):
        cmd = _cmd()
        assert cmd is not None
        assert cmd.name == "drives"


class TestUnavailable:
    async def test_no_service(self):
        cmd = _cmd()
        result = await cmd.execute("list", UserCommandContext(extra={}))
        assert result.error and "terrarium" in result.error

    async def test_no_creature(self):
        svc, engine = await _service_with_graph()
        try:
            ctx = UserCommandContext(extra={"service": svc, "principal": "user:ops"})
            result = await cmd_list(ctx)
            assert result.error and "focused creature" in result.error
        finally:
            await engine.shutdown()


async def cmd_list(ctx):
    return await _cmd().execute("list", ctx)


class TestLifecycle:
    async def test_create_list_show_pause(self):
        svc, engine = await _service_with_graph()
        try:
            ctx = _ctx(svc)
            created = await _cmd().execute(
                "create --kind generic --title watch-deploy", ctx
            )
            assert created.success and "Drive created" in created.output
            listing = await _cmd().execute("list", ctx)
            assert "watch-deploy" in listing.output
            assert listing.data["type"] == "list"
            # Pull the drive id from a fresh list row via the service.
            rows = await _drive_rows(svc)
            did, rev = rows[0]["drive_id"], rows[0]["revision"]
            shown = await _cmd().execute(f"show {did}", ctx)
            assert did in shown.output and shown.data["type"] == "info_panel"
            paused = await _cmd().execute(f"pause {did} --revision {rev}", ctx)
            assert paused.success and "paused" in paused.output.lower()
        finally:
            await engine.shutdown()

    async def test_missing_revision_guard(self):
        svc, engine = await _service_with_graph()
        try:
            ctx = _ctx(svc)
            await _cmd().execute("create --kind generic --title t", ctx)
            rows = await _drive_rows(svc)
            did = rows[0]["drive_id"]
            result = await _cmd().execute(f"pause {did}", ctx)
            assert result.error and "--revision" in result.error
        finally:
            await engine.shutdown()


class TestOperatorDefault:
    def test_missing_operator_context_is_not_elevated(self):
        # R1-21: omitting the authority bit resolves to unprivileged, never a
        # silent operator grant.
        from kohakuterrarium.builtins.user_commands import drives as drives_mod

        ctx = UserCommandContext(
            extra={"service": object(), "creature_id": "root", "principal": "user:ops"}
        )
        resolved = drives_mod._resolve(ctx)
        assert resolved.is_operator is False


class TestAuthorization:
    async def test_foreign_actor_cannot_pause(self):
        svc, engine = await _service_with_graph()
        try:
            # Owner (operator) creates a drive owned by user:ops.
            await _cmd().execute(
                "create --kind generic --title owned", _ctx(svc, principal="user:ops")
            )
            rows = await _drive_rows(svc)
            did, rev = rows[0]["drive_id"], rows[0]["revision"]
            # A different, non-operator user may not pause someone else's drive.
            foreign = _ctx(svc, principal="user:bob", is_operator=False)
            result = await _cmd().execute(f"pause {did} --revision {rev}", foreign)
            assert result.error and "permission" in result.error.lower()
        finally:
            await engine.shutdown()


async def _drive_rows(service):
    from kohakuterrarium.studio.sessions import drives as facade

    info = await service.list_creatures()
    gid = info[0].graph_id
    return await facade.list_records(
        service, graph_id=gid, actor=ActorRef("user", "ops"), is_privileged=True
    )
