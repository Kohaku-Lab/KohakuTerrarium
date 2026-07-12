"""Unit tests for the built-in GoalPlugin + its ``/goal`` command (design §11).

Covers the plugin's command + prompt contribution and the ``/goal`` command's
deterministic parse + trusted-context resolution + USER-actor propagation +
least-privilege ownership. The registration/spec policy is tested separately in
``tests/unit/terrarium/drive/test_drive_goal.py``.
"""

from types import SimpleNamespace

import pytest

from kohakuterrarium.builtins.plugins.goal import GoalPlugin
from kohakuterrarium.builtins.plugins.goal import plugin as goal_plugin_mod
from kohakuterrarium.builtins.plugins.goal.plugin import GoalCommand
from kohakuterrarium.modules.plugin.base import PluginContext
from kohakuterrarium.modules.user_command.base import UserCommandContext
from kohakuterrarium.terrarium.drive.models import ActorRef, DriveStatus

# ── GoalPlugin ──────────────────────────────────────────────────────


class TestGoalPlugin:
    def test_contributes_goal_command(self):
        plugin = GoalPlugin()
        cmds = plugin.contribute_user_commands()
        assert set(cmds) == {"goal"}
        assert isinstance(cmds["goal"], GoalCommand)

    def test_prompt_content_is_bounded_str(self):
        content = GoalPlugin().get_prompt_content(PluginContext())
        assert isinstance(content, str) and len(content) < 2048

    def test_contribute_commands_is_empty(self):
        # It contributes a USER command, not a controller ##command##.
        assert GoalPlugin().contribute_commands() == {}


# ── /goal command: parse + trusted context + actor propagation ──────


def _fake_view(
    *, drive_id="d1", revision=0, status=DriveStatus.ACTIVE, spec=None, kind="goal"
):
    return SimpleNamespace(
        record=SimpleNamespace(
            drive_id=drive_id,
            revision=revision,
            status=status,
            kind=kind,
            spec=spec or {"objective": "obj", "autonomy": "manual"},
            title="obj",
            owner=ActorRef("user", "alice"),
            created_at=0,
        ),
        assignee_creature_id="worker",
    )


class _FakeService:
    """Records the actor / privilege / request each /goal call forwards.

    ``drive_kind`` controls the kind of any Drive resolved by id — set it to a
    non-goal kind to prove ``/goal`` refuses to mutate foreign-kind records.
    """

    def __init__(self, drive_kind="goal"):
        self.create_call = None
        self.propose_call = None
        self.transition_call = None
        self.assign_call = None
        self._drive_kind = drive_kind

    async def get_creature_info(self, creature_id):
        return SimpleNamespace(creature_id=creature_id, graph_id="g1")

    async def create_drive(
        self, request, *, graph_id, actor, is_privileged=False, operator=False
    ):
        self.create_call = SimpleNamespace(
            request=request,
            graph_id=graph_id,
            actor=actor,
            is_privileged=is_privileged,
            operator=operator,
        )
        return _fake_view(spec=request.spec)

    async def list_drives(self, *, actor, assignee_creature_id=None, **kw):
        return (_fake_view(),)

    async def get_drive(self, drive_id, *, actor, is_privileged=False):
        return _fake_view(drive_id=drive_id, kind=self._drive_kind)

    async def assign_drive(
        self,
        drive_id,
        target_creature,
        target_graph,
        *,
        expected_revision,
        actor,
        operator=False,
        **kw,
    ):
        self.assign_call = SimpleNamespace(
            drive_id=drive_id, target=target_creature, operator=operator
        )
        return _fake_view(drive_id=drive_id)

    async def transition_drive(
        self, drive_id, target, *, expected_revision, actor, is_privileged=False, **kw
    ):
        self.transition_call = SimpleNamespace(
            actor=actor, target=target, is_privileged=is_privileged
        )
        return _fake_view(drive_id=drive_id, status=target)

    async def propose_drive_transition(
        self, drive_id, target, *, actor, is_privileged=False, **kw
    ):
        self.propose_call = SimpleNamespace(
            actor=actor, target=target, is_privileged=is_privileged
        )
        return _fake_view(drive_id=drive_id, status=DriveStatus.COMPLETED)


def _ctx(**extra):
    return UserCommandContext(agent=None, session=None, extra=extra)


