"""Regression tests for stable mutation prefixes containing completed background calls."""

from contextlib import closing
from copy import deepcopy
from types import SimpleNamespace

import pytest

from kohakuterrarium.core.agent_message_history import apply_prepared_branch_view
from kohakuterrarium.core.agent_raw_history import prepare_persisted_edit
from kohakuterrarium.core.conversation import Conversation
from kohakuterrarium.core.settled_refresh import prepare_mutation_prefix
from kohakuterrarium.errors import ConflictError
from kohakuterrarium.session.store import SessionStore


@pytest.mark.parametrize(
    ("settled", "feedback", "scenario"),
    [
        (False, True, None),
        (True, False, None),
        (True, True, None),
        (True, False, "same-turn"),
        (True, True, "bounded"),
        (True, False, "untagged"),
        (True, False, "missing"),
        (True, False, "retention-drop"),
        (True, True, "retention-trim"),
    ],
)
def test_refresh_pending_prefix_from_saved_structured_result(
    tmp_path, settled, feedback, scenario
):
    conv = Conversation()
    conv.append("system", "plain")
    conv.append("user", "launch", metadata={"turn_index": 1, "branch_id": 1})
    call = {
        "id": "provider1",
        "type": "function",
        "function": {"name": "media", "arguments": "{}"},
    }
    conv.append("assistant", "", tool_calls=[call])
    conv.append(
        "tool",
        "pending",
        name="media",
        tool_call_id="provider1",
        metadata={"job_id": "job1", "tool_result_phase": "pending"},
    )
    if feedback:
        conv.append(
            "user",
            "completion feedback",
            metadata={
                "kind": "tool_results",
                "job_ids": ["otherjob"],
                "tool_result_phase": "final",
            },
        )
    conv.append("user", "later", metadata={"turn_index": 2, "branch_id": 1})
    parts = [
        {"type": "text", "text": "caption"},
        {"type": "image_url", "image_url": {"url": "https://media.invalid/a"}},
        {
            "type": "file",
            "file": {"content": "result", "name": "out.txt", "is_inline": True},
        },
    ]
    with closing(SessionStore(str(tmp_path / "refresh.kohakutr"))) as store:
        store.append_event(
            "agent",
            "user_message",
            {"content": "launch"},
            turn_index=1,
            branch_id=1,
            parent_branch_path=[],
        )
        store.append_event(
            "agent",
            "tool_call",
            {
                "name": "media",
                "call_id": "job1",
                "tool_call_id": "provider1",
                "args": {},
            },
            turn_index=1,
            branch_id=1,
            parent_branch_path=[],
        )
        if settled and scenario == "same-turn":
            store.append_event(
                "agent",
                "tool_result",
                {"name": "media", "call_id": "job1", "output": parts},
                turn_index=1,
                branch_id=1,
                parent_branch_path=[],
            )
        store.append_event(
            "agent",
            "user_message",
            {"content": "later"},
            turn_index=2,
            branch_id=1,
            parent_branch_path=[(1, 1)],
        )
        if settled and scenario != "same-turn":
            store.append_event(
                "agent",
                "tool_result",
                {"name": "media", "call_id": "job1", "output": parts},
                turn_index=2,
                branch_id=1,
                parent_branch_path=[(1, 1)],
            )
        if scenario == "bounded":
            conv.config.max_messages = 6
            for suffix_turn in range(3, 12):
                store.append_event(
                    "agent",
                    "user_message",
                    {"content": f"suffix {suffix_turn}"},
                    turn_index=suffix_turn,
                    branch_id=1,
                    parent_branch_path=[(turn, 1) for turn in range(1, suffix_turn)],
                )
        agent = SimpleNamespace(
            controller=SimpleNamespace(conversation=conv),
            config=SimpleNamespace(name="agent"),
            session_store=store,
            _turn_index=2,
            _branch_id=1,
            _parent_branch_path=[(1, 1)],
        )
        target_index = 5 if feedback else 4
        if scenario == "untagged":
            conv.get_messages()[target_index].metadata.clear()
        elif scenario == "missing":
            conv.get_messages()[target_index].metadata["turn_index"] = 99
        if scenario == "retention-drop":
            conv.append("system", "second system")
            conv.config.max_messages = 1
        elif scenario == "retention-trim":
            conv.config.max_messages = 4
        original = conv.to_messages(include_metadata=True)
        if scenario == "bounded":
            agent._turn_index = 11
            agent._parent_branch_path = [(turn, 1) for turn in range(1, 11)]
        if settled and scenario in {
            "untagged",
            "missing",
            "retention-drop",
            "retention-trim",
        }:
            with pytest.raises(ConflictError):
                prepare_mutation_prefix(agent, target_index)
            assert conv.to_messages(include_metadata=True) == original
            return
        before = deepcopy(conv.snapshot_messages())
        before_state = (
            agent._turn_index,
            agent._branch_id,
            agent._parent_branch_path[:],
        )
        refreshed = prepare_mutation_prefix(agent, target_index)
        assert conv.snapshot_messages() == before
        assert (
            agent._turn_index,
            agent._branch_id,
            agent._parent_branch_path,
        ) == before_state
        if settled and scenario is None:
            prepared = prepare_persisted_edit(
                agent, message_idx=target_index, replacement_content="edited"
            )
            assert conv.snapshot_messages() == before
            assert (
                agent._turn_index,
                agent._branch_id,
                agent._parent_branch_path,
            ) == before_state
            prepared.install(agent)
            index = prepared.index
        elif refreshed is not None:
            index = refreshed["target_index"]
            apply_prepared_branch_view(
                agent, refreshed["prepared"], candidate=refreshed["conversation"]
            )
        else:
            index = target_index
        assert conv.get_messages()[index].get_text_content() == "later"
        tool = next(
            message for message in conv.get_messages() if message.role == "tool"
        )
        if settled:
            assert [part.to_dict() for part in tool.content] == parts
            assert tool.tool_call_id == "provider1"
            assert tool.metadata["tool_result_phase"] == "final"
        else:
            assert tool.content == "pending"
            assert tool.metadata["tool_result_phase"] == "pending"


def test_plain_prefix_and_unpersisted_history_need_no_store_read():
    conv = Conversation()
    conv.append("user", "question")
    agent = SimpleNamespace(
        controller=SimpleNamespace(conversation=conv), session_store=None
    )
    assert prepare_mutation_prefix(agent, 0) is None
    conv.append("assistant", "answer")
    assert prepare_mutation_prefix(agent, 1) is None
    agent.session_store = object()
    assert prepare_mutation_prefix(agent, 0) is None
