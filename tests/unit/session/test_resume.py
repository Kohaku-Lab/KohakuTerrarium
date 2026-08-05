"""Unit tests for :mod:`kohakuterrarium.session.resume`.

The full ``resume_agent`` path is exercised with a real agent config
on disk plus a ``ScriptedLLM`` injected via the monkeypatched LLM
bootstrap, so resumption is tested end-to-end without a live provider.
"""

import os

import pytest

from kohakuterrarium.bootstrap import agent_init as _agent_init
from kohakuterrarium.bootstrap import llm as _bootstrap_llm
from kohakuterrarium.core.conversation import Conversation
from kohakuterrarium.errors import SessionNotResumableError
from kohakuterrarium.session.resume_branch import snapshot_mismatches_branch

from kohakuterrarium.session.resume import (
    IO_MODES,
    _build_conversation,
    _create_io_modules,
    _load_conversation_with_replay_fallback,
    _open_store_with_migration,
    _restore_turn_branch_state,
    align_agent_name,
    detect_session_type,
    inject_saved_state,
    resume_agent,
)
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.testing.llm import ScriptedLLM


@pytest.fixture
def patched_llm(monkeypatch):
    """Inject a ScriptedLLM into both LLM bootstrap entry points so
    ``Agent.from_path`` (called by resume_agent) never needs a real
    provider."""

    def _fake_create(config, llm=None):
        return ScriptedLLM(["OK"])

    monkeypatch.setattr(_bootstrap_llm, "create_llm_provider", _fake_create)
    monkeypatch.setattr(_agent_init, "create_llm_provider", _fake_create)


def _write_agent_config(config_dir) -> None:
    """Write a minimal but complete creature config dir."""
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.yaml").write_text(
        "name: resumee\n"
        "controller:\n"
        "  tool_format: bracket\n"
        "  include_tools_in_prompt: false\n"
        "  include_hints_in_prompt: false\n"
        "system_prompt: |\n"
        "  test agent\n"
        "input:\n"
        "  type: none\n"
        "output:\n"
        "  type: stdout\n"
    )


# ── _create_io_modules ────────────────────────────────────────────


class TestCreateIoModules:
    def test_known_modes_constants(self):
        # The publicly-advertised modes — keep this stable so callers
        # can introspect the set.
        assert IO_MODES == ("cli", "plain", "tui")

    def test_cli_mode_rejected(self):
        # cli must be built by the caller (cycle-prevention rule).
        with pytest.raises(ValueError, match="cli"):
            _create_io_modules("cli")

    def test_unknown_mode_rejected(self):
        with pytest.raises(ValueError, match="Unknown IO mode"):
            _create_io_modules("not-a-mode")

    def test_plain_mode_builds_cli_input_stdout_output(self):
        from kohakuterrarium.modules.input.base import InputModule
        from kohakuterrarium.modules.output.base import OutputModule

        inp, out = _create_io_modules("plain")
        # plain mode → a CLI-style input + a stdout output module.
        assert isinstance(inp, InputModule)
        assert isinstance(out, OutputModule)

    def test_tui_mode_builds_tui_input_output(self):
        from kohakuterrarium.modules.input.base import InputModule
        from kohakuterrarium.modules.output.base import OutputModule

        inp, out = _create_io_modules("tui")
        assert isinstance(inp, InputModule)
        assert isinstance(out, OutputModule)


# ── _build_conversation ───────────────────────────────────────────


class TestBuildConversation:
    def test_empty(self):
        conv = _build_conversation([])
        assert isinstance(conv, Conversation)
        assert list(conv.to_messages()) == []

    def test_basic_round_trip(self):
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        conv = _build_conversation(msgs)
        out = conv.to_messages()
        # Both role AND content round-trip unchanged.
        assert out == [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]

    def test_metadata_preserved(self):
        # A message carrying a ``metadata`` dict round-trips it through
        # the rebuilt Conversation.
        msgs = [
            {"role": "user", "content": "hi", "metadata": {"source": "test"}},
        ]
        conv = _build_conversation(msgs)
        out = conv.to_messages()
        assert out[0]["role"] == "user"
        # metadata is carried through onto the rebuilt message object.
        rebuilt = conv.get_messages()
        assert rebuilt[0].metadata == {"source": "test"}

    def test_tool_calls_preserved(self):
        msgs = [
            {
                "role": "assistant",
                "content": "calling",
                "tool_calls": [{"id": "c1", "type": "function"}],
            },
            {
                "role": "tool",
                "content": "result",
                "tool_call_id": "c1",
                "name": "bash",
            },
        ]
        conv = _build_conversation(msgs)
        out = conv.to_messages()
        # Assistant message keeps its tool_calls verbatim.
        assert out[0]["role"] == "assistant"
        assert out[0]["content"] == "calling"
        assert out[0]["tool_calls"] == [{"id": "c1", "type": "function"}]
        # Tool message keeps role, content, the linking call id and name.
        assert out[1]["role"] == "tool"
        assert out[1]["content"] == "result"
        assert out[1]["tool_call_id"] == "c1"
        assert out[1]["name"] == "bash"


