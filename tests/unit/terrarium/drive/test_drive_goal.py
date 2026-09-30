"""Unit tests for :mod:`kohakuterrarium.terrarium.drive.goal` (design §11).

Covers the GoalSpec validation + budgets and the GoalDriveRegistration policy
(schema / readiness incl. re_arm continuation / projection / per-Drive terminal
verification). The registration is a builtin beside ``generic``; no package
install is involved.
"""

from types import SimpleNamespace

import pytest

from kohakuterrarium.terrarium.drive import goal as goal_mod
from kohakuterrarium.terrarium.drive.errors import (
    DriveConflictError,
    DriveTransitionError,
    DriveValidationError,
)
from kohakuterrarium.terrarium.drive.goal import GoalDriveRegistration
from kohakuterrarium.terrarium.drive.models import ActorRef, DriveStatus
from kohakuterrarium.terrarium.drive.registration import GenericDriveRegistration

from tests.unit.terrarium.drive._harness import (
    ADMIN,
    USER,
    WORKER,
    build_manager,
    creature_request,
    make_snapshot,
)

# ── GoalSpec ────────────────────────────────────────────────────────


class TestGoalSpec:
    def test_defaults_are_conservative(self):
        spec = goal_mod.normalize_goal_spec({"objective": "fix the bug"})
        assert spec["objective"] == "fix the bug"
        assert spec["autonomy"] == "manual"
        assert spec["completion_policy"] == "self_propose"
        assert spec["budgets"] == {
            "max_turns": None,
            "max_tool_calls": None,
            "max_walltime_s": None,
        }

    def test_empty_objective_rejected(self):
        with pytest.raises(goal_mod.GoalSpecError):
            goal_mod.normalize_goal_spec({"objective": "   "})

    def test_bad_autonomy_and_policy_rejected(self):
        with pytest.raises(goal_mod.GoalSpecError):
            goal_mod.normalize_goal_spec({"objective": "x", "autonomy": "loop"})
        with pytest.raises(goal_mod.GoalSpecError):
            goal_mod.normalize_goal_spec(
                {"objective": "x", "completion_policy": "vibes"}
            )

    def test_bare_string_criteria_rejected(self):
        # A bare string where a list is expected is a common mistake.
        with pytest.raises(goal_mod.GoalSpecError):
            goal_mod.normalize_goal_spec(
                {"objective": "x", "success_criteria": "tests pass"}
            )

    def test_bad_budget_value_rejected(self):
        with pytest.raises(goal_mod.GoalSpecError):
            goal_mod.normalize_goal_spec(
                {"objective": "x", "budgets": {"max_turns": 0}}
            )
        with pytest.raises(goal_mod.GoalSpecError):
            goal_mod.normalize_goal_spec({"objective": "x", "budgets": {"unknown": 3}})

    def test_build_goal_spec_normalizes_kwargs(self):
        spec = goal_mod.build_goal_spec(
            "  ship it  ",
            success_criteria=["a", "b"],
            completion_policy="user_confirm",
            autonomy="continue_when_ready",
            budgets={"max_turns": 3},
        )
        assert spec["objective"] == "ship it"
        assert spec["success_criteria"] == ["a", "b"]
        assert spec["completion_policy"] == "user_confirm"
        assert spec["autonomy"] == "continue_when_ready"
        assert spec["budgets"]["max_turns"] == 3

    def test_budget_block_reason(self):
        br = goal_mod.budget_block_reason
        assert br({"budgets": {"max_turns": 2}}, turns_used=2) is not None
        assert br({"budgets": {"max_turns": 2}}, turns_used=1) is None
        assert br({"budgets": {"max_tool_calls": 5}}, tool_calls_used=5) is not None
        assert br({"budgets": {"max_walltime_s": 10}}, walltime_s=11) is not None
        assert br({"budgets": {}}, turns_used=99) is None


# ── GoalDriveRegistration ───────────────────────────────────────────


def _record(**spec):
    return SimpleNamespace(drive_id="goal-test01", spec=spec)


