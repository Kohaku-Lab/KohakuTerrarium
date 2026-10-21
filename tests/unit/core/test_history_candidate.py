"""Unit tests for :mod:`kohakuterrarium.core.history_candidate`."""

from types import SimpleNamespace

import pytest

from kohakuterrarium.core.conversation import Conversation, ConversationConfig
from kohakuterrarium.core.conversation_elide import estimate_messages_tokens
from kohakuterrarium.core.history_candidate import (
    HistoryCandidate,
    adopt_candidate_conversation,
    detached_conversation,
    final_prompt_messages,
    install_candidate_state,
    require_history_candidate,
    without_retention,
    without_retention_config,
)
from kohakuterrarium.errors import ConflictError
from kohakuterrarium.llm.message import create_message

# ── harness ─────────────────────────────────────────────────────


def _conversation(*messages):
    conversation = Conversation(ConversationConfig())
    for role, content in messages:
        conversation.append(role, content)
    return conversation


def _agent(conversation=None, *, compact_manager=None):
    return SimpleNamespace(
        controller=SimpleNamespace(
            conversation=conversation or _conversation(("system", "sys"))
        ),
        compact_manager=compact_manager,
    )


def _compact_manager(max_tokens, threshold=1.0, count=3, last=7.5):
    return SimpleNamespace(
        config=SimpleNamespace(max_tokens=max_tokens, threshold=threshold),
        _compact_count=count,
        _last_compact_time=last,
    )


# ── admission: retention bound ──────────────────────────────────


def test_retention_overflow_rejects_a_prefix_that_would_be_trimmed():
    agent = _agent(
        _conversation(("system", "sys"), ("user", "u1"), ("assistant", "a1"))
    )
    agent.controller.conversation.config.max_messages = 3
    messages = [
        create_message("system", "sys"),
        create_message("user", "u1"),
        create_message("assistant", "a1"),
        create_message("user", "u2"),
    ]
    with pytest.raises(ConflictError) as excinfo:
        require_history_candidate(agent, messages)
    message = str(excinfo.value)
    assert "retention limit" in message
    assert "3 non-system messages, budget 2 of limit 3" in message
    assert "original branch was not changed" in message


def test_retention_allows_exactly_the_budget():
    agent = _agent()
    agent.controller.conversation.config.max_messages = 2
    require_history_candidate(
        agent, [create_message("system", "sys"), create_message("user", "u1")]
    )


def test_retention_counts_system_messages_when_keep_system_is_off():
    agent = _agent()
    agent.controller.conversation.config.max_messages = 2
    agent.controller.conversation.config.keep_system = False
    messages = [
        create_message("system", "sys"),
        create_message("user", "u1"),
        create_message("user", "u2"),
    ]
    with pytest.raises(ConflictError, match="3 non-system messages, budget 2"):
        require_history_candidate(agent, messages)


def test_retention_ignores_messages_without_system_role():
    agent = _agent()
    agent.controller.conversation.config.max_messages = 2
    messages = [
        {"role": "system", "content": "dict prompt"},
        {"role": "user", "content": "dict user"},
        create_message("user", "u1"),
    ]
    with pytest.raises(ConflictError, match="2 non-system messages, budget 1"):
        require_history_candidate(agent, messages)


@pytest.mark.parametrize(
    "limit", [0, -1, True, False, 2.0, "3", None], ids=lambda v: repr(v)
)
def test_retention_bound_is_ignored_for_non_positive_or_non_int_limits(limit):
    agent = _agent()
    agent.controller.conversation.config.max_messages = limit
    huge = [create_message("user", "x" * 10) for _ in range(20)]
    require_history_candidate(agent, huge)


def test_retention_bound_counts_anonymous_messages_as_non_system():
    # A message object with no ``role`` attribute never counts as system.
    agent = _agent()
    agent.controller.conversation.config.max_messages = 2
    messages = [SimpleNamespace(content="anonymous") for _ in range(3)]
    with pytest.raises(ConflictError, match="3 non-system messages, budget 2"):
        require_history_candidate(agent, messages)


# ── admission: token bound ──────────────────────────────────────


