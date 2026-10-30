"""Unit tests for :mod:`kohakuterrarium.terrarium.service.dto`."""

from types import SimpleNamespace

import pytest

from kohakuterrarium.core.token_ledger import ledger_for
from kohakuterrarium.terrarium.service.dto import (
    CreatureInfo,
    completed_branch_result,
    creature_to_info,
)
from kohakuterrarium.testing.terrarium import _FakeAgent


def _creature(agent) -> SimpleNamespace:
    return SimpleNamespace(
        creature_id="cid",
        name="alice",
        graph_id="g",
        is_running=True,
        is_privileged=False,
        parent_creature_id=None,
        listen_channels=["a"],
        send_channels=[],
        agent=agent,
    )


class TestCompletedBranchResult:
    @pytest.mark.parametrize("request_id", ["expected", "other", None])
    def test_failure_is_bound_to_one_request_and_preserves_branch_selection(
        self, request_id
    ):
        agent = SimpleNamespace(
            _turn_index=3,
            _branch_id=2,
            _parent_branch_path=((1, 2), (2, 1)),
            _branch_execution_error=("expected", "missing media"),
        )
        result = completed_branch_result(agent, request_id)
        assert result["turn_index"] == 3
        assert result["branch_id"] == 2
        assert result["parent_branch_path"] == [[1, 2], [2, 1]]
        assert result["request_id"]
        assert result.get("execution_error") == (
            "missing media" if request_id == "expected" else None
        )
        result["parent_branch_path"][0][1] = 99
        assert agent._parent_branch_path == ((1, 2), (2, 1))


class TestCreatureToInfoUsage:
    def test_snapshot_carries_running_usage(self):
        agent = _FakeAgent(name="alice")
        ledger_for(agent).add({"prompt_tokens": 120, "completion_tokens": 30})
        info = creature_to_info(_creature(agent))
        assert info.token_usage == {
            "prompt": 120,
            "completion": 30,
            "cached": 0,
            "total": 150,
        }
        assert info.context_tokens == 120

    def test_snapshot_without_agent_reports_zero(self):
        info = creature_to_info(_creature(None))
        assert info.token_usage["total"] == 0
        assert info.context_tokens == 0


class TestCreatureInfoIdentity:
    def test_usage_is_not_part_of_identity(self):
        base = dict(
            creature_id="cid",
            name="n",
            graph_id="g",
            is_running=True,
            is_privileged=False,
            parent_creature_id=None,
            listen_channels=(),
            send_channels=(),
        )
        busy = CreatureInfo(**base, token_usage={"total": 5}, context_tokens=5)
        assert busy == CreatureInfo(**base)
        assert hash(busy) == hash(CreatureInfo(**base))
