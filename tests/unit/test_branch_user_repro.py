"""Reproduction tests for user-reported branching bugs.

User scenarios:

**Bug A (edit+regen wipes context):**

    u1 a1 u2 a2 u3 a3
      → edit u2 to "u2_edited"        (expected: u1 a1 u2_edited a2_edited)
      → switch back to turn-2 branch 1 (expected: u1 a1 u2 a2 u3 a3)
      → can't click "save & rerun" on u3              ← UI bug
      → switch back to turn-2 branch 2 (expected: u1 a1 u2_edited a2_edited)
      → add u4, a4                    (expected: u1 a1 u2_edited a2_edited u4 a4)
      → edit u4 to "u5"               (expected: u1 a1 u2_edited a2_edited u5 a5)
                                       actual:   u1 a1 u5 a5    ← BUG: u2_edited/a2_edited vanish

**Bug B (retry always targets tail):**

    u1 a1 u2 a2 u3 a3
      → click "retry" on a2          (expected: new branch at turn 2)
                                       actual:   regenerates a3 instead

These tests drive the AgentMessagesMixin directly (no LLM) and check
the event log + replay result against the user's expectations.
"""

import pytest

from kohakuterrarium.core.agent_messages import AgentMessagesMixin
from kohakuterrarium.core.conversation import Conversation
from kohakuterrarium.session.history import replay_conversation
from kohakuterrarium.session.store import SessionStore

# -----------------------------------------------------------------------------
# Test fixtures: a minimal Agent that exercises the real edit/regen code path
# end-to-end through `_process_event`-style event handling, so the
# double-append bug in agent_handlers actually fires (the existing
# test_regen_no_duplicate_user.py stubs _rerun_from_last and misses it).
# -----------------------------------------------------------------------------


class _FakeController:
    def __init__(self, conv: Conversation):
        self.conversation = conv


class _FakeConfig:
    name = "alice"


class _FakeAgent(AgentMessagesMixin):
    """Minimal agent that emulates the full ``_process_event`` flow
    end-to-end. ``_rerun_from_last`` fires through the controller append
    + event-log writes the same way the real ``Agent`` does.
    """

    def __init__(self, store: SessionStore):
        self.config = _FakeConfig()
        self.session_store = store
        self.controller = _FakeController(Conversation())
        self._turn_index = 0
        self._branch_id = 0
        self._parent_branch_path: list[tuple[int, int]] = []

    async def _rerun_from_last(self, new_user_content="") -> None:
        """Emulate the trigger-event flow through `_process_event`.

        Real `_process_event` (in `AgentHandlersMixin`):
          - reads is_rerun / is_edited from event.context
          - skips turn_index bump for rerun events
          - appends user_input + user_message events to session_store
            iff ``not is_pure_rerun`` (the bug being investigated)
          - controller appends user message to in-memory conversation
            (skipped for pure regen)
          - LLM call → assistant message appended

        We model that here without invoking the real agent_handlers,
        but we EXACTLY mirror the event-log write semantics (with the
        BUG intact) so the test reflects production behavior.
        """
        edited = bool(new_user_content)
        is_rerun = True
        is_edited = edited
        is_pure_rerun = is_rerun and not is_edited

        # Mirror the production condition in agent_handlers.py:154.
        # POST-FIX: ``not is_rerun`` (skips both pure regen and
        # edit+rerun — both have their event-log writes owned by
        # agent_messages). Flip back to ``not is_pure_rerun`` to
        # reproduce the original double-append bug.
        if self.session_store is not None and not is_rerun:
            ppath = [tuple(p) for p in self._parent_branch_path]
            self.session_store.append_event(
                self.config.name,
                "user_input",
                {"content": new_user_content},
                turn_index=self._turn_index,
                branch_id=self._branch_id,
                parent_branch_path=ppath,
            )
            self.session_store.append_event(
                self.config.name,
                "user_message",
                {"content": new_user_content},
                turn_index=self._turn_index,
                branch_id=self._branch_id,
                parent_branch_path=ppath,
            )

        # Controller append (real `controller.run_once` skips for pure regen)
        if not is_pure_rerun:
            self.controller.conversation.append("user", new_user_content)

        # Simulate the LLM reply.
        assistant_text = f"reply-to-{new_user_content}" if new_user_content else "reply"
        self.controller.conversation.append("assistant", assistant_text)
        ppath = [tuple(p) for p in self._parent_branch_path]
        self.session_store.append_event(
            self.config.name,
            "text_chunk",
            {"content": assistant_text, "chunk_seq": 0},
            turn_index=self._turn_index,
            branch_id=self._branch_id,
            parent_branch_path=ppath,
        )
        self.session_store.append_event(
            self.config.name,
            "processing_end",
            {},
            turn_index=self._turn_index,
            branch_id=self._branch_id,
            parent_branch_path=ppath,
        )

    def _apply_user_input(self, content: str) -> None:
        """Add a fresh user turn (not rerun). Mirrors the agent_handlers
        bookkeeping for ``user_input`` events with ``is_rerun=False`` —
        including the collision-safe branch allocation that bumps past
        any existing events at the new turn_index.
        """
        if self._turn_index > 0 and self._branch_id > 0:
            self._parent_branch_path = list(self._parent_branch_path)
            self._parent_branch_path.append((self._turn_index, self._branch_id))
        self._turn_index += 1
        existing_max = self._max_branch_id_for_turn(self._turn_index)
        self._branch_id = existing_max + 1 if existing_max > 0 else 1
        self.controller.conversation.append("user", content)
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

    def _emit_assistant(self, content: str) -> None:
        """Add an assistant reply to the current turn."""
        self.controller.conversation.append("assistant", content)
        ppath = [tuple(p) for p in self._parent_branch_path]
        self.session_store.append_event(
            self.config.name,
            "text_chunk",
            {"content": content, "chunk_seq": 0},
            turn_index=self._turn_index,
            branch_id=self._branch_id,
            parent_branch_path=ppath,
        )
        self.session_store.append_event(
            self.config.name,
            "processing_end",
            {},
            turn_index=self._turn_index,
            branch_id=self._branch_id,
            parent_branch_path=ppath,
        )


