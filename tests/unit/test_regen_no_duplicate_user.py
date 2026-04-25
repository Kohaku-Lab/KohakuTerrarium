"""Pin: regen / edit+rerun never produces duplicate user messages.

The previous implementation called ``conv.append("user", new_content)``
in ``edit_and_rerun`` AND fired a USER_INPUT trigger; the controller's
``_build_turn_context`` then appended ANOTHER user message to the
conversation. The LLM saw the same edit content twice and edit+regen
appeared to "do nothing".

These tests exercise the public Agent surface without a real LLM by
patching the controller's run loop. They pin two contracts:

1. After ``regenerate_last_response``, the in-memory conversation
   has at most one user message at each position.
2. After ``edit_and_rerun``, the in-memory conversation has the
   edited content at the right position, NOT duplicated.
"""

import pytest

from kohakuterrarium.core.agent_messages import AgentMessagesMixin
from kohakuterrarium.core.conversation import Conversation
from kohakuterrarium.session.store import SessionStore


class _FakeController:
    def __init__(self, conv: Conversation):
        self.conversation = conv


class _FakeConfig:
    name = "alice"


class _FakeAgent(AgentMessagesMixin):
    """Minimal Agent surface needed for regen / edit+rerun helpers.

    Real ``_rerun_from_last`` would push a TriggerEvent through the
    event handlers and the controller. We override it to assert what
    SHOULD happen at the controller boundary instead — confirming the
    helper hands off cleanly without polluting the conversation first.
    """

    def __init__(self, store: SessionStore):
        self.config = _FakeConfig()
        self.session_store = store
        self.controller = _FakeController(Conversation())
        self._turn_index = 0
        self._branch_id = 0
        self._captured_rerun: list[tuple[str, dict]] = []

    async def _rerun_from_last(self, new_user_content: str = "") -> None:
        # Capture the call instead of pushing through the trigger
        # pipeline. Tests assert this stays a single hand-off.
        self._captured_rerun.append(
            (
                new_user_content,
                {"turn_index": self._turn_index, "branch_id": self._branch_id},
            )
        )


def _seed_first_user_turn(agent: _FakeAgent, content: str = "hi") -> None:
    """Simulate one completed user→assistant turn.

    Adds the in-memory conversation entries AND the persisted events
    so regen / edit can resolve the right turn_index and branch_id.
    """
    agent._turn_index = 1
    agent._branch_id = 1
    agent.controller.conversation.append("user", content)
    agent.controller.conversation.append("assistant", "first reply")
    agent.session_store.append_event(
        agent.config.name,
        "user_input",
        {"content": content},
        turn_index=1,
        branch_id=1,
    )
    agent.session_store.append_event(
        agent.config.name,
        "user_message",
        {"content": content},
        turn_index=1,
        branch_id=1,
    )
    agent.session_store.append_event(
        agent.config.name,
        "text_chunk",
        {"content": "first reply", "chunk_seq": 0},
        turn_index=1,
        branch_id=1,
    )
    agent.session_store.append_event(
        agent.config.name, "processing_end", {}, turn_index=1, branch_id=1
    )


@pytest.mark.asyncio
async def test_regenerate_does_not_duplicate_user_in_conversation(tmp_path):
    """Regen truncates the assistant reply but leaves the user
    message untouched. ``_rerun_from_last`` is called with empty
    content (the controller does NOT re-append on rerun)."""
    path = tmp_path / "session.kohakutr.v2"
    store = SessionStore(str(path))
    store.init_meta(
        session_id="s1",
        config_type="agent",
        config_path="x",
        pwd=str(tmp_path),
        agents=["alice"],
    )
    agent = _FakeAgent(store)
    _seed_first_user_turn(agent, "hi")

    await agent.regenerate_last_response()

    msgs = agent.controller.conversation.get_messages()
    user_msgs = [m for m in msgs if m.role == "user"]
    assert len(user_msgs) == 1
    assert user_msgs[0].content == "hi"

    # The rerun trigger carries no new content (pure regen).
    assert len(agent._captured_rerun) == 1
    new_content, state = agent._captured_rerun[0]
    assert new_content == ""
    assert state["turn_index"] == 1
    assert state["branch_id"] == 2

    # Event log has fresh user_input + user_message for branch 2 with
    # the SAME content as branch 1 (mirrored by regen).
    events = store.get_events("alice")
    branch2_user = [
        e for e in events if e.get("type") == "user_message" and e.get("branch_id") == 2
    ]
    assert len(branch2_user) == 1
    assert branch2_user[0]["content"] == "hi"

    store.close(update_status=False)


@pytest.mark.asyncio
async def test_edit_and_rerun_does_not_duplicate_user_in_conversation(tmp_path):
    """edit_and_rerun truncates from the edited message onward and
    does NOT append the new user message in-memory. The controller's
    USER_INPUT trigger handler is what appends it — preventing the
    duplicate-user bug that previously made edit+regen do nothing."""
    path = tmp_path / "session.kohakutr.v2"
    store = SessionStore(str(path))
    store.init_meta(
        session_id="s1",
        config_type="agent",
        config_path="x",
        pwd=str(tmp_path),
        agents=["alice"],
    )
    agent = _FakeAgent(store)
    _seed_first_user_turn(agent, "hi")

    await agent.edit_and_rerun(0, "actually, hello")

    msgs = agent.controller.conversation.get_messages()
    # The conversation has been TRUNCATED to before the edited user
    # message — it's empty. The controller will append the new user
    # message when the rerun trigger flows through ``push_event``.
    assert msgs == []

    # The rerun trigger carries the new content (edit case).
    assert len(agent._captured_rerun) == 1
    new_content, state = agent._captured_rerun[0]
    assert new_content == "actually, hello"
    assert state["turn_index"] == 1
    assert state["branch_id"] == 2

    # Event log has new user_input + user_message for branch 2.
    events = store.get_events("alice")
    branch2_user = [
        e for e in events if e.get("type") == "user_message" and e.get("branch_id") == 2
    ]
    assert len(branch2_user) == 1
    assert branch2_user[0]["content"] == "actually, hello"

    store.close(update_status=False)


@pytest.mark.asyncio
async def test_branch_id_increments_on_repeated_regen(tmp_path):
    """Each regen of the same turn opens a new branch_id."""
    path = tmp_path / "session.kohakutr.v2"
    store = SessionStore(str(path))
    store.init_meta(
        session_id="s1",
        config_type="agent",
        config_path="x",
        pwd=str(tmp_path),
        agents=["alice"],
    )
    agent = _FakeAgent(store)
    _seed_first_user_turn(agent, "hi")

    # Regen #1 → branch 2
    await agent.regenerate_last_response()
    assert agent._branch_id == 2

    # Replay would now seed an assistant turn for branch 2; we just
    # mark it complete in the store so the next regen sees branch 2
    # as the current end-of-stream.
    store.append_event("alice", "processing_end", {}, turn_index=1, branch_id=2)

    # Re-add the user msg manually (helper truncated it) so regen has
    # something to find.
    agent.controller.conversation.append("user", "hi")

    # Regen #2 → branch 3
    await agent.regenerate_last_response()
    assert agent._branch_id == 3

    store.close(update_status=False)