def test_token_bound_rejects_a_prefix_at_the_configured_limit():
    agent = _agent(compact_manager=_compact_manager(max_tokens=100))
    messages = [create_message("user", "x" * 350)]
    with pytest.raises(ConflictError) as excinfo:
        require_history_candidate(agent, messages)
    message = str(excinfo.value)
    assert "~100 text tokens, limit 100" in message
    assert "1 messages" in message
    assert "no history was dropped" in message


def test_token_bound_admits_a_prefix_just_below_the_limit():
    agent = _agent(compact_manager=_compact_manager(max_tokens=100))
    require_history_candidate(agent, [create_message("user", "x" * 349)])


@pytest.mark.parametrize(
    "threshold",
    [0.5, 1.0, 4.0, None, "0.5", object(), -5],
    ids=["low", "unity", "high", "none", "str", "object", "negative"],
)
def test_token_bound_is_the_context_budget_not_the_compact_trigger(threshold):
    # The admission bound is max_tokens alone: no trigger value moves it.
    agent = _agent(
        compact_manager=_compact_manager(max_tokens=100, threshold=threshold)
    )
    require_history_candidate(agent, [create_message("user", "x" * 300)])
    with pytest.raises(ConflictError, match="limit 100;"):
        require_history_candidate(agent, [create_message("user", "x" * 5000)])


@pytest.mark.parametrize(
    "maximum", [0, -10, True, False, "100", None], ids=lambda v: repr(v)
)
def test_token_bound_is_ignored_for_unset_or_non_numeric_maximum(maximum):
    agent = _agent(compact_manager=_compact_manager(max_tokens=maximum))
    require_history_candidate(agent, [create_message("user", "x" * 50_000)])


def test_token_bound_is_ignored_without_a_compact_manager():
    agent = SimpleNamespace(
        controller=SimpleNamespace(conversation=_conversation(("user", "short")))
    )
    require_history_candidate(agent, [create_message("user", "x" * 50_000)])


def test_token_bound_is_ignored_when_the_manager_has_no_config():
    agent = _agent(compact_manager=SimpleNamespace(config=None))
    require_history_candidate(agent, [create_message("user", "x" * 50_000)])


def test_retention_is_checked_before_the_token_bound():
    agent = _agent(compact_manager=_compact_manager(max_tokens=10))
    agent.controller.conversation.config.max_messages = 1
    with pytest.raises(ConflictError, match="retention limit"):
        require_history_candidate(
            agent,
            [create_message("user", "short"), create_message("user", "other")],
        )


# ── final replacement prompt ────────────────────────────────────


def test_final_prompt_messages_replaces_only_the_target_with_the_new_input():
    # Only the clone is mutated; the candidate keeps its original messages.
    messages = [create_message("user", "one"), create_message("user", "two")]
    final_prompt_messages(messages, 0, "changed")
    assert [message.content for message in messages] == ["one", "two"]

    old = create_message("user", "X" * 8000)
    messages = [create_message("system", "sys"), old, create_message("user", "tail")]
    final = final_prompt_messages(messages, 1, "tiny edit")
    # The rerun regenerates everything after the target, so only the surviving
    # prefix plus the new input is measured.
    assert [message.content for message in final] == ["sys", "tiny edit"]
    assert final[1].role == "user"
    assert final is not messages
    assert messages[1] is old
    assert messages[1].content == "X" * 8000
    assert estimate_messages_tokens(final) < estimate_messages_tokens(messages)


# ── config / conversation clones ────────────────────────────────


def test_without_retention_config_drops_the_message_limit():
    config = without_retention_config(
        ConversationConfig(
            max_messages=4, keep_system=False, sanitize_orphan_tool_calls=False
        )
    )
    assert config == ConversationConfig(
        max_messages=0, keep_system=False, sanitize_orphan_tool_calls=False
    )


def test_without_retention_keeps_every_message_of_a_truncating_conversation():
    # The source holds more messages than its live limit would now allow (the
    # limit can be tightened after the messages were appended); the copy must
    # keep all of them instead of re-applying the limit.
    current = Conversation(ConversationConfig(max_messages=2))
    current._messages.extend(
        [
            create_message("system", "sys"),
            create_message("user", "u1"),
            create_message("assistant", "a1"),
            create_message("user", "u2"),
        ]
    )
    relaxed = without_retention(current)
    assert [message.content for message in relaxed.get_messages()] == [
        "sys",
        "u1",
        "a1",
        "u2",
    ]
    assert relaxed.config.max_messages == 0
    assert relaxed is not current


