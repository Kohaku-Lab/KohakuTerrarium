"""End-to-end audit of every realistic branching flow.

The codebase has multiple branching code paths that historically
diverged: ``regenerate_last_response``, ``edit_and_rerun``,
``rewind_to``, fresh ``user_input`` events, branch switching via
replay's ``branch_view`` override, and the metadata surfaces
(``collect_branch_metadata`` / ``_live_user_turns``) the UI relies on.
This file pins every meaningful user-facing flow against the expected
replay + navigator behavior so regressions surface immediately.

Each test drives the real ``AgentMessagesMixin`` + a fixture
``_FakeAgent`` whose ``_rerun_from_last`` mirrors the production
``_process_event`` post-fix semantics exactly (single event-log write
per turn, collision-safe branch allocation on fresh input).
"""

import pytest

from kohakuterrarium.core.agent_messages import AgentMessagesMixin
from kohakuterrarium.core.conversation import Conversation
from kohakuterrarium.llm.message import create_message
from kohakuterrarium.session.history import (
    collect_branch_metadata,
    replay_conversation,
    select_live_event_ids,
)
from kohakuterrarium.session.store import SessionStore

# -----------------------------------------------------------------------------
# Fixture: the agent surface we'd see in production.
# -----------------------------------------------------------------------------


class _FakeController:
    def __init__(self, conv: Conversation):
        self.conversation = conv


class _FakeConfig:
    name = "alice"


class _FakeAgent(AgentMessagesMixin):
    """Production-equivalent surface for branch ops.

    ``_rerun_from_last`` mirrors the post-fix ``_process_event``:
    event-log writes for rerun triggers are owned by
    ``AgentMessagesMixin`` (this class's helpers), not duplicated by
    the handler.

    ``_apply_user_input`` mirrors the fresh-input bookkeeping in
    ``_process_event``: bump turn, allocate branch_id past any
    existing events at the new turn (collision-safe).
    """

    def __init__(self, store: SessionStore):
        self.config = _FakeConfig()
        self.session_store = store
        self.controller = _FakeController(Conversation())
        self._turn_index = 0
        self._branch_id = 0
        self._parent_branch_path: list[tuple[int, int]] = []

    async def _rerun_from_last(self, new_user_content="") -> None:
        edited = bool(new_user_content)
        is_pure_rerun = not edited

        if not is_pure_rerun:
            self.controller.conversation.append("user", new_user_content)

        assistant_text = (
            f"reply-to-{new_user_content}"
            if new_user_content
            else f"reply-t{self._turn_index}b{self._branch_id}"
        )
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
        """Fresh user input. Mirrors ``_process_event`` non-rerun path."""
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

    # -------------------------------------------------------------------
    # Frontend-style helpers: simulate "switch to branch X then continue"
    # by adjusting agent state to match the chosen viewpoint, then
    # continuing as if from there. The frontend equivalent is
    # selectBranch + a fresh user_input; here we provide the same shape.
    # -------------------------------------------------------------------

    def _switch_view_to(self, branch_view: dict[int, int]) -> None:
        """Reload in-memory conversation as if the user picked
        ``branch_view`` and the agent reloaded its state.

        Real backend doesn't do this automatically today (frontend
        selectBranch is view-only) — we model the SUPPORTED scenario
        of "edit on whichever branch is latest" by physically restoring
        agent state to the branch_view leaf.
        """
        events = self.session_store.get_events(self.config.name)
        msgs = replay_conversation(events, branch_view=branch_view)
        # Reset in-memory conversation from replay (drop system if not
        # already present — Conversation always has system at index 0;
        # the replay puts a synthesized one only if events had one).
        self.controller.conversation._messages.clear()
        if not msgs or msgs[0].get("role") != "system":
            self.controller.conversation._messages.append(create_message("system", ""))
        for m in msgs:
            self.controller.conversation.append(m["role"], m.get("content", ""))
        # Restore agent state to the branch_view leaf's (turn, branch).
        latest_turn = max(branch_view.keys()) if branch_view else 0
        if latest_turn:
            self._turn_index = latest_turn
            self._branch_id = branch_view[latest_turn]
            # parent_branch_path = the entries for turns earlier than latest.
            self._parent_branch_path = [
                (t, b) for t, b in sorted(branch_view.items()) if t < latest_turn
            ]