class TestPendingTailAcrossResume:
    def test_pending_call_survives_snapshot_rebuild_and_completes(self):
        # Full lifecycle: a compact snapshot saved mid-turn carries an
        # in-flight announcement → the resume REBUILD must keep it →
        # the arriving result pairs with it → ordinary serialization
        # keeps the completed pair.
        saved = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "pending_1",
                        "type": "function",
                        "function": {"name": "bash", "arguments": "{}"},
                    }
                ],
            },
        ]
        conv = _build_conversation(saved)
        announced = [
            tc["id"]
            for m in conv.get_messages()
            if getattr(m, "role", None) == "assistant"
            and getattr(m, "tool_calls", None)
            for tc in m.tool_calls
        ]
        assert (
            "pending_1" in announced
        ), "resume rebuild must keep the in-flight announcement"
        # The result lands (post-watermark tail / stop-sweep terminal).
        conv.append("tool", "done", tool_call_id="pending_1", name="bash")
        wire = conv.to_messages()
        assert any(
            m.get("role") == "tool" and m.get("tool_call_id") == "pending_1"
            for m in wire
        )
        assert any(
            tc["id"] == "pending_1"
            for m in wire
            if m.get("role") == "assistant" and m.get("tool_calls")
            for tc in m["tool_calls"]
        )

    def test_wire_serialization_still_drops_dead_pending_call(self):
        # Provider payloads must NOT carry an unanswered announcement.
        saved = [
            {"role": "system", "content": "sys"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "dead_1",
                        "type": "function",
                        "function": {"name": "bash", "arguments": "{}"},
                    }
                ],
            },
        ]
        conv = _build_conversation(saved)
        wire = conv.to_messages()
        assert all(
            tc["id"] != "dead_1"
            for m in wire
            if m.get("role") == "assistant" and m.get("tool_calls")
            for tc in m["tool_calls"]
        )


# ── _restore_turn_branch_state ────────────────────────────────────


class _FakeAgent:
    def __init__(self):
        self._turn_index = 0
        self._branch_id = 0
        self._parent_branch_path = []


class TestRestoreTurnBranchState:
    def test_no_events_no_change(self, tmp_path):
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            agent = _FakeAgent()
            _restore_turn_branch_state(agent, store, "alice")
            assert agent._turn_index == 0
            assert agent._branch_id == 0
        finally:
            store.close()

    def test_picks_latest_branch(self, tmp_path):
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            store.append_event(
                "alice", "user_message", {"content": "a"}, turn_index=1, branch_id=1
            )
            store.append_event(
                "alice", "user_message", {"content": "b"}, turn_index=1, branch_id=2
            )
            store.flush()
            agent = _FakeAgent()
            _restore_turn_branch_state(agent, store, "alice")
            assert agent._turn_index == 1
            assert agent._branch_id == 2
        finally:
            store.close()

    def test_builds_parent_path(self, tmp_path):
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            for ti in (1, 2, 3):
                store.append_event(
                    "alice", "user_message", {}, turn_index=ti, branch_id=1
                )
            store.flush()
            agent = _FakeAgent()
            _restore_turn_branch_state(agent, store, "alice")
            assert agent._turn_index == 3
            assert agent._parent_branch_path == [(1, 1), (2, 1)]
        finally:
            store.close()

    def test_restores_one_coherent_ancestry_path(self, tmp_path):
        # Turn 1 has branches 1+2; turn 2 branch 1 exists ONLY under
        # turn1/branch1. Per-turn independent max produced
        # (turn 2, branch 1, parent [(1, 2)]) — an ancestry that never
        # existed. The restore must mirror replay's path-aware pick.
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            store.append_event(
                "alice",
                "user_message",
                {"content": "a"},
                turn_index=1,
                branch_id=1,
            )
            store.append_event(
                "alice",
                "user_message",
                {"content": "a-regen"},
                turn_index=1,
                branch_id=2,
            )
            store.append_event(
                "alice",
                "user_message",
                {"content": "b under old branch"},
                turn_index=2,
                branch_id=1,
                parent_branch_path=[(1, 1)],
            )
            store.flush()
            agent = _FakeAgent()
            _restore_turn_branch_state(agent, store, "alice")
            # Path-aware selection: turn1 → branch 2 (latest); turn 2's
            # only branch lives under (1,1) — incompatible — so the
            # coherent leaf is turn 1 / branch 2 with an empty parent
            # path, exactly what replay_conversation renders.
            assert (agent._turn_index, agent._branch_id) == (1, 2)
            assert agent._parent_branch_path == []
        finally:
            store.close()

    def test_get_events_failure_leaves_agent_untouched(self, tmp_path):
        # If reading events raises, the restore is a defensive no-op —
        # the agent's turn / branch counters stay at their defaults.
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:

            def _boom(agent_name):
                raise RuntimeError("event read failed")

            store.get_events = _boom
            agent = _FakeAgent()
            _restore_turn_branch_state(agent, store, "alice")
            assert agent._turn_index == 0
            assert agent._branch_id == 0
            assert agent._parent_branch_path == []
        finally:
            store.close()


# ── align_agent_name ──────────────────────────────────────────────


class _FakeConfig:
    def __init__(self, name="random"):
        self.name = name


class _FakeNamed:
    def __init__(self):
        self._agent_name = "old"


class _FakeAgentForAlign:
    def __init__(self, name="random", with_managers=True):
        self.config = _FakeConfig(name)
        if with_managers:
            self.executor = _FakeNamed()
            self.trigger_manager = _FakeNamed()
            self.compact_manager = _FakeNamed()
        else:
            self.executor = None
            self.trigger_manager = None
            self.compact_manager = None


class TestAlignAgentName:
    def test_sets_config_name(self):
        agent = _FakeAgentForAlign()
        align_agent_name(agent, "saved")
        assert agent.config.name == "saved"

    def test_updates_manager_caches(self):
        agent = _FakeAgentForAlign()
        align_agent_name(agent, "saved")
        assert agent.executor._agent_name == "saved"
        assert agent.trigger_manager._agent_name == "saved"
        assert agent.compact_manager._agent_name == "saved"

    def test_no_managers_is_noop(self):
        agent = _FakeAgentForAlign(with_managers=False)
        # Doesn't raise.
        align_agent_name(agent, "saved")
        assert agent.config.name == "saved"

    def test_no_config_is_noop(self):
        class _Bare:
            config = None

        agent = _Bare()
        align_agent_name(agent, "saved")  # no raise


# ── _load_conversation_with_replay_fallback ──────────────────────


