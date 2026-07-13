"""Unit tests for :mod:`kohakuterrarium.terrarium.drive.goal` (design §11).

Covers the GoalSpec validation + budgets and the GoalDriveRegistration policy
(schema / readiness incl. re_arm continuation / projection / per-Drive terminal
verification). The registration is a builtin beside ``generic``; no package
install is involved.
"""

from types import SimpleNamespace

import pytest

from kohakuterrarium.terrarium.drive import goal as goal_mod
from kohakuterrarium.terrarium.drive.errors import DriveValidationError
from kohakuterrarium.terrarium.drive.goal import GoalDriveRegistration
from kohakuterrarium.terrarium.drive.models import ActorRef

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