class TestGoalCommand:
    async def test_parse_set_flags_and_criteria(self):
        opts, objective = goal_plugin_mod._parse_set(
            "autonomy=continue_when_ready policy=user_confirm Fix the auth race"
        )
        assert opts == {
            "autonomy": "continue_when_ready",
            "policy": "user_confirm",
        }
        assert objective == "Fix the auth race"
        assert goal_plugin_mod._split_criteria("a; b, c") == ["a", "b", "c"]

    async def test_unavailable_without_service(self):
        cmd = GoalCommand()
        res = await cmd._execute("show", _ctx())
        assert res.error and "terrarium" in res.error

    async def test_unavailable_without_creature(self):
        cmd = GoalCommand()
        res = await cmd._execute("show", _ctx(service=_FakeService()))
        assert res.error and "creature" in res.error

    async def test_non_user_principal_rejected(self):
        cmd = GoalCommand()
        res = await cmd._execute(
            "show",
            _ctx(service=_FakeService(), creature_id="worker", principal="creature:w"),
        )
        assert res.error and "user actor" in res.error

    async def test_missing_operator_context_is_not_elevated(self):
        # R1-21: an adapter that supplies service/creature but omits the
        # authority bit must NOT silently grant operator elevation.
        resolved = goal_plugin_mod._resolve(
            _ctx(service=_FakeService(), creature_id="worker", principal="user:alice")
        )
        assert resolved.is_operator is False

    async def test_set_without_operator_forwards_non_operator(self):
        svc = _FakeService()
        cmd = GoalCommand()
        res = await cmd._execute(
            "set autonomy=continue_when_ready Fix the auth race",
            _ctx(service=svc, creature_id="worker", principal="user:alice"),
        )
        assert res.success, res.error
        # Omitted is_operator → the create is attempted as a plain user, never
        # as an operator (graph-authority is denied downstream, not defaulted on).
        assert svc.create_call.operator is False

    async def test_set_creates_user_owned_drive_with_user_actor(self):
        svc = _FakeService()
        cmd = GoalCommand()
        res = await cmd._execute(
            "set autonomy=continue_when_ready Fix the auth race",
            _ctx(
                service=svc,
                creature_id="worker",
                principal="user:alice",
                is_operator=True,
            ),
        )
        assert res.success, res.error
        call = svc.create_call
        # The command acts as the USER actor, never plugin/creature (§11.5). The
        # graph-authority elevation is an explicit, audited operator grant — NOT
        # creature privilege (is_privileged stays False).
        assert call.actor == ActorRef("user", "alice")
        assert call.operator is True
        assert call.is_privileged is False
        assert call.request.owner == ActorRef("user", "alice")
        assert call.request.owner_scope == "actor"
        assert call.request.kind == "goal"
        assert call.request.assignee_creature_id == "worker"
        assert call.request.spec["autonomy"] == "continue_when_ready"

    async def test_complete_proposes_as_user_owner_without_privilege(self):
        # Completion goes through the owner's propose_terminal capability — no
        # operator privilege (least privilege; the user owns the goal).
        svc = _FakeService()
        cmd = GoalCommand()
        res = await cmd._execute(
            "complete d1",
            _ctx(service=svc, creature_id="worker", principal="user:alice"),
        )
        assert res.success, res.error
        assert svc.propose_call.actor == ActorRef("user", "alice")
        assert svc.propose_call.target == DriveStatus.COMPLETED
        assert svc.propose_call.is_privileged is False

    async def test_pause_uses_owner_capability_not_operator(self):
        # pause/resume/cancel are owner transitions — no operator privilege.
        svc = _FakeService()
        cmd = GoalCommand()
        res = await cmd._execute(
            "pause d1",
            _ctx(service=svc, creature_id="worker", principal="user:alice"),
        )
        assert res.success, res.error
        assert svc.transition_call.target == DriveStatus.PAUSED
        assert svc.transition_call.is_privileged is False

    async def test_bad_subcommand_shows_usage(self):
        cmd = GoalCommand()
        res = await cmd._execute("frobnicate", _ctx())
        assert res.error and res.error.startswith("usage:")


class TestGoalKindGuard:
    """R1-22: an explicit id must resolve to a goal-kind Drive for every verb."""

    def _foreign_ctx(self):
        return _ctx(
            service=_FakeService(drive_kind="generic"),
            creature_id="worker",
            principal="user:alice",
            is_operator=True,
        )

    async def test_show_rejects_non_goal(self):
        res = await GoalCommand()._execute("show d1", self._foreign_ctx())
        assert res.error and "not a goal" in res.error

    @pytest.mark.parametrize("verb", ["pause", "resume", "cancel"])
    async def test_transition_rejects_non_goal(self, verb):
        res = await GoalCommand()._execute(f"{verb} d1", self._foreign_ctx())
        assert res.error and "not a goal" in res.error

    async def test_complete_rejects_non_goal(self):
        svc = _FakeService(drive_kind="generic")
        res = await GoalCommand()._execute(
            "complete d1",
            _ctx(service=svc, creature_id="worker", principal="user:alice"),
        )
        assert res.error and "not a goal" in res.error
        assert svc.propose_call is None  # never proposed a terminal on a non-goal

    async def test_assign_rejects_non_goal(self):
        svc = _FakeService(drive_kind="generic")
        res = await GoalCommand()._execute(
            "assign d1 worker2",
            _ctx(
                service=svc,
                creature_id="worker",
                principal="user:alice",
                is_operator=True,
            ),
        )
        assert res.error and "not a goal" in res.error
        assert svc.assign_call is None  # never assigned a foreign-kind record