class TestLoadConversationFallback:
    def test_no_events_returns_snapshot(self, tmp_path):
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            store.save_conversation("alice", [{"role": "user", "content": "hi"}])
            out = _load_conversation_with_replay_fallback(store, "alice")
            assert out == [{"role": "user", "content": "hi"}]
        finally:
            store.close()

    def test_snapshot_fresh_with_metadata_returns_snapshot(self, tmp_path):
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            _, eid = store.append_event("alice", "x", {})
            store.save_conversation(
                "alice",
                [
                    {
                        "role": "user",
                        "content": "snap",
                        "metadata": {"turn_index": 1, "branch_id": 1},
                    }
                ],
            )
            store.state["alice:snapshot_event_id"] = eid
            store.flush()
            out = _load_conversation_with_replay_fallback(store, "alice")
            assert out[0]["content"] == "snap"
            assert out[0]["metadata"]["turn_index"] == 1
        finally:
            store.close()

    def test_snapshot_fresh_without_metadata_backfills(self, tmp_path):
        # Legacy snapshots carry no turn metadata, so edit targeting
        # (turn_index lookup) cannot resolve. Backfill turn identity from
        # the event log while trusting the snapshot verbatim — a full
        # replay would resurrect pre-compact history and drop snapshot-only
        # in-flight messages.
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            _, eid = store.append_event(
                "alice",
                "user_message",
                {"content": "x"},
                turn_index=1,
                branch_id=1,
            )
            store.save_conversation("alice", [{"role": "user", "content": "snap"}])
            store.state["alice:snapshot_event_id"] = eid
            store.flush()
            out = _load_conversation_with_replay_fallback(store, "alice")
            assert out[0]["content"] == "snap"
            assert out[0]["metadata"]["turn_index"] == 1
            assert out[0]["metadata"]["branch_id"] == 1
        finally:
            store.close()

    def test_snapshot_stale_appends_post_watermark_tail(self, tmp_path):
        # The snapshot is the only artifact reflecting compaction — a
        # full replay would resurrect compacted history. A stale
        # snapshot keeps its prefix; only events past the watermark
        # replay on top of it.
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            store.append_event("alice", "user_message", {"content": "fresh"})
            store.append_event("alice", "user_message", {"content": "newer"})
            store.save_conversation("alice", [{"role": "user", "content": "stale"}])
            store.state["alice:snapshot_event_id"] = 1
            store.flush()
            out = _load_conversation_with_replay_fallback(store, "alice")
            contents = [m["content"] for m in out if m["role"] == "user"]
            assert contents == ["stale", "newer"]
        finally:
            store.close()

    def test_snapshot_stale_tail_with_branch_fork_falls_back_to_replay(self, tmp_path):
        # An edit / regenerate after the snapshot REWRITES earlier
        # turns; blind snapshot+tail append would keep the superseded
        # turn AND the fork. Fork-bearing tails must full-replay.
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            store.append_event(
                "alice",
                "user_message",
                {"content": "original"},
                turn_index=1,
                branch_id=1,
            )
            store.save_conversation("alice", [{"role": "user", "content": "original"}])
            store.state["alice:snapshot_event_id"] = 1
            # Fork of turn 1 landed AFTER the snapshot.
            store.append_event(
                "alice",
                "user_message",
                {"content": "edited"},
                turn_index=1,
                branch_id=2,
            )
            store.flush()
            out = _load_conversation_with_replay_fallback(store, "alice")
            contents = [m["content"] for m in out if m["role"] == "user"]
            assert contents.count("original") + contents.count("edited") == 1, (
                "the superseded turn and its fork must not BOTH appear: " f"{contents}"
            )
        finally:
            store.close()

    def test_continuation_on_existing_branch_keeps_snapshot(self, tmp_path):
        # A tail event on an ALREADY-forked branch (pair seen before
        # the watermark) is ordinary continuation — discarding the
        # compacted snapshot for it resurrects pre-compact history.
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            store.append_event(
                "alice",
                "user_message",
                {"content": "on-branch-2"},
                turn_index=1,
                branch_id=2,
            )
            store.save_conversation(
                "alice", [{"role": "user", "content": "compacted-prefix"}]
            )
            store.state["alice:snapshot_event_id"] = 1
            store.append_event(
                "alice",
                "user_message",
                {"content": "continuation"},
                turn_index=1,
                branch_id=2,
            )
            store.flush()
            out = _load_conversation_with_replay_fallback(store, "alice")
            contents = [m["content"] for m in out if m["role"] == "user"]
            assert "compacted-prefix" in contents, (
                "snapshot must be kept for a non-fork tail; got " f"{contents}"
            )
            assert "continuation" in contents
        finally:
            store.close()

    def test_snapshot_stale_tail_tool_events_replay_paired(self, tmp_path):
        # Live streams carry tool_call/tool_result (never
        # assistant_tool_calls); the tail replay must normalize them so
        # tool results arrive paired instead of orphaned.
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            store.append_event("alice", "user_message", {"content": "q"})
            store.save_conversation("alice", [{"role": "user", "content": "q"}])
            store.state["alice:snapshot_event_id"] = 1
            store.append_event(
                "alice", "tool_call", {"name": "grep", "call_id": "grep_1", "args": {}}
            )
            store.append_event(
                "alice",
                "tool_result",
                {"name": "grep", "call_id": "grep_1", "output": "hit"},
            )
            store.flush()
            out = _load_conversation_with_replay_fallback(store, "alice")
            tools = [m for m in out if m.get("role") == "tool"]
            assert tools and tools[0]["tool_call_id"] == "grep_1"
            announced = [
                tc.get("id")
                for m in out
                if m.get("role") == "assistant" and m.get("tool_calls")
                for tc in m["tool_calls"]
            ]
            assert "grep_1" in announced
        finally:
            store.close()

    def test_missing_snapshot_replay_is_normalized(self, tmp_path):
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            store.append_event("alice", "user_message", {"content": "q"})
            store.append_event(
                "alice", "tool_call", {"name": "bash", "call_id": "bash_1", "args": {}}
            )
            store.append_event(
                "alice",
                "tool_result",
                {"name": "bash", "call_id": "bash_1", "output": "ok"},
            )
            store.flush()
            out = _load_conversation_with_replay_fallback(store, "alice")
            announced = [
                tc.get("id")
                for m in out
                if m.get("role") == "assistant" and m.get("tool_calls")
                for tc in m["tool_calls"]
            ]
            assert "bash_1" in announced
        finally:
            store.close()

    def test_build_conversation_prunes_orphan_tool_messages(self):
        # Old snapshots can carry orphans; they must be dropped ONCE at
        # build time so every later to_messages() stops re-warning.
        conv = _build_conversation(
            [
                {"role": "user", "content": "hi"},
                {"role": "tool", "content": "zombie", "tool_call_id": "dead_1"},
                {"role": "assistant", "content": "reply"},
            ]
        )
        roles = [getattr(m, "role", None) for m in conv.get_messages()]
        assert "tool" not in roles
        assert roles == ["user", "assistant"]

    def test_missing_snapshot_event_id_with_metadata_uses_snapshot(self, tmp_path):
        # When there's a snapshot but no recorded snapshot_event_id,
        # a metadata-bearing snapshot is trusted (avoid false-positive
        # replays); a legacy one without metadata is replayed.
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            store.append_event("alice", "user_message", {"content": "x"})
            store.save_conversation(
                "alice",
                [
                    {
                        "role": "user",
                        "content": "snap",
                        "metadata": {"turn_index": 1, "branch_id": 1},
                    }
                ],
            )
            store.flush()
            out = _load_conversation_with_replay_fallback(store, "alice")
            assert out[0]["content"] == "snap"
        finally:
            store.close()

    def test_missing_snapshot_event_id_without_metadata_backfills(self, tmp_path):
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            store.append_event(
                "alice",
                "user_message",
                {"content": "x"},
                turn_index=1,
                branch_id=1,
            )
            store.save_conversation("alice", [{"role": "user", "content": "snap"}])
            store.flush()
            out = _load_conversation_with_replay_fallback(store, "alice")
            assert out[0]["content"] == "snap"
            assert out[0]["metadata"]["turn_index"] == 1
        finally:
            store.close()

    def test_state_get_raising_treated_as_no_cache(self, tmp_path, monkeypatch):
        # If reading the cached snapshot_event_id from store.state raises
        # (TypeError / KeyError), the helper treats it as "no cache" and
        # backfills turn metadata onto the trusted snapshot rather than
        # crashing.
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            store.append_event(
                "alice",
                "user_message",
                {"content": "x"},
                turn_index=1,
                branch_id=1,
            )
            store.save_conversation("alice", [{"role": "user", "content": "snap"}])
            store.flush()

            def _boom_get(key, default=None):
                raise TypeError("state backend exploded")

            monkeypatch.setattr(store.state, "get", _boom_get)
            out = _load_conversation_with_replay_fallback(store, "alice")
            # cached_up_to is None → legacy snapshot without metadata gets
            # turn metadata backfilled from the event log.
            assert out[0]["content"] == "snap"
            assert out[0]["metadata"]["turn_index"] == 1
        finally:
            store.close()


