"""R1-20: service-level command execution must use the live registry + trusted
Drive context (service / creature / principal / operator).

The web/Lab command path went through ``agent_execute_command``, which resolved
built-ins only and built a context with no service/creature/principal/operator.
So plugin-contributed ``/goal`` was unknown and built-in ``/drives`` reported
unavailable. These pin the fixed contract.
"""

import pytest

from kohakuterrarium.terrarium.creature_host import Creature
from kohakuterrarium.terrarium.creature_ops import agent_execute_command
from kohakuterrarium.terrarium.drive.config import (
    DriveRuntimeConfig,
    default_registrations,
)
from kohakuterrarium.terrarium.drive.models import ActorRef
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.service import LocalTerrariumService
from kohakuterrarium.testing.terrarium import _FakeAgent

pytestmark = pytest.mark.timeout(30)

OPS = ActorRef("service", "ops")


class _Result:
    output = "ran"
    error = None
    success = True
    data = None


class _LiveCmd:
    """A plugin-contributed command that lives only in the agent registry."""

    seen: dict | None = None

    async def execute(self, args, context):
        type(self).seen = dict(context.extra or {})
        return _Result()


class _StubAgent:
    session = None
    name = "stub"

    def list_user_commands(self):
        return {"mycmd": _LiveCmd()}


async def _service_with_drive():
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
    svc = LocalTerrariumService(engine)
    from kohakuterrarium.studio.sessions import drives as facade

    await facade.create_record(
        svc,
        graph_id=root.graph_id,
        actor=OPS,
        body={"kind": "generic", "title": "watch-me"},
        is_privileged=True,
        operator=True,
    )
    return svc, engine


async def test_service_execute_command_supplies_drive_context():
    svc, engine = await _service_with_drive()
    try:
        resp = await svc.execute_command("root", "drives", "list")
        assert resp["success"] is True, resp
        assert "watch-me" in (resp["output"] or "")
    finally:
        await engine.shutdown()


async def test_agent_execute_command_resolves_live_registry_and_threads_context():
    _LiveCmd.seen = None
    resp = await agent_execute_command(
        _StubAgent(),
        "mycmd",
        "",
        service="SVC",
        creature_id="c1",
        principal="user:alice",
        is_operator=True,
    )
    assert resp["success"] is True
    # The trusted context DTO reached the command unchanged.
    assert _LiveCmd.seen["service"] == "SVC"
    assert _LiveCmd.seen["creature_id"] == "c1"
    assert _LiveCmd.seen["principal"] == "user:alice"
    assert _LiveCmd.seen["is_operator"] is True


async def test_missing_operator_context_defaults_unprivileged():
    _LiveCmd.seen = None
    await agent_execute_command(_StubAgent(), "mycmd", "")
    # Missing authority context is never elevated (R1-20/R1-21 contract).
    assert _LiveCmd.seen["is_operator"] is False