def test_without_retention_copies_metadata_and_leaves_the_source_alone():
    current = _conversation(("system", "sys"), ("user", "u1"))
    before = current.get_messages()
    relaxed = without_retention(current)
    assert relaxed._metadata.created_at == current._metadata.created_at
    assert relaxed._metadata.updated_at == current._metadata.updated_at
    assert relaxed._metadata.message_count == len(relaxed.get_messages())
    assert relaxed._metadata.total_chars == relaxed.get_context_length()
    assert current.get_messages() == before


def test_detached_conversation_installs_messages_without_trimming():
    current = Conversation(ConversationConfig(max_messages=2))
    current.append("system", "sys")
    messages = [create_message("user", f"u{index}") for index in range(5)]
    detached = detached_conversation(current, messages)
    assert detached.get_messages() == messages
    assert detached.config.max_messages == 0
    assert detached._metadata.created_at == current._metadata.created_at
    assert detached is not current


def test_detached_conversation_does_not_alias_the_source_messages():
    current = _conversation(("user", "current"))
    detached = detached_conversation(current, [create_message("user", "u1")])
    detached.append("assistant", "a1")
    assert [message.content for message in current.get_messages()] == ["current"]


def test_adopt_candidate_conversation_keeps_live_identity():
    current = _conversation(("user", "stale"))
    identity = id(current)
    candidate = detached_conversation(current, [create_message("user", "fresh")])
    adopt_candidate_conversation(
        SimpleNamespace(controller=SimpleNamespace(conversation=current)), candidate
    )
    assert id(current) == identity
    assert [message.content for message in current.get_messages()] == ["fresh"]


# ── state installation ──────────────────────────────────────────


def test_install_candidate_state_applies_branch_compact_and_cutoff():
    manager = _compact_manager(max_tokens=100, count=0, last=0.0)
    agent = _agent(compact_manager=manager)
    install_candidate_state(
        agent,
        HistoryCandidate(
            messages=[create_message("user", "u1")],
            turn_index=4,
            branch_id=2,
            parent_branch_path=[[1, 1], [3, 2]],
            compact_state={"compact_count": 5, "last_compact_time": 12.5},
            context_cutoff_event_id=17,
        ),
    )
    assert agent._turn_index == 4
    assert agent._branch_id == 2
    assert agent._parent_branch_path == [(1, 1), (3, 2)]
    assert agent._context_cutoff_event_id == 17
    assert manager._compact_count == 5
    assert manager._last_compact_time == 12.5


def test_install_candidate_state_skips_counters_without_a_compact_state():
    manager = _compact_manager(max_tokens=100, count=3, last=7.5)
    agent = _agent(compact_manager=manager)
    install_candidate_state(
        agent,
        HistoryCandidate(messages=[], turn_index=1, branch_id=1),
    )
    assert manager._compact_count == 3
    assert manager._last_compact_time == 7.5
    assert agent._context_cutoff_event_id == 0
    assert agent._parent_branch_path == []


def test_install_candidate_state_tolerates_a_managerless_agent():
    agent = SimpleNamespace(
        controller=SimpleNamespace(conversation=_conversation(("user", "u1")))
    )
    install_candidate_state(
        agent,
        HistoryCandidate(
            messages=[],
            turn_index=2,
            branch_id=3,
            compact_state={"compact_count": 9, "last_compact_time": 1.0},
        ),
    )
    assert (agent._turn_index, agent._branch_id) == (2, 3)


def test_history_candidate_defaults_are_neutral():
    candidate = HistoryCandidate(messages=[])
    assert candidate.turn_index == 0
    assert candidate.branch_id == 0
    assert candidate.parent_branch_path == []
    assert candidate.compact_state is None
    assert candidate.context_cutoff_event_id == 0
    assert candidate.target_index is None
    assert candidate.messages == []