# ── detect_session_type ──────────────────────────────────────────


# ── P3b: snapshot branch matching on resume ───────────────────────


class _BranchAgent:
    """Minimal agent exposing branch state for matching tests."""

    def __init__(self, turn_index=1, branch_id=1, parent_branch_path=None):
        self._turn_index = turn_index
        self._branch_id = branch_id
        self._parent_branch_path = parent_branch_path or []


class TestSnapshotMismatchesBranch:
    def test_no_tag_trusted(self, tmp_path):
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            agent = _BranchAgent(turn_index=2, branch_id=1, parent_branch_path=[(1, 1)])
            assert snapshot_mismatches_branch(store, agent, "alice") is False
        finally:
            store.close()

    def test_matching_prefix_trusted(self, tmp_path):
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            store.state["alice:snapshot_branch"] = {
                "turn_index": 2,
                "branch_id": 1,
                "parent_branch_path": [(1, 1)],
            }
            # snapshot path [(1,1),(2,1)] is prefix of agent path [(1,1),(2,1)]
            agent = _BranchAgent(turn_index=2, branch_id=1, parent_branch_path=[(1, 1)])
            assert snapshot_mismatches_branch(store, agent, "alice") is False
        finally:
            store.close()

    def test_ancestor_snapshot_trusted(self, tmp_path):
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            store.state["alice:snapshot_branch"] = {
                "turn_index": 2,
                "branch_id": 1,
                "parent_branch_path": [(1, 1)],
            }
            # agent continued to turn3 on same path -> snapshot is ancestor
            agent = _BranchAgent(
                turn_index=3, branch_id=1, parent_branch_path=[(1, 1), (2, 1)]
            )
            assert snapshot_mismatches_branch(store, agent, "alice") is False
        finally:
            store.close()

    def test_sibling_branch_snapshot_mismatches(self, tmp_path):
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            store.state["alice:snapshot_branch"] = {
                "turn_index": 2,
                "branch_id": 1,
                "parent_branch_path": [(1, 1)],
            }
            # agent landed on branch2 (sibling) -> snapshot must be rebuilt
            agent = _BranchAgent(turn_index=2, branch_id=2, parent_branch_path=[(1, 1)])
            assert snapshot_mismatches_branch(store, agent, "alice") is True
        finally:
            store.close()

    def test_bad_tag_trusted(self, tmp_path):
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            store.state["alice:snapshot_branch"] = "not-a-dict"
            agent = _BranchAgent(turn_index=2, branch_id=1)
            assert snapshot_mismatches_branch(store, agent, "alice") is False
        finally:
            store.close()


