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
        assert "act directly" in content
        assert "drive_status or group_drive" in content

    def test_contribute_commands_is_empty(self):
        # It contributes a USER command, not a controller ##command##.
        assert GoalPlugin().contribute_commands() == {}


# ── /goal command: parse + trusted context + actor propagation ──────


def _fake_view(
    *,
    drive_id="d1",
    revision=0,
    status=DriveStatus.ACTIVE,
    spec=None,
    kind="goal",
    title="obj",
    created_at=0,
    owner=ActorRef("user", "alice"),
):
    return SimpleNamespace(
        record=SimpleNamespace(
            drive_id=drive_id,
            revision=revision,
            status=status,
            kind=kind,
            spec=spec or {"objective": title, "autonomy": "manual"},
            title=title,
            owner=owner,
            created_at=created_at,
        ),
        assignee_creature_id="worker",
    )


class _FakeService:
    """Records the actor / privilege / request each /goal call forwards.

    ``drive_kind`` controls the kind of any Drive resolved by id — set it to a
    non-goal kind to prove ``/goal`` refuses to mutate foreign-kind records.
    """

    def __init__(self, drive_kind="goal", *, views=None):
        self.create_call = None
        self.propose_call = None
        self.transition_call = None
        self.wake_call = None
        self.assign_call = None
        self.list_calls = []
        self._drive_kind = drive_kind
        self._views = (
            list(views) if views is not None else [_fake_view(kind=self._drive_kind)]
        )

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

    async def wake_drive(
        self, drive_id, *, actor, expected_revision=None, is_privileged=False
    ):
        self.wake_call = SimpleNamespace(
            drive_id=drive_id,
            actor=actor,
            expected_revision=expected_revision,
            is_privileged=is_privileged,
        )
        return _fake_view(drive_id=drive_id)

    async def list_drives(self, *, actor, assignee_creature_id=None, **kw):
        self.list_calls.append({"assignee_creature_id": assignee_creature_id, **kw})
        return tuple(self._views)

    async def get_drive(self, drive_id, *, actor, is_privileged=False):
        for view in self._views:
            if view.record.drive_id == drive_id:
                return view
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

    async def test_set_defaults_to_manual_autonomy(self):
        # The command shares the registration default: no self-continuing
        # loop unless the user asks for one.
        svc = _FakeService(views=[])
        res = await GoalCommand()._execute(
            "set Fix the auth race",
            _ctx(
                service=svc,
                creature_id="worker",
                principal="user:alice",
                is_operator=True,
            ),
        )
        assert res.success, res.error
        assert svc.create_call.request.spec["autonomy"] == "manual"
        assert svc.wake_call.drive_id == "d1"
        assert "note: autonomy=manual" in res.output

    async def test_set_reports_other_live_goals(self):
        svc = _FakeService(
            views=[
                _fake_view(drive_id="older", status=DriveStatus.ACTIVE),
                _fake_view(drive_id="done", status=DriveStatus.COMPLETED),
            ]
        )
        res = await GoalCommand()._execute(
            "set autonomy=continue_when_ready Fix the auth race",
            _ctx(service=svc, creature_id="worker", principal="user:alice"),
        )
        assert res.success, res.error
        assert "1 other live goal(s)" in res.output
        assert "older [active]" in res.output
        assert "done" not in res.output
        assert "autonomy=manual" not in res.output

    async def test_set_at_goal_limit_explains_backpressure(self):
        from kohakuterrarium.terrarium.drive.errors import DriveBackpressureError

        class _Full(_FakeService):
            async def create_drive(self, request, **kw):
                raise DriveBackpressureError("assignee at the active-Drive limit (8)")

        res = await GoalCommand()._execute(
            "set Fix the auth race",
            _ctx(service=_Full(views=[]), creature_id="worker", principal="user:alice"),
        )
        assert res.error and "at the goal limit" in res.error
        assert "/goal pause|cancel" in res.error

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
        assert svc.wake_call.drive_id == "d1"
        assert svc.wake_call.actor == ActorRef("user", "alice")
        assert svc.wake_call.expected_revision == 0
        assert svc.wake_call.is_privileged is False

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

    async def test_pause_elevates_operator_for_creature_owned_goal(self):
        # A goal created by a creature (owner=creature) is not the user's own;
        # an operator user still manages it via explicit operator privilege.
        svc = _FakeService(
            views=[_fake_view(drive_id="d1", owner=ActorRef("creature", "worker"))]
        )
        cmd = GoalCommand()
        res = await cmd._execute(
            "pause d1",
            _ctx(
                service=svc,
                creature_id="worker",
                principal="user:alice",
                is_operator=True,
            ),
        )
        assert res.success, res.error
        assert svc.transition_call.target == DriveStatus.PAUSED
        assert svc.transition_call.is_privileged is True

    async def test_pause_non_operator_user_does_not_elevate(self):
        # A non-operator user is never elevated, even for creature-owned goals.
        svc = _FakeService(
            views=[_fake_view(drive_id="d1", owner=ActorRef("creature", "worker"))]
        )
        cmd = GoalCommand()
        res = await cmd._execute(
            "pause d1",
            _ctx(
                service=svc,
                creature_id="worker",
                principal="user:alice",
                is_operator=False,
            ),
        )
        assert res.success, res.error
        assert svc.transition_call.is_privileged is False

    async def test_complete_elevates_operator_for_creature_owned_goal(self):
        # Completion of a creature-owned goal also rides the operator grant.
        svc = _FakeService(
            views=[_fake_view(drive_id="d1", owner=ActorRef("creature", "worker"))]
        )
        cmd = GoalCommand()
        res = await cmd._execute(
            "complete d1",
            _ctx(
                service=svc,
                creature_id="worker",
                principal="user:alice",
                is_operator=True,
            ),
        )
        assert res.success, res.error
        assert svc.propose_call.target == DriveStatus.COMPLETED
        assert svc.propose_call.is_privileged is True

    async def test_list_shows_only_live_goals_with_explicit_status(self):
        svc = _FakeService(
            views=[
                _fake_view(
                    drive_id="active", status=DriveStatus.ACTIVE, title="Current"
                ),
                _fake_view(
                    drive_id="paused", status=DriveStatus.PAUSED, title="Paused"
                ),
                _fake_view(drive_id="done", status=DriveStatus.COMPLETED, title="Old"),
                _fake_view(
                    drive_id="cancelled",
                    status=DriveStatus.CANCELLED,
                    title="Dropped",
                ),
            ]
        )
        res = await GoalCommand()._execute(
            "list", _ctx(service=svc, creature_id="worker", principal="user:alice")
        )
        assert res.success, res.error
        assert "active  [status: active]  Current" in res.output
        assert "paused  [status: paused]  Paused" in res.output
        assert "done" not in res.output
        assert "cancelled" not in res.output
        labels = [item["label"] for item in res.data["items"]]
        assert labels == ["Current [status: active]", "Paused [status: paused]"]

    async def test_list_reports_when_there_are_no_live_goals(self):
        svc = _FakeService(views=[_fake_view(status=DriveStatus.COMPLETED)])
        res = await GoalCommand()._execute(
            "list", _ctx(service=svc, creature_id="worker", principal="user:alice")
        )
        assert res.output == "No live goals in this graph."

    async def test_list_is_graph_wide_and_shows_assignees(self):
        svc = _FakeService(views=[_fake_view(drive_id="d1", title="Current")])
        res = await GoalCommand()._execute(
            "list", _ctx(service=svc, creature_id="worker", principal="user:alice")
        )
        assert res.success, res.error
        assert svc.list_calls[-1].get("graph_id") == "g1"
        assert svc.list_calls[-1]["assignee_creature_id"] is None
        assert "→ worker" in res.output
        assert "assignee=worker" in res.data["items"][0]["description"]

    async def test_resume_on_active_manual_goal_wakes_without_transition(self):
        svc = _FakeService(views=[_fake_view(drive_id="d1", status=DriveStatus.ACTIVE)])
        res = await GoalCommand()._execute(
            "resume", _ctx(service=svc, creature_id="worker", principal="user:alice")
        )
        assert res.success, res.error
        assert svc.transition_call is None
        assert svc.wake_call.drive_id == "d1"
        assert res.output.startswith("Goal resumed: d1")

    async def test_resume_on_paused_goal_transitions_then_wakes(self):
        svc = _FakeService(
            views=[_fake_view(drive_id="d1", status=DriveStatus.PAUSED, revision=4)]
        )
        res = await GoalCommand()._execute(
            "resume d1", _ctx(service=svc, creature_id="worker", principal="user:alice")
        )
        assert res.success, res.error
        assert svc.transition_call.target is DriveStatus.ACTIVE
        assert svc.wake_call.drive_id == "d1"

    async def test_bare_goal_skips_newer_terminal_history(self):
        svc = _FakeService(
            views=[
                _fake_view(drive_id="live", status=DriveStatus.ACTIVE, created_at=1),
                _fake_view(
                    drive_id="stale", status=DriveStatus.COMPLETED, created_at=2
                ),
            ]
        )
        res = await GoalCommand()._execute(
            "", _ctx(service=svc, creature_id="worker", principal="user:alice")
        )
        assert res.success, res.error
        assert "Goal: live [status: active]" in res.output

    async def test_show_by_id_keeps_terminal_history_available(self):
        svc = _FakeService(
            views=[_fake_view(drive_id="old", status=DriveStatus.CANCELLED)]
        )
        res = await GoalCommand()._execute(
            "show old",
            _ctx(service=svc, creature_id="worker", principal="user:alice"),
        )
        assert res.success, res.error
        assert "Goal: old [status: cancelled]" in res.output

    @pytest.mark.parametrize(
        ("command", "eligible", "target", "wording"),
        [
            ("pause", DriveStatus.ACTIVE, DriveStatus.PAUSED, "Goal paused"),
            ("resume", DriveStatus.PAUSED, DriveStatus.ACTIVE, "Goal resumed"),
            ("cancel", DriveStatus.PAUSED, DriveStatus.CANCELLED, "Goal cancelled"),
        ],
    )
    async def test_implicit_transition_selects_eligible_live_goal(
        self, command, eligible, target, wording
    ):
        svc = _FakeService(
            views=[
                _fake_view(drive_id="eligible", status=eligible, created_at=1),
                _fake_view(
                    drive_id="terminal", status=DriveStatus.COMPLETED, created_at=2
                ),
            ]
        )
        res = await GoalCommand()._execute(
            command,
            _ctx(service=svc, creature_id="worker", principal="user:alice"),
        )
        assert res.success, res.error
        assert svc.transition_call.target is target
        assert res.output.startswith(f"{wording}: eligible [status: {target.value}]")
        assert "canceld" not in res.output

    async def test_implicit_complete_selects_active_not_paused_or_terminal(self):
        svc = _FakeService(
            views=[
                _fake_view(drive_id="active", status=DriveStatus.ACTIVE, created_at=1),
                _fake_view(drive_id="paused", status=DriveStatus.PAUSED, created_at=2),
                _fake_view(drive_id="done", status=DriveStatus.COMPLETED, created_at=3),
            ]
        )
        res = await GoalCommand()._execute(
            "complete",
            _ctx(service=svc, creature_id="worker", principal="user:alice"),
        )
        assert res.success, res.error
        assert "Goal completed: active [status: completed]" in res.output

    @pytest.mark.parametrize(
        ("command", "status"),
        [
            ("pause", DriveStatus.PAUSED),
            ("resume", DriveStatus.CANCELLED),
            ("cancel", DriveStatus.CANCELLED),
            ("complete", DriveStatus.PAUSED),
        ],
    )
    async def test_explicit_transition_rejects_ineligible_status(self, command, status):
        svc = _FakeService(views=[_fake_view(drive_id="d1", status=status)])
        res = await GoalCommand()._execute(
            f"{command} d1",
            _ctx(service=svc, creature_id="worker", principal="user:alice"),
        )
        assert res.error and f"cannot {command}" in res.error
        assert f"status is {status.value}" in res.error
        assert svc.transition_call is None
        assert svc.propose_call is None

    async def test_bad_subcommand_shows_accurate_usage(self):
        cmd = GoalCommand()
        res = await cmd._execute("frobnicate", _ctx())
        assert res.error and res.error.startswith("usage:")
        assert "show [id]" in res.error
        assert "list shows live goals" in res.error
        assert "show <id> can display terminal history" in res.error
        assert "actions without an id select an eligible live goal" in res.error


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