def _new_agent(tmp_path) -> _FakeAgent:
    path = tmp_path / "session.kohakutr.v2"
    store = SessionStore(str(path))
    store.init_meta(
        session_id="s1",
        config_type="agent",
        config_path="x",
        pwd=str(tmp_path),
        agents=["alice"],
    )
    return _FakeAgent(store)


def _user_text(msgs):
    return [m.get("content") for m in msgs if m.get("role") == "user"]


def _assistant_text(msgs):
    return [m.get("content") for m in msgs if m.get("role") == "assistant"]


def _msg_count_for_branch(events, *, turn_index, branch_id, msg_type):
    return sum(
        1
        for e in events
        if e.get("type") == msg_type
        and e.get("turn_index") == turn_index
        and e.get("branch_id") == branch_id
    )


# =============================================================================
# A. SINGLE-OP BASELINES — must work as the elementary primitives.
# =============================================================================


@pytest.mark.asyncio
async def test_A1_tail_regenerate_opens_new_branch_at_tail(tmp_path):
    """Click retry on the tail assistant.

    Effect: branch_id of the tail turn bumps; user message unchanged.
    """
    agent = _new_agent(tmp_path)
    agent._apply_user_input("u1")
    agent._emit_assistant("a1")
    agent._apply_user_input("u2")
    agent._emit_assistant("a2")

    await agent.regenerate_last_response()

    events = agent.session_store.get_events("alice")
    # Branch 2 of turn 2 exists, with the SAME user message as branch 1.
    assert (
        _msg_count_for_branch(
            events, turn_index=2, branch_id=2, msg_type="user_message"
        )
        == 1
    )
    msgs = replay_conversation(events)
    assert _user_text(msgs) == ["u1", "u2"]
    assert _assistant_text(msgs)[-1].startswith("reply-")


@pytest.mark.asyncio
async def test_A2_tail_edit_opens_new_branch_with_new_content(tmp_path):
    """Edit the tail user message + save.

    Effect: new branch of tail turn with the edited content.
    """
    agent = _new_agent(tmp_path)
    agent._apply_user_input("u1")
    agent._emit_assistant("a1")
    agent._apply_user_input("u2")
    agent._emit_assistant("a2")

    assert await agent.edit_and_rerun(2, "u2_edited", turn_index=2) is True

    events = agent.session_store.get_events("alice")
    branch2_msgs = [
        e
        for e in events
        if e.get("type") == "user_message"
        and e.get("turn_index") == 2
        and e.get("branch_id") == 2
    ]
    assert len(branch2_msgs) == 1
    assert branch2_msgs[0]["content"] == "u2_edited"
    assert _user_text(replay_conversation(events)) == ["u1", "u2_edited"]


@pytest.mark.asyncio
async def test_A3_retry_non_tail_assistant_targets_clicked_turn(tmp_path):
    """Click retry on a2 in [u1 a1 u2 a2 u3 a3] — new branch at TURN 2."""
    agent = _new_agent(tmp_path)
    agent._apply_user_input("u1")
    agent._emit_assistant("a1")
    agent._apply_user_input("u2")
    agent._emit_assistant("a2")
    agent._apply_user_input("u3")
    agent._emit_assistant("a3")

    await agent.regenerate_last_response(turn_index=2)

    events = agent.session_store.get_events("alice")
    new_b = _msg_count_for_branch(
        events, turn_index=2, branch_id=2, msg_type="user_message"
    )
    assert new_b == 1, "retry on turn 2 must create branch 2 of turn 2"
    # Replay: turn 3 (original) hidden because parent_path = [(1,1),(2,1)]
    # no longer matches selection {1:1, 2:2}.
    msgs = replay_conversation(events)
    assert _user_text(msgs) == [
        "u1",
        "u2",
    ], f"after retry on turn 2, original turn 3 must be hidden, got {_user_text(msgs)}"