class TestDetectSessionType:
    def test_agent_by_default(self, tmp_path):
        path = tmp_path / "x.kohakutr"
        store = SessionStore(str(path))
        try:
            store.meta["format_version"] = 2
            store.init_meta("s", "agent", "/p", "/w", ["a"])
        finally:
            store.close()
        assert detect_session_type(path) == "agent"

    def test_terrarium(self, tmp_path):
        path = tmp_path / "x.kohakutr.v2"
        store = SessionStore(str(path))
        try:
            store.meta["format_version"] = 2
            store.init_meta("s", "terrarium", "/p", "/w", ["a"])
        finally:
            store.close()
        assert detect_session_type(path) == "terrarium"


# ── inject_saved_state ────────────────────────────────────────────


class _FakeSessionScratchpad:
    def __init__(self):
        self._data = {}

    def set(self, k, v):
        self._data[k] = v

    def to_dict(self):
        return dict(self._data)


class _FakeAgentSession:
    def __init__(self):
        self.scratchpad = _FakeSessionScratchpad()


class _FakeController:
    def __init__(self):
        self.conversation = Conversation()


class _ElideConfig:
    name = "alice"
    elide_tool_results = True


class _FakeCompactConfig:
    enabled = True
    max_tokens = 256_000
    threshold = 0.8


class _FakeCompactManager:
    config = _FakeCompactConfig()


class _FakeAgentForInject:
    def __init__(self):
        self.config = _FakeConfig()
        self.controller = _FakeController()
        self.session = _FakeAgentSession()
        self.executor = _FakeNamed()
        self.trigger_manager = _FakeNamed()
        self.compact_manager = _FakeCompactManager()
        self.native_tool_options = None


class TestInjectSavedState:
    def test_realigns_name(self, tmp_path):
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            agent = _FakeAgentForInject()
            inject_saved_state(agent, store, "saved-name")
            assert agent.config.name == "saved-name"
        finally:
            store.close()

    def test_loads_scratchpad(self, tmp_path):
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            store.save_state("alice", scratchpad={"key": "value"})
            agent = _FakeAgentForInject()
            inject_saved_state(agent, store, "alice")
            assert agent.session.scratchpad._data == {"key": "value"}
        finally:
            store.close()

    def test_skips_dunder_scratchpad_keys(self, tmp_path):
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            store.save_state(
                "alice",
                scratchpad={"public": 1, "__hidden__": 2},
            )
            agent = _FakeAgentForInject()
            inject_saved_state(agent, store, "alice")
            # ``public`` set; ``__hidden__`` is filtered.
            assert "public" in agent.session.scratchpad._data
            assert "__hidden__" not in agent.session.scratchpad._data
        finally:
            store.close()

    def test_legacy_native_tool_options_promoted(self, tmp_path):
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            store.save_state(
                "alice",
                scratchpad={"__native_tool_options__": {"some_flag": True}},
            )
            agent = _FakeAgentForInject()
            inject_saved_state(agent, store, "alice")
            # Legacy options went through agent.session.scratchpad.set.
            assert "__native_tool_options__" in agent.session.scratchpad._data
        finally:
            store.close()

    def test_loads_pending_resume_events(self, tmp_path):
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            # An unfinished tool call → resume should synthesize an
            # interrupted result, which lands on _pending_resume_events.
            store.append_event("alice", "tool_call", {"call_id": "c1", "name": "bash"})
            store.flush()
            agent = _FakeAgentForInject()
            inject_saved_state(agent, store, "alice")
            synth = [
                e for e in agent._pending_resume_events if e.get("_synthetic_resume")
            ]
            # Exactly one interrupted tool_result, linked to the open call.
            assert len(synth) == 1
            assert synth[0]["type"] == "tool_result"
            assert synth[0]["call_id"] == "c1"
            assert synth[0]["interrupted"] is True
            assert synth[0]["error"] == "Interrupted by session resume"
        finally:
            store.close()

    def test_loads_pending_resume_triggers(self, tmp_path):
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            store.save_state("alice", triggers=[{"name": "t1"}])
            agent = _FakeAgentForInject()
            inject_saved_state(agent, store, "alice")
            assert agent._pending_resume_triggers == [{"name": "t1"}]
        finally:
            store.close()

    def test_loads_conversation(self, tmp_path):
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            store.save_conversation("alice", [{"role": "user", "content": "saved"}])
            agent = _FakeAgentForInject()
            inject_saved_state(agent, store, "alice")
            msgs = agent.controller.conversation.to_messages()
            assert any(m.get("content") == "saved" for m in msgs)
        finally:
            store.close()

    def test_native_tool_options_apply_failure_is_swallowed(self, tmp_path):
        # If reapplying native tool options raises, inject_saved_state
        # logs and continues — the rest of the state still loads.
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            store.save_state("alice", triggers=[{"name": "t1"}])

            class _BoomOptions:
                def apply(self):
                    raise RuntimeError("native tool reapply failed")

            agent = _FakeAgentForInject()
            agent.native_tool_options = _BoomOptions()
            # Must not raise.
            inject_saved_state(agent, store, "alice")
            # The trigger load (which runs after the native-tool-options
            # block) still completed.
            assert agent._pending_resume_triggers == [{"name": "t1"}]
        finally:
            store.close()

    def test_replays_when_snapshot_branch_differs(self, tmp_path):
        # Snapshot was saved on branch1 but the latest live subtree is
        # branch2 (sibling). inject_saved_state must discard the stale
        # snapshot and rebuild the conversation for the target branch.
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            store.append_event(
                "alice",
                "user_message",
                {"content": "U1"},
                turn_index=1,
                branch_id=1,
            )
            store.append_event(
                "alice",
                "text_chunk",
                {"content": "R1"},
                turn_index=1,
                branch_id=1,
            )
            store.append_event(
                "alice",
                "user_message",
                {"content": "U2a"},
                turn_index=2,
                branch_id=1,
                parent_branch_path=[(1, 1)],
            )
            store.append_event(
                "alice",
                "user_message",
                {"content": "U2b"},
                turn_index=2,
                branch_id=2,
                parent_branch_path=[(1, 1)],
            )
            store.append_event(
                "alice",
                "text_chunk",
                {"content": "R2b"},
                turn_index=2,
                branch_id=2,
                parent_branch_path=[(1, 1)],
            )
            store.save_conversation(
                "alice",
                [
                    {"role": "user", "content": "U1"},
                    {"role": "assistant", "content": "R1"},
                    {"role": "user", "content": "U2a"},
                ],
            )
            store.state["alice:snapshot_event_id"] = 5
            store.state["alice:snapshot_branch"] = {
                "turn_index": 2,
                "branch_id": 1,
                "parent_branch_path": [(1, 1)],
            }
            store.flush()
            agent = _FakeAgentForInject()
            inject_saved_state(agent, store, "alice")
            contents = [
                m.content
                for m in agent.controller.conversation.get_messages()
                if m.role == "user"
            ]
            # rebuilt for branch2 -> U2b present (branch2's content)
            assert "U2b" in contents
            # branch1-only turn2 content must NOT be in the restored view
            assert "U2a" not in contents
        finally:
            store.close()

    def test_keeps_snapshot_when_branch_matches(self, tmp_path):
        # Snapshot and target branch agree -> trust the snapshot, do not
        # replay (branch1 content stays).
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            store.append_event(
                "alice",
                "user_message",
                {"content": "U1"},
                turn_index=1,
                branch_id=1,
            )
            store.append_event(
                "alice",
                "user_message",
                {"content": "U2a"},
                turn_index=2,
                branch_id=1,
                parent_branch_path=[(1, 1)],
            )
            store.save_conversation(
                "alice",
                [
                    {"role": "user", "content": "U1"},
                    {"role": "user", "content": "U2a"},
                ],
            )
            store.state["alice:snapshot_event_id"] = 2
            store.state["alice:snapshot_branch"] = {
                "turn_index": 2,
                "branch_id": 1,
                "parent_branch_path": [(1, 1)],
            }
            store.flush()
            agent = _FakeAgentForInject()
            inject_saved_state(agent, store, "alice")
            contents = [
                m.content
                for m in agent.controller.conversation.get_messages()
                if m.role == "user"
            ]
            assert contents == ["U1", "U2a"]
        finally:
            store.close()