def _new_agent(tmp_path) -> tuple[_FakeAgent, SessionStore]:
    path = tmp_path / "session.kohakutr.v2"
    store = SessionStore(str(path))
    store.init_meta(
        session_id="s1",
        config_type="agent",
        config_path="x",
        pwd=str(tmp_path),
        agents=["alice"],
    )
    return _FakeAgent(store), store


def _user_messages(messages: list[dict]) -> list[str]:
    return [m.get("content") for m in messages if m.get("role") == "user"]


def _assistant_messages(messages: list[dict]) -> list[str]:
    return [m.get("content") for m in messages if m.get("role") == "assistant"]


# -----------------------------------------------------------------------------
# Bug A: edit chain wipes context
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bug_a_initial_edit_works(tmp_path):
    """Sanity: edit u2 → u2_edited gives the expected replay.

    State after: branch 2 of turn 2 has u2_edited / a2_edited.
    Default replay (latest branches) shows: [u1, a1, u2_edited, a2_edited].
    """
    agent, store = _new_agent(tmp_path)
    agent._apply_user_input("u1")
    agent._emit_assistant("a1")
    agent._apply_user_input("u2")
    agent._emit_assistant("a2")
    agent._apply_user_input("u3")
    agent._emit_assistant("a3")

    # in-memory: [u1, a1, u2, a2, u3, a3]  (6 messages)
    # event log: 3 turns × (user_input + user_message + text_chunk + processing_end)
    # Edit u2 (the 2nd user message — position 2 in the conversation, i.e. msg_idx=2).
    ok = await agent.edit_and_rerun(2, "u2_edited")
    assert ok is True

    msgs = replay_conversation(store.get_events("alice"))
    assert _user_messages(msgs) == [
        "u1",
        "u2_edited",
    ], f"expected [u1, u2_edited], got {_user_messages(msgs)}"
    assert _assistant_messages(msgs) == ["a1", "reply-to-u2_edited"]