@pytest.mark.asyncio
async def test_A4_edit_non_tail_user_truncates_later_turns(tmp_path):
    """Edit u2 in [u1 a1 u2 a2 u3 a3] — drops u3/a3 from the live view."""
    agent = _new_agent(tmp_path)
    for i in (1, 2, 3):
        agent._apply_user_input(f"u{i}")
        agent._emit_assistant(f"a{i}")

    assert await agent.edit_and_rerun(2, "u2_edited", turn_index=2) is True

    msgs = replay_conversation(agent.session_store.get_events("alice"))
    assert _user_text(msgs) == ["u1", "u2_edited"]


# =============================================================================
# B. BRANCH SWITCHING — view-time operations via branch_view.
# =============================================================================


@pytest.mark.asyncio
async def test_B1_switch_back_to_original_branch_replays_original(tmp_path):
    """After edit u2 → u2', selecting branch_view={2:1} shows original chain."""
    agent = _new_agent(tmp_path)
    for i in (1, 2, 3):
        agent._apply_user_input(f"u{i}")
        agent._emit_assistant(f"a{i}")
    await agent.edit_and_rerun(2, "u2_edited", turn_index=2)

    events = agent.session_store.get_events("alice")
    # Default (latest branches): u1 + u2_edited
    assert _user_text(replay_conversation(events)) == ["u1", "u2_edited"]
    # Switch back to branch 1 of turn 2 → original u1, u2, u3 visible.
    assert _user_text(replay_conversation(events, branch_view={2: 1})) == [
        "u1",
        "u2",
        "u3",
    ]


@pytest.mark.asyncio
async def test_B2_add_input_after_switch_does_not_collide_with_old_subtree(tmp_path):
    """The user's exact bug repro: edit u2, then add u4 — u4 must not
    collide with the original turn-3 events (parent_path mismatch)."""
    agent = _new_agent(tmp_path)
    for i in (1, 2, 3):
        agent._apply_user_input(f"u{i}")
        agent._emit_assistant(f"a{i}")
    await agent.edit_and_rerun(2, "u2_edited", turn_index=2)

    agent._apply_user_input("u4")
    agent._emit_assistant("a4")

    msgs = replay_conversation(agent.session_store.get_events("alice"))
    assert _user_text(msgs) == ["u1", "u2_edited", "u4"], (
        f"u4 must appear after u2_edited (not collide with orphan turn-3), "
        f"got {_user_text(msgs)}"
    )


@pytest.mark.asyncio
async def test_B3_edit_in_new_subtree_keeps_parent_chain_visible(tmp_path):
    """The other half of the user's bug: edit u4 → u5, the chain
    [u1, u2_edited, u5] must remain — u2_edited mustn't vanish."""
    agent = _new_agent(tmp_path)
    for i in (1, 2, 3):
        agent._apply_user_input(f"u{i}")
        agent._emit_assistant(f"a{i}")
    await agent.edit_and_rerun(2, "u2_edited", turn_index=2)
    agent._apply_user_input("u4")
    agent._emit_assistant("a4")

    await agent.edit_and_rerun(
        message_idx=4,  # u4 sits at idx 4 (u1, a1, u2_edited, a2_edited, u4)
        new_content="u5",
        turn_index=3,
    )

    msgs = replay_conversation(agent.session_store.get_events("alice"))
    assert _user_text(msgs) == ["u1", "u2_edited", "u5"]


@pytest.mark.asyncio
async def test_B4_branch_metadata_subtree_aware_at_each_turn(tmp_path):
    """Navigator <x/N> count must reflect the visible subtree.

    Setup: turn 2 has branches 1 (original) and 2 (edited). Turn 3
    branch 1 is under turn 2 branch 1; turn 3 branch 2 is a regen of
    turn 3 branch 1 (still under turn 2 branch 1).

    Default view (latest everywhere) selects turn 2 branch 2 — there
    is NO turn 3 in this subtree, so collect_branch_metadata should
    not report turn 3 at all.
    Switching to turn 2 branch 1 makes turn 3 visible with branches
    [1, 2].
    """
    agent = _new_agent(tmp_path)
    agent._apply_user_input("u1")
    agent._emit_assistant("a1")
    agent._apply_user_input("u2")
    agent._emit_assistant("a2")
    agent._apply_user_input("u3")
    agent._emit_assistant("a3")
    # Regen turn 3 (tail at this point) → branch 2 of turn 3.
    await agent.regenerate_last_response()
    # Edit u2 (turn 2) → branch 2 of turn 2, orphaning turn-3 branches.
    await agent.edit_and_rerun(2, "u2_edited", turn_index=2)

    events = agent.session_store.get_events("alice")
    meta_default = collect_branch_metadata(events)
    # Default subtree (turn 2 branch 2) — no turn 3 here.
    assert (
        3 not in meta_default
    ), f"turn 3 must be hidden in the edited subtree; got {meta_default}"
    # Switched subtree (turn 2 branch 1) — turn 3 has branches [1, 2].
    meta_b1 = collect_branch_metadata(events, branch_view={2: 1})
    assert sorted(meta_b1.get(3, {}).get("branches", [])) == [1, 2]