class TestGoalRegistration:
    def test_descriptor_shape(self):
        d = GoalDriveRegistration().descriptor()
        assert d.name == "goal" and d.kind == "goal"
        # Per-Drive completion policy needs the extension verifier hook.
        assert d.verifier_mode == "extension"
        assert "readiness" in d.required_roles
        assert d.prompt_contribution

    def test_validate_spec_raises_drive_validation_error(self):
        reg = GoalDriveRegistration()
        with pytest.raises(DriveValidationError):
            reg.validate_spec({"objective": ""})
        # A valid spec passes silently.
        reg.validate_spec({"objective": "do it"})

    def test_readiness_honors_autonomy(self):
        reg = GoalDriveRegistration()
        manual = reg.readiness(_record(autonomy="manual"), {}, None)
        assert manual.ready is False
        assert manual.re_arm is False
        cont = reg.readiness(_record(autonomy="continue_when_ready"), {}, None)
        assert cont.ready is True
        # continue_when_ready re-arms so the dispatcher continues after settlement.
        assert cont.re_arm is True

    def test_readiness_stops_re_arm_when_budget_exhausted(self):
        reg = GoalDriveRegistration()
        rec = _record(autonomy="continue_when_ready", budgets={"max_turns": 2})
        # within budget: re-arm
        assert reg.readiness(rec, {}, None, turns_used=1).re_arm is True
        # at/over budget: stop re-arming, never complete (design §11)
        exhausted = reg.readiness(rec, {}, None, turns_used=2)
        assert exhausted.re_arm is False
        assert exhausted.ready is False
        assert "budget" in (exhausted.reason or "")

    def test_projection_carries_bounded_objective(self):
        reg = GoalDriveRegistration()
        proj = reg.project_event(
            _record(objective="fix auth race", success_criteria=["tests green"]),
            None,
            "ready",
        )
        assert proj.event_type == "drive_ready"
        assert "Goal ID: goal-test01" in proj.prompt_override
        assert "fix auth race" in proj.prompt_override
        assert "do not call drive_status or group_drive" in proj.prompt_override
        assert proj.context["kind"] == "goal"

    def test_verify_self_propose_accepts(self):
        reg = GoalDriveRegistration()
        proposal = SimpleNamespace(
            proposed_by=ActorRef("creature", "worker"), evidence={}
        )
        ctx = {"record": _record(completion_policy="self_propose")}
        assert reg.verify_terminal(proposal, ctx).approved is True

    def test_verify_user_confirm_requires_user_actor(self):
        reg = GoalDriveRegistration()
        ctx = {"record": _record(completion_policy="user_confirm")}
        by_creature = SimpleNamespace(
            proposed_by=ActorRef("creature", "worker"), evidence={}
        )
        assert reg.verify_terminal(by_creature, ctx).approved is False
        by_user = SimpleNamespace(proposed_by=ActorRef("user", "alice"), evidence={})
        assert reg.verify_terminal(by_user, ctx).approved is True

    def test_verify_verifier_policy_requires_evidence(self):
        reg = GoalDriveRegistration()
        ctx = {"record": _record(completion_policy="verifier")}
        no_ev = SimpleNamespace(proposed_by=ActorRef("user", "a"), evidence={})
        assert reg.verify_terminal(no_ev, ctx).approved is False
        with_ev = SimpleNamespace(
            proposed_by=ActorRef("user", "a"), evidence={"stable": True}
        )
        assert reg.verify_terminal(with_ev, ctx).approved is True


