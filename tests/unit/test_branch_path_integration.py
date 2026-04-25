"""End-to-end pin: regen → continue → branch-switch round-trip.

Exercises the live agent message flow (``AgentMessagesMixin`` +
``_process_event`` user-input handling) with parent_branch_path
tracking so a branch switch correctly hides follow-ups made under
a different subtree.

Uses the same ``_FakeAgent`` scaffold as
``test_regen_no_duplicate_user`` plus a thin shim that runs the
``user_input`` bookkeeping the way ``AgentHandlersMixin._process_event``
does in production. That keeps the test isolated from the LLM
machinery while still exercising the parent-path stamping path.
"""

import pytest

from kohakuterrarium.core.agent_messages import AgentMessagesMixin
from kohakuterrarium.core.conversation import Conversation
from kohakuterrarium.session.history import (
    collect_branch_metadata,
    replay_conversation,
)
from kohakuterrarium.session.store import SessionStore


class _FakeController:
    def __init__(self, conv: Conversation):
        self.conversation = conv


class _FakeConfig:
    name = "alice"


class _FakeAgent(AgentMessagesMixin):
    """Agent surface needed for regen / edit+rerun + parent-path stamping."""

    def __init__(self, store: SessionStore):
        self.config = _FakeConfig()
        self.session_store = store
        self.controller = _FakeController(Conversation())
        self._turn_index = 0
        self._branch_id = 0
        self._parent_branch_path: list[tuple[int, int]] = []

    async def _rerun_from_last(self, new_user_content: str = "") -> None:
        # Production trigger pipeline is replaced with a no-op here;
        # the test invokes ``_apply_user_input`` explicitly to simulate
        # the controller's user_input handling.
        return None

    def _apply_user_input(self, content: str) -> None:
        """Mirror of ``AgentHandlersMixin._process_event`` for new
        user input (not rerun) — bumps turn, resets branch, stamps
        parent_branch_path on the persisted event."""
        if self._turn_index > 0 and self._branch_id > 0:
            self._parent_branch_path = list(self._parent_branch_path)
            self._parent_branch_path.append((self._turn_index, self._branch_id))
        self._turn_index += 1
        self._branch_id = 1
        ppath = [tuple(p) for p in self._parent_branch_path]
        self.session_store.append_event(
            self.config.name,
            "user_input",
            {"content": content},
            turn_index=self._turn_index,
            branch_id=self._branch_id,
            parent_branch_path=ppath,
        )
        self.session_store.append_event(
            self.config.name,
            "user_message",
            {"content": content},
            turn_index=self._turn_index,
            branch_id=self._branch_id,
            parent_branch_path=ppath,
        )
        self.controller.conversation.append("user", content)

    def _emit_assistant(self, text: str) -> None:
        """Pretend the LLM produced one assistant text chunk on the
        current turn/branch."""
        self.session_store.append_event(
            self.config.name,
            "text_chunk",
            {"content": text, "chunk_seq": 0},
            turn_index=self._turn_index,
            branch_id=self._branch_id,
            parent_branch_path=[tuple(p) for p in self._parent_branch_path],
        )
        self.session_store.append_event(
            self.config.name,
            "processing_end",
            {},
            turn_index=self._turn_index,
            branch_id=self._branch_id,
            parent_branch_path=[tuple(p) for p in self._parent_branch_path],
        )
        self.controller.conversation.append("assistant", text)


def _new_agent(tmp_path) -> tuple[_FakeAgent, SessionStore]:
    path = tmp_path / "session.kohakutr.v2"
    store = SessionStore(str(path))
    store.init_meta(
        session_id="s",
        config_type="agent",
        config_path="x",
        pwd=str(tmp_path),
        agents=["alice"],
    )
    return _FakeAgent(store), store


@pytest.mark.asyncio
async def test_regen_then_continue_records_correct_parent_path(tmp_path):
    """After regen + continue, turn 2's events must carry
    parent_branch_path = [(1, 2)] so a future switch back to branch 1
    of turn 1 hides them."""
    agent, store = _new_agent(tmp_path)

    # Turn 1 branch 1.
    agent._apply_user_input("hi")
    agent._emit_assistant("1a")

    # Regen → turn 1 branch 2.
    await agent.regenerate_last_response()
    agent._emit_assistant("2a")

    # Continue to turn 2.
    agent._apply_user_input("next")
    agent._emit_assistant("next-a")

    events = store.get_events("alice")
    turn2_events = [e for e in events if e.get("turn_index") == 2]
    assert turn2_events
    for evt in turn2_events:
        assert evt.get("parent_branch_path") == [[1, 2]]

    # Default replay (latest everywhere) shows the live subtree.
    replayed = replay_conversation(events)
    contents = [m["content"] for m in replayed]
    assert "2a" in contents
    assert "next-a" in contents
    assert "1a" not in contents

    # Switching turn 1 → branch 1 hides the follow-up entirely.
    replayed = replay_conversation(events, branch_view={1: 1})
    contents = [m["content"] for m in replayed]
    assert "1a" in contents
    assert "next-a" not in contents

    store.close(update_status=False)


@pytest.mark.asyncio
async def test_branch_metadata_filters_to_current_subtree(tmp_path):
    """Per-turn ``branches`` must not list branches whose parent path
    is incompatible with the user's current selections of prior turns."""
    agent, store = _new_agent(tmp_path)

    agent._apply_user_input("hi")
    agent._emit_assistant("1a")
    await agent.regenerate_last_response()
    agent._emit_assistant("2a")
    agent._apply_user_input("next")
    agent._emit_assistant("next-a")

    events = store.get_events("alice")
    # Default view: turn 1 has both branches.
    meta = collect_branch_metadata(events)
    assert sorted(meta[1]["branches"]) == [1, 2]
    # Default view: turn 2 has its single branch under turn 1 branch 2.
    assert meta[2]["branches"] == [1]

    # Override turn 1 → branch 1: turn 2 disappears from the navigator.
    meta = collect_branch_metadata(events, branch_view={1: 1})
    assert 2 not in meta

    store.close(update_status=False)


@pytest.mark.asyncio
async def test_edit_and_rerun_truncates_parent_path(tmp_path):
    """Edit+rerun on an earlier turn drops every later-turn entry from
    the parent path. New events under the edited turn carry ONLY the
    pre-edited prefix."""
    agent, store = _new_agent(tmp_path)

    agent._apply_user_input("q1")
    agent._emit_assistant("a1")
    agent._apply_user_input("q2")
    agent._emit_assistant("a2")
    agent._apply_user_input("q3")
    agent._emit_assistant("a3")

    # Conversation now has 6 messages: u q1, a a1, u q2, a a2, u q3, a a3.
    # Edit q2 (index 2 — the second user message position).
    await agent.edit_and_rerun(2, "q2-edited")

    events = store.get_events("alice")
    # The new edit's user_message lives on turn 2 / branch 2.
    edited = [
        e
        for e in events
        if e.get("type") == "user_message"
        and e.get("turn_index") == 2
        and e.get("branch_id") == 2
    ]
    assert len(edited) == 1
    # Parent path drops the post-turn-2 entry that was on the agent
    # before the edit.
    assert edited[0].get("parent_branch_path") == [[1, 1]]

    store.close(update_status=False)