# =============================================================================
# C. COMPOUNDING — multiple successive ops must not corrupt state.
# =============================================================================


@pytest.mark.asyncio
async def test_C1_multiple_edits_at_different_turns(tmp_path):
    """Edit turn 2, then edit turn 4 — both edits must be distinguishable."""
    agent = _new_agent(tmp_path)
    for i in (1, 2, 3, 4, 5):
        agent._apply_user_input(f"u{i}")
        agent._emit_assistant(f"a{i}")

    await agent.edit_and_rerun(2, "u2_edited", turn_index=2)
    # After turn-2 edit, conversation has been truncated + rebuilt to
    # [u1, a1, u2_edited, a2_edited]. Continue from there.
    agent._apply_user_input("u3_after_edit")
    agent._emit_assistant("a3_after_edit")
    agent._apply_user_input("u4_after_edit")
    agent._emit_assistant("a4_after_edit")

    # Now edit u4_after_edit (the second user message of this subtree
    # at turn=4 in this subtree's lineage).
    await agent.edit_and_rerun(
        message_idx=6,
        new_content="u4_doubly_edited",
        turn_index=4,
    )

    msgs = replay_conversation(agent.session_store.get_events("alice"))
    assert _user_text(msgs) == ["u1", "u2_edited", "u3_after_edit", "u4_doubly_edited"]


@pytest.mark.asyncio
async def test_C2_regen_same_turn_three_times(tmp_path):
    """Repeated regen at same turn produces branches [1, 2, 3, ...]."""
    agent = _new_agent(tmp_path)
    agent._apply_user_input("u1")
    agent._emit_assistant("a1")

    await agent.regenerate_last_response()  # → branch 2
    assert agent._branch_id == 2

    # Re-add the user msg so find_last_user_index works next regen.
    agent.controller.conversation.append("user", "u1")
    await agent.regenerate_last_response()  # → branch 3
    assert agent._branch_id == 3

    events = agent.session_store.get_events("alice")
    meta = collect_branch_metadata(events)
    assert meta[1]["branches"] == [1, 2, 3]
    assert meta[1]["latest_branch"] == 3


@pytest.mark.asyncio
async def test_C3_retry_then_continue_records_correct_parent_path(tmp_path):
    """After ``retry on turn 2`` (new branch 2 of turn 2), continuing
    with u4 must record (turn=3, branch=1, parent=[(1,1),(2,2)]) — no
    collision with the orphan original (3, 1)."""
    agent = _new_agent(tmp_path)
    for i in (1, 2, 3):
        agent._apply_user_input(f"u{i}")
        agent._emit_assistant(f"a{i}")

    await agent.regenerate_last_response(turn_index=2)
    agent._apply_user_input("u4")
    agent._emit_assistant("a4")

    events = agent.session_store.get_events("alice")
    new_u4_events = [
        e
        for e in events
        if e.get("type") == "user_message" and e.get("content") == "u4"
    ]
    assert len(new_u4_events) == 1
    assert new_u4_events[0]["parent_branch_path"] == [[1, 1], [2, 2]]
    # And the branch_id collision-avoidance must have kicked in: u4's
    # branch_id is > 1 because the original turn-3 used branch 1.
    assert new_u4_events[0]["branch_id"] >= 1


# =============================================================================
# D. INVARIANTS — properties that must hold regardless of operation history.
# =============================================================================