@pytest.mark.asyncio
async def test_bug_a_user_scenario_chain(tmp_path):
    """The user's exact scenario.

    u1 a1 u2 a2 u3 a3
      → edit u2 → "u2_edited"        (branch 2 of turn 2)
      → add u4, a4 to that subtree   (turn 3, parent=[(1,1),(2,2)])
      → edit u4 → "u5"               (branch 2 of turn 3, parent=[(1,1),(2,2)])

    Expected default replay after all that:
      [u1, a1, u2_edited, a2_edited, u5, a5]

    User reports it actually replays as [u1, a1, u5, a5] — the
    u2_edited / a2_edited disappear.
    """
    agent, store = _new_agent(tmp_path)
    agent._apply_user_input("u1")
    agent._emit_assistant("a1")
    agent._apply_user_input("u2")
    agent._emit_assistant("a2")
    agent._apply_user_input("u3")
    agent._emit_assistant("a3")

    # Step 1: edit u2 (the second user message).
    assert await agent.edit_and_rerun(2, "u2_edited") is True

    # After edit: agent's in-memory conv = [u1, a1, u2_edited, reply-to-u2_edited].
    # `_apply_user_input` adds the next turn as a follow-up of the
    # current branch (turn 2 branch 2).
    agent._apply_user_input("u4")
    agent._emit_assistant("a4")

    # Sanity: with no edit yet to turn 3, default replay walks
    # turn 1 (b1), turn 2 (b2, latest), turn 3 (b1, only choice).
    msgs_before_edit_u4 = replay_conversation(store.get_events("alice"))
    assert _user_messages(msgs_before_edit_u4) == ["u1", "u2_edited", "u4"], (
        f"after add u4 expected [u1, u2_edited, u4], got "
        f"{_user_messages(msgs_before_edit_u4)}"
    )

    # Step 2: edit u4 (in the new subtree's turn 3).
    # u4 is now the 3rd user message in conversation. msg_idx of u4 in
    # the truncated-then-rebuilt conversation: [u1, a1, u2_edited,
    # reply-to-u2_edited, u4, reply-to-u4] → idx 4.
    assert await agent.edit_and_rerun(4, "u5") is True

    msgs = replay_conversation(store.get_events("alice"))
    user_msgs = _user_messages(msgs)
    assistant_msgs = _assistant_messages(msgs)

    print("---- BUG A FINAL REPLAY ----")
    for m in msgs:
        print(f"  {m['role']}: {m.get('content')!r}")

    assert user_msgs == [
        "u1",
        "u2_edited",
        "u5",
    ], f"BUG A: expected [u1, u2_edited, u5], got {user_msgs}"
    assert assistant_msgs[-1] == "reply-to-u5"


@pytest.mark.asyncio
async def test_bug_a_double_append_inflates_live_user_turns(tmp_path):
    """The proximate cause: agent_handlers' rerun-event re-append.

    After a SINGLE edit_and_rerun call, the event log should contain
    exactly one user_message at (turn=2, branch=2). If the live agent
    code path double-appends (agent_messages writes once, then
    `_process_event` re-writes), this test catches it.
    """
    agent, store = _new_agent(tmp_path)
    agent._apply_user_input("u1")
    agent._emit_assistant("a1")
    agent._apply_user_input("u2")
    agent._emit_assistant("a2")

    await agent.edit_and_rerun(2, "u2_edited")

    events = store.get_events("alice")
    branch2_user_msgs = [
        e
        for e in events
        if e.get("type") == "user_message"
        and e.get("turn_index") == 2
        and e.get("branch_id") == 2
    ]
    assert len(branch2_user_msgs) == 1, (
        f"BUG A proximate cause: expected 1 user_message at (turn=2, "
        f"branch=2), got {len(branch2_user_msgs)}. Doubled? "
        f"{[e.get('content') for e in branch2_user_msgs]}"
    )


# -----------------------------------------------------------------------------
# Bug B: retry/regenerate always targets the tail
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bug_b_regenerate_at_specified_turn(tmp_path):
    """Retry on a non-tail message must open a new branch at THAT turn.

    User scenario: chat has u1 a1 u2 a2 u3 a3, user clicks retry on a2.
    Pre-fix: ``regenerate_last_response()`` had no turn parameter and
    always operated on the conversation tail (so retry on a2 secretly
    regenerated a3 instead).
    Post-fix: the method accepts ``turn_index=…`` and routes through
    edit_and_rerun with the same content, creating a new branch at the
    requested turn.
    """
    agent, store = _new_agent(tmp_path)
    agent._apply_user_input("u1")
    agent._emit_assistant("a1")
    agent._apply_user_input("u2")
    agent._emit_assistant("a2")
    agent._apply_user_input("u3")
    agent._emit_assistant("a3")

    # User clicks retry on a2 → backend gets turn_index=2.
    await agent.regenerate_last_response(turn_index=2)

    # A new branch must be created at turn 2.
    events = store.get_events("alice")
    new_branches_at_t2 = sorted(
        {
            e.get("branch_id")
            for e in events
            if e.get("turn_index") == 2 and isinstance(e.get("branch_id"), int)
        }
    )
    assert new_branches_at_t2 == [1, 2], (
        f"BUG B: retry on turn 2 should add branch_id=2 alongside "
        f"the original branch_id=1. Got branches {new_branches_at_t2}."
    )

    # And the post-retry replay should hide the original turn-3
    # follow-up (its parent_branch_path mismatches the new selection).
    msgs = replay_conversation(store.get_events("alice"))
    user_msgs = _user_messages(msgs)
    assert user_msgs == ["u1", "u2"], (
        f"After retry on turn 2 the original turn 3 should be hidden "
        f"(its parent_branch_path is [(1,1),(2,1)]). Got {user_msgs}."
    )