# -- _open_store_with_migration ------------------------------------


class TestOpenStoreWithMigration:
    def test_opens_already_current_store(self, tmp_path):
        # A store already at the latest format opens in place -- the
        # returned store reads back the same metadata.
        path = tmp_path / "s.kohakutr.v2"
        s = SessionStore(str(path))
        try:
            s.meta["format_version"] = 2
            s.init_meta("sess", "agent", "/cfg", "/wd", ["resumee"])
        finally:
            s.close()
        opened = _open_store_with_migration(path)
        try:
            assert opened.load_meta()["config_type"] == "agent"
        finally:
            opened.close()

    def test_migration_failure_wraps_with_original_path(self, tmp_path, monkeypatch):
        # If ensure_latest_version raises, the helper re-raises a
        # RuntimeError that names the original path so the user can
        # retry against the preserved file.
        import kohakuterrarium.session.resume as resume_mod

        def _boom(p):
            raise ValueError("migration broke")

        monkeypatch.setattr(resume_mod, "ensure_latest_version", _boom)
        with pytest.raises(RuntimeError, match="Failed to migrate"):
            _open_store_with_migration(tmp_path / "x.kohakutr")


# -- resume_agent (end-to-end with ScriptedLLM) --------------------


class TestResumeAgent:
    def _make_session(self, tmp_path, config_dir, *, config_type="agent"):
        path = tmp_path / "sess.kohakutr.v2"
        s = SessionStore(str(path))
        try:
            s.meta["format_version"] = 2
            s.init_meta(
                "sess", config_type, str(config_dir), str(tmp_path), ["resumee"]
            )
            s.save_conversation(
                "resumee", [{"role": "user", "content": "earlier turn"}]
            )
            s.flush()
        finally:
            s.close()
        return path

    def test_rebuilds_agent_and_restores_conversation(self, tmp_path, patched_llm):
        config_dir = tmp_path / "creature"
        _write_agent_config(config_dir)
        path = self._make_session(tmp_path, config_dir)
        agent, store = resume_agent(path)
        try:
            # The agent was rebuilt from the saved config_path.
            assert agent.config.name == "resumee"
            # The saved conversation was injected.
            msgs = agent.controller.conversation.to_messages()
            assert any(m.get("content") == "earlier turn" for m in msgs)
            # The store was re-attached + marked running for continued
            # recording.
            assert store.load_meta()["status"] == "running"
        finally:
            store.close()

    def test_resume_does_not_chdir(self, tmp_path, patched_llm):
        # E8: resume used to ``os.chdir(saved_pwd)`` process-wide — a
        # race for concurrent multi-session programs.  The saved pwd now
        # flows into the rebuilt agent's workspace instead.
        config_dir = tmp_path / "creature"
        _write_agent_config(config_dir)
        workdir = tmp_path / "saved-pwd"
        workdir.mkdir()
        path = tmp_path / "sess.kohakutr.v2"
        s = SessionStore(str(path))
        try:
            s.meta["format_version"] = 2
            s.init_meta("sess", "agent", str(config_dir), str(workdir), ["resumee"])
            s.flush()
        finally:
            s.close()

        cwd_before = os.getcwd()
        agent, store = resume_agent(path)
        try:
            assert os.getcwd() == cwd_before
            # The saved pwd landed on the agent's executor workspace.
            assert str(agent.executor._working_dir) == str(workdir.resolve())
        finally:
            store.close()

    def test_missing_saved_workspace_requires_explicit_replacement(
        self, monkeypatch, tmp_path, patched_llm
    ):
        config_dir = tmp_path / "creature"
        _write_agent_config(config_dir)
        missing = tmp_path / "deleted-workspace"
        path = tmp_path / "missing-workspace.kohakutr.v2"
        store = SessionStore(str(path))
        try:
            store.meta["format_version"] = 2
            store.init_meta("sess", "agent", str(config_dir), str(missing), ["resumee"])
            store.flush()
        finally:
            store.close()

        import kohakuterrarium.session.resume as resume_mod

        real_open = resume_mod._open_store_with_migration
        writer_calls = []

        def tracking_open(session_path, *, writer_lock=False):
            if writer_lock:
                writer_calls.append(str(session_path))
            return real_open(session_path, writer_lock=writer_lock)

        monkeypatch.setattr(resume_mod, "_open_store_with_migration", tracking_open)
        with pytest.raises(
            SessionNotResumableError, match="Choose a replacement directory"
        ):
            resume_agent(path)
        assert writer_calls == []

        agent, resumed = resume_agent(path, pwd_override=str(tmp_path))
        try:
            assert str(agent.executor._working_dir) == str(tmp_path.resolve())
        finally:
            resumed.close()

    def test_rejects_non_agent_session(self, tmp_path, patched_llm):
        config_dir = tmp_path / "creature"
        _write_agent_config(config_dir)
        path = self._make_session(tmp_path, config_dir, config_type="terrarium")
        # A terrarium session must not resume through the agent path.
        with pytest.raises(ValueError, match="terrarium"):
            resume_agent(path)

    def test_terrarium_error_points_to_modern_api(self, tmp_path, patched_llm):
        # The actionable error must name an entry point that ACTUALLY
        # exists today. The legacy ``terrarium.legacy_resume`` module
        # was deleted; pointing users there is a dead end.
        config_dir = tmp_path / "creature"
        _write_agent_config(config_dir)
        path = self._make_session(tmp_path, config_dir, config_type="terrarium")
        with pytest.raises(ValueError) as excinfo:
            resume_agent(path)
        message = str(excinfo.value)
        # Must NOT reference the removed legacy module.
        assert "legacy_resume" not in message
        # Must point at one of the real modern entry points.
        assert (
            "Terrarium.resume" in message
            or "adopt_session" in message
            or "resume_into_engine" in message
        )

    def test_recipe_spawned_single_creature_meta_resumes_as_agent(
        self, tmp_path, patched_llm
    ):
        # Worker-spawned recipes that produced ONE creature have
        # ``config_type="agent"`` (the worker's ``_ensure_store_meta``
        # path) but ALSO carry a recipe-style ``agents`` list. Resume
        # must treat this as an agent session and rebuild successfully.
        config_dir = tmp_path / "creature"
        _write_agent_config(config_dir)
        path = self._make_session(tmp_path, config_dir, config_type="agent")
        agent, store = resume_agent(path)
        try:
            assert agent is not None
            assert agent.config.name == "resumee"
        finally:
            store.close()

    def test_missing_config_type_treated_as_agent(self, tmp_path, patched_llm):
        # Mirror files that did not receive a meta sync before the
        # controller pushed them back to a worker can land with
        # ``config_type`` missing/None. ``detect_session_type`` defaults
        # such files to "agent"; ``resume_agent`` must agree so the
        # worker doesn't 502 with "Session is a None, not an agent".
        config_dir = tmp_path / "creature"
        _write_agent_config(config_dir)
        path = tmp_path / "sess.kohakutr.v2"
        s = SessionStore(str(path))
        try:
            s.meta["format_version"] = 2
            # Deliberately skip init_meta — emulate an un-synced mirror.
            s.meta["session_id"] = "sess"
            s.meta["config_path"] = str(config_dir)
            s.meta["pwd"] = str(tmp_path)
            s.meta["agents"] = ["resumee"]
            s.flush()
        finally:
            s.close()
        agent, store = resume_agent(path)
        try:
            assert agent is not None
            assert agent.config.name == "resumee"
        finally:
            store.close()

    def test_missing_config_path_raises(self, tmp_path, patched_llm):
        path = tmp_path / "sess.kohakutr.v2"
        s = SessionStore(str(path))
        try:
            s.meta["format_version"] = 2
            # config_path deliberately empty.
            s.init_meta("sess", "agent", "", str(tmp_path), ["resumee"])
        finally:
            s.close()
        with pytest.raises(ValueError, match="no config_path"):
            resume_agent(path)

    def test_failed_resume_releases_writer_lock(
        self, tmp_path, patched_llm, monkeypatch
    ):
        # A resume that fails after opening the store (here: invalid meta
        # with no config_path/snapshot) must close it, releasing the
        # writer lock — otherwise the .kohakutr can never be re-opened by
        # a fresh writer (and on Windows the file itself can't be removed).
        import kohakuterrarium.session.resume as resume_mod

        path = tmp_path / "sess.kohakutr.v2"
        s = SessionStore(str(path))
        try:
            s.meta["format_version"] = 2
            s.init_meta("sess", "agent", "", str(tmp_path), ["resumee"])
        finally:
            s.close()
        # Hold a reference to the opened store so a leaked handle can't be
        # GC-collected before the assertion (that would release the OS
        # lock and mask the bug).
        opened = {}
        real_open = resume_mod._open_store_with_migration

        def _capture_open(p, **kw):
            store = real_open(p, **kw)
            opened["store"] = store
            return store

        monkeypatch.setattr(resume_mod, "_open_store_with_migration", _capture_open)
        with pytest.raises(ValueError, match="no config_path"):
            resume_agent(path)
        # The store was closed → its writer lock released.
        assert getattr(opened["store"], "_closed", False) is True
        reopened = SessionStore(str(path), writer_lock=True)
        reopened.close()

    def test_io_mode_override_builds_modules(self, tmp_path, patched_llm):
        # Passing io_mode="plain" makes resume build + wire the plain
        # CLI input + stdout output instead of the config defaults.
        config_dir = tmp_path / "creature"
        _write_agent_config(config_dir)
        path = self._make_session(tmp_path, config_dir)
        agent, store = resume_agent(path, io_mode="plain")
        try:
            assert agent.config.name == "resumee"
        finally:
            store.close()

    def test_crowded_resume_elides_stale_tool_results(self, tmp_path):
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            big = "y" * 1_000_000
            store.save_conversation(
                "alice",
                [
                    {"role": "user", "content": "q1"},
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "c1",
                                "function": {
                                    "name": "read",
                                    "arguments": '{"path": "big.py"}',
                                },
                            }
                        ],
                    },
                    {"role": "tool", "tool_call_id": "c1", "content": big},
                    {"role": "assistant", "content": "reading done"},
                    {"role": "user", "content": "q2"},
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "c2",
                                "function": {
                                    "name": "grep",
                                    "arguments": '{"pattern": "x"}',
                                },
                            }
                        ],
                    },
                    {"role": "tool", "tool_call_id": "c2", "content": "small"},
                    {"role": "assistant", "content": "latest"},
                ],
            )
            agent = _FakeAgentForInject()
            agent.config = _ElideConfig()
            agent.controller.config = _ElideConfig()
            inject_saved_state(agent, store, "alice")
            from kohakuterrarium.core.conversation_elide import ELISION_MARKER

            contents = [
                m.get("content", "")
                for m in agent.controller.conversation.to_messages()
            ]
            # The 1MB tool result from the earlier round is stubbed out.
            assert any(isinstance(c, str) and ELISION_MARKER in c for c in contents)
            # The latest feedback round stays verbatim.
            assert "small" in contents
        finally:
            store.close()

    def test_spacious_resume_keeps_tool_results_verbatim(self, tmp_path):
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            store.save_conversation(
                "alice",
                [
                    {"role": "user", "content": "q1"},
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "c1",
                                "function": {"name": "read", "arguments": "{}"},
                            }
                        ],
                    },
                    {"role": "tool", "tool_call_id": "c1", "content": "small"},
                    {"role": "assistant", "content": "done"},
                ],
            )
            agent = _FakeAgentForInject()
            agent.config = _ElideConfig()
            agent.controller.config = _ElideConfig()
            inject_saved_state(agent, store, "alice")
            contents = [
                m.get("content", "")
                for m in agent.controller.conversation.to_messages()
            ]
            assert "small" in contents
        finally:
            store.close()