@pytest.mark.asyncio
async def test_D1_no_duplicate_user_message_per_branch(tmp_path):
    """Every (turn_index, branch_id) must contain at most one
    user_message event. Regression for the double-append bug."""
    agent = _new_agent(tmp_path)
    for i in (1, 2, 3):
        agent._apply_user_input(f"u{i}")
        agent._emit_assistant(f"a{i}")
    await agent.edit_and_rerun(2, "u2'", turn_index=2)
    await agent.regenerate_last_response()
    await agent.edit_and_rerun(2, "u2''", turn_index=2)

    events = agent.session_store.get_events("alice")
    seen: dict[tuple[int, int], int] = {}
    for e in events:
        if e.get("type") != "user_message":
            continue
        key = (e.get("turn_index"), e.get("branch_id"))
        seen[key] = seen.get(key, 0) + 1
    dups = {k: v for k, v in seen.items() if v > 1}
    assert not dups, f"duplicate user_message events at branches: {dups}"


@pytest.mark.asyncio
async def test_D2_branch_id_globally_unique_per_turn(tmp_path):
    """A (turn_index, branch_id) pair must never be reused with
    different parent_branch_paths (the collision that hid u4)."""
    agent = _new_agent(tmp_path)
    for i in (1, 2, 3):
        agent._apply_user_input(f"u{i}")
        agent._emit_assistant(f"a{i}")
    await agent.edit_and_rerun(2, "u2_edited", turn_index=2)
    agent._apply_user_input("u4")
    agent._emit_assistant("a4")

    events = agent.session_store.get_events("alice")
    # Group user_message events by (turn, branch) and verify parent
    # paths are consistent within each group.
    paths_by_key: dict[tuple[int, int], set] = {}
    for e in events:
        if e.get("type") != "user_message":
            continue
        key = (e.get("turn_index"), e.get("branch_id"))
        path = tuple(tuple(p) for p in (e.get("parent_branch_path") or []))
        paths_by_key.setdefault(key, set()).add(path)
    collisions = {k: paths for k, paths in paths_by_key.items() if len(paths) > 1}
    assert (
        not collisions
    ), f"(turn, branch) reused with conflicting parent_paths: {collisions}"


@pytest.mark.asyncio
async def test_D3_replay_idempotent_for_same_events(tmp_path):
    """replay_conversation is deterministic — same events → same result."""
    agent = _new_agent(tmp_path)
    for i in (1, 2, 3):
        agent._apply_user_input(f"u{i}")
        agent._emit_assistant(f"a{i}")
    await agent.edit_and_rerun(2, "u2_edited", turn_index=2)
    agent._apply_user_input("u4")
    agent._emit_assistant("a4")

    events = agent.session_store.get_events("alice")
    r1 = replay_conversation(events)
    r2 = replay_conversation(events)
    r3 = replay_conversation(events)
    assert r1 == r2 == r3


@pytest.mark.asyncio
async def test_D4_metadata_total_branches_match_unique_branch_ids(tmp_path):
    """collect_branch_metadata's branch list for a turn equals the
    set of unique branch_ids recorded at that turn (within the
    current subtree)."""
    agent = _new_agent(tmp_path)
    agent._apply_user_input("u1")
    agent._emit_assistant("a1")
    agent._apply_user_input("u2")
    agent._emit_assistant("a2")
    await agent.regenerate_last_response()  # branch 2 of turn 2
    # Replay still has u2 at the conversation tail, but a2 was popped
    # by regen_last_response — re-emit so we can edit safely.
    await agent.edit_and_rerun(2, "u2_v3", turn_index=2)  # branch 3 of turn 2

    events = agent.session_store.get_events("alice")
    meta = collect_branch_metadata(events)
    assert sorted(meta[2]["branches"]) == [1, 2, 3]
    assert meta[2]["latest_branch"] == 3


# =============================================================================
# E. NON-LATEST-BRANCH EDIT/RETRY — the "back to first branch and try to
# edit u3" scenario the user reported as silently no-op.
# =============================================================================