class TestPausedGoalCompletion:
    @pytest.mark.parametrize(
        ("policy", "proposer", "evidence"),
        [
            ("self_propose", WORKER, {}),
            ("self_propose", USER, {}),
            ("user_confirm", USER, {}),
            ("verifier", WORKER, {"checks": "passed"}),
        ],
    )
    async def test_completion_finalizes_without_reactivating_or_delivering(
        self, policy, proposer, evidence
    ):
        h = build_manager(snapshot=make_snapshot(GoalDriveRegistration()))
        record = await h.manager.create_drive(
            creature_request(
                kind="goal",
                spec={
                    "objective": "finish",
                    "completion_policy": policy,
                    "autonomy": "continue_when_ready",
                },
            ),
            actor=WORKER,
            graph_id="g1",
        )
        paused = await h.manager.transition(
            record.drive_id,
            DriveStatus.PAUSED,
            expected_revision=record.revision,
            actor=WORKER,
            status_reason="user_interrupted",
        )
        await h.manager._scan_ready()
        await h.manager.dispatcher.dispatch_once()
        await h.manager.dispatcher.drain()
        assert await h.manager.get_drive(record.drive_id) == paused
        before = await h.manager.list_deliveries(record.drive_id)
        with pytest.raises(DriveConflictError):
            await h.manager.propose_transition(
                record.drive_id,
                DriveStatus.COMPLETED,
                actor=proposer,
                is_privileged=True,
                evidence=evidence,
                expected_revision=record.revision,
            )
        completed = await h.manager.propose_transition(
            record.drive_id,
            DriveStatus.COMPLETED,
            actor=proposer,
            is_privileged=proposer == USER,
            evidence=evidence,
            expected_revision=paused.revision,
        )
        assert completed.status is DriveStatus.COMPLETED
        assert completed.revision == paused.revision + 1
        assert completed.lifecycle_epoch == paused.lifecycle_epoch
        await h.manager._scan_ready()
        await h.manager.dispatcher.dispatch_once()
        await h.manager.dispatcher.drain()
        assert await h.manager.list_deliveries(record.drive_id) == before
        assert h.sink.delivered == []

    @pytest.mark.parametrize(
        ("policy", "proposer", "evidence"),
        [
            ("user_confirm", WORKER, {"done": True}),
            ("user_confirm", ADMIN, {"done": True}),
            ("verifier", USER, {}),
        ],
    )
    async def test_completion_policy_rejection_leaves_goal_paused(
        self, policy, proposer, evidence
    ):
        h = build_manager(snapshot=make_snapshot(GoalDriveRegistration()))
        record = await h.manager.create_drive(
            creature_request(
                kind="goal", spec={"objective": "finish", "completion_policy": policy}
            ),
            actor=WORKER,
            graph_id="g1",
        )
        paused = await h.manager.transition(
            record.drive_id,
            DriveStatus.PAUSED,
            expected_revision=record.revision,
            actor=WORKER,
        )
        before = await h.manager.list_deliveries(record.drive_id)
        with pytest.raises(DriveTransitionError, match="verifier rejected"):
            await h.manager.propose_transition(
                record.drive_id,
                DriveStatus.COMPLETED,
                actor=proposer,
                is_privileged=True,
                evidence=evidence,
                expected_revision=paused.revision,
            )
        assert await h.manager.get_drive(record.drive_id) == paused
        assert await h.manager.list_deliveries(record.drive_id) == before
        assert h.sink.delivered == []

    @pytest.mark.parametrize(
        ("kind", "target"),
        [("generic", DriveStatus.COMPLETED), ("goal", DriveStatus.FAILED)],
    )
    async def test_other_paused_terminal_edges_remain_forbidden(self, kind, target):
        h = build_manager(
            snapshot=make_snapshot(GoalDriveRegistration(), GenericDriveRegistration())
        )
        record = await h.manager.create_drive(
            creature_request(kind=kind, spec={"objective": "finish"}),
            actor=WORKER,
            graph_id="g1",
        )
        paused = await h.manager.transition(
            record.drive_id,
            DriveStatus.PAUSED,
            expected_revision=record.revision,
            actor=WORKER,
        )
        with pytest.raises(DriveTransitionError, match="not permitted"):
            await h.manager.propose_transition(
                record.drive_id,
                target,
                actor=WORKER,
                expected_revision=paused.revision,
            )
        assert await h.manager.get_drive(record.drive_id) == paused