# -- detect_session_type defensive path ---------------------------


class TestDetectSessionTypeDefensive:
    def test_unmigratable_file_still_probed_directly(self, tmp_path, monkeypatch):
        # If ensure_latest_version raises, detect_session_type falls
        # back to probing the raw path rather than propagating.
        import kohakuterrarium.session.resume as resume_mod

        path = tmp_path / "s.kohakutr"
        s = SessionStore(str(path))
        try:
            s.meta["format_version"] = 2
            s.init_meta("s", "agent", "/p", "/w", ["a"])
        finally:
            s.close()

        def _boom(p):
            raise ValueError("cannot migrate")

        monkeypatch.setattr(resume_mod, "ensure_latest_version", _boom)
        # Falls back to the raw path -> still reports the stored type.
        assert detect_session_type(path) == "agent"

    def test_backfill_trusts_snapshot_not_replay(self, tmp_path):
        # The snapshot is authoritative (it may hold a post-compact view or
        # in-flight messages that never reached the event log). Backfill must
        # keep snapshot content verbatim and only attach turn metadata.
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            _, eid = store.append_event(
                "alice",
                "user_message",
                {"content": "real-event-content"},
                turn_index=3,
                branch_id=1,
            )
            store.save_conversation(
                "alice",
                [
                    {"role": "user", "content": "compact-summary"},
                    {"role": "assistant", "content": "ok"},
                    {"role": "user", "content": "latest-turn"},
                ],
            )
            store.state["alice:snapshot_event_id"] = eid
            store.flush()
            out = _load_conversation_with_replay_fallback(store, "alice")
            assert out[0]["content"] == "compact-summary"  # verbatim, not replay
            assert out[0]["metadata"]["turn_index"] == 3
            assert out[0]["metadata"]["branch_id"] == 1
            assert [m["content"] for m in out] == [
                "compact-summary",
                "ok",
                "latest-turn",
            ]
        finally:
            store.close()

    def test_backfill_maps_snapshot_users_to_last_events(self, tmp_path):
        # Compaction keeps only the live zone verbatim, so snapshot user
        # messages are the MOST RECENT turns, not the first ones. Backfill
        # must map them to the last user_message events, or edit targeting
        # would resolve the wrong turn.
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            for i in range(1, 6):
                store.append_event(
                    "alice",
                    "user_message",
                    {"content": "turn-%d" % i},
                    turn_index=i,
                    branch_id=1,
                )
            store.save_conversation(
                "alice",
                [
                    {"role": "system", "content": "sys"},
                    {
                        "role": "assistant",
                        "content": "[Previous context summary (compact round 2)]\n",
                    },
                    {"role": "user", "content": "turn-4"},
                    {"role": "user", "content": "turn-5"},
                ],
            )
            store.flush()
            out = _load_conversation_with_replay_fallback(store, "alice")
            user_meta = [
                m["metadata"]["turn_index"] for m in out if m.get("role") == "user"
            ]
            assert user_meta == [4, 5]
        finally:
            store.close()

    def test_backfill_preserves_metadata_less_user(self, tmp_path):
        # When the event log has fewer user turns than the snapshot, trailing
        # user messages simply stay without metadata instead of erroring.
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            store.append_event(
                "alice",
                "user_message",
                {"content": "x"},
                turn_index=1,
                branch_id=1,
            )
            store.save_conversation(
                "alice",
                [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}],
            )
            store.flush()
            out = _load_conversation_with_replay_fallback(store, "alice")
            assert out[0]["metadata"]["turn_index"] == 1
            meta1 = out[1].get("metadata")
            assert meta1 is None or meta1.get("turn_index") is None
        finally:
            store.close()