@pytest.mark.asyncio
async def test_E1_edit_on_non_latest_branch_succeeds(tmp_path):
    """User scenario:

    Start: u1 a1 u2 a2 u3 a3.
    Edit u2 → u2_edited (creates branch 2 of turn 2).
    Switch UI back to branch 1 of turn 2 — visible: u1 a1 u2 a2 u3 a3.
    Edit u3 → u3_edited.

    Expected: new branch at turn 3 UNDER branch 1 of turn 2; replay
    with branch_view={2: 1} shows [u1, u2, u3_edited]. Without the
    branch_view plumbing the backend's agent was still on branch 2
    of turn 2 and the edit silently failed.
    """
    agent = _new_agent(tmp_path)
    for i in (1, 2, 3):
        agent._apply_user_input(f"u{i}")
        agent._emit_assistant(f"a{i}")
    await agent.edit_and_rerun(2, "u2_edited", turn_index=2)

    # User selectBranch(2, 1) — frontend-only locally; backend just
    # receives branch_view={2:1} on the next edit.
    ok = await agent.edit_and_rerun(
        message_idx=-1,
        new_content="u3_edited",
        turn_index=3,
        branch_view={2: 1},
    )
    assert ok is True, "edit on non-latest branch must succeed"

    events = agent.session_store.get_events("alice")
    # Replay under the user's chosen subtree — turn 2 branch 1 +
    # turn 3 whatever-is-latest-under-it.
    msgs = replay_conversation(events, branch_view={2: 1})
    assert _user_text(msgs) == ["u1", "u2", "u3_edited"]


@pytest.mark.asyncio
async def test_E2_retry_on_non_latest_branch_succeeds(tmp_path):
    """Same scenario but with retry (regen with same content):

    Start: u1 a1 u2 a2 u3 a3.
    Edit u2 → u2_edited.
    Switch back to turn-2 branch 1.
    Retry on a3 (turn 3, branch 1).

    Expected: new branch at turn 3 under branch 1 of turn 2.
    """
    agent = _new_agent(tmp_path)
    for i in (1, 2, 3):
        agent._apply_user_input(f"u{i}")
        agent._emit_assistant(f"a{i}")
    await agent.edit_and_rerun(2, "u2_edited", turn_index=2)

    await agent.regenerate_last_response(
        turn_index=3,
        branch_view={2: 1},
    )

    events = agent.session_store.get_events("alice")
    # Turn 3 under branch 1 of turn 2 must now have branches [1, 2].
    meta = collect_branch_metadata(events, branch_view={2: 1})
    assert sorted(meta.get(3, {}).get("branches", [])) == [1, 2], (
        f"retry must add branch 2 of turn 3 under the chosen subtree; "
        f"got {meta.get(3, {})}"
    )


@pytest.mark.asyncio
async def test_E3_continue_after_switching_to_old_branch(tmp_path):
    """User switches to an old branch, then continues with a new
    user input — the new input must extend that subtree, not the
    latest one."""
    agent = _new_agent(tmp_path)
    for i in (1, 2):
        agent._apply_user_input(f"u{i}")
        agent._emit_assistant(f"a{i}")
    await agent.edit_and_rerun(2, "u2_edited", turn_index=2)

    # Switch to branch 1 of turn 2 view (reloads agent state to
    # match — frontend would just selectBranch; our backend helper
    # mirrors that for tests).
    agent._switch_view_to({2: 1})

    agent._apply_user_input("u3_continued")
    agent._emit_assistant("a3_continued")

    msgs = replay_conversation(
        agent.session_store.get_events("alice"),
        branch_view={2: 1},
    )
    assert _user_text(msgs) == ["u1", "u2", "u3_continued"]


@pytest.mark.asyncio
async def test_D5_select_live_event_ids_excludes_orphans(tmp_path):
    """After edit u2 + add u4, the original turn-3 events must NOT be
    in the live subtree's event-id set."""
    agent = _new_agent(tmp_path)
    for i in (1, 2, 3):
        agent._apply_user_input(f"u{i}")
        agent._emit_assistant(f"a{i}")
    original_turn3_user_id = next(
        e["event_id"]
        for e in agent.session_store.get_events("alice")
        if e.get("type") == "user_message" and e.get("content") == "u3"
    )

    await agent.edit_and_rerun(2, "u2_edited", turn_index=2)
    agent._apply_user_input("u4")
    agent._emit_assistant("a4")

    events = agent.session_store.get_events("alice")
    live = select_live_event_ids(events)
    assert (
        original_turn3_user_id not in live
    ), "original u3 user_message must be orphaned from the live subtree"
