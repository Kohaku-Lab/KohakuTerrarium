"""Unit tests for :mod:`kohakuterrarium.session.resume`.

The full ``resume_agent`` path is exercised with a real agent config
on disk plus a ``ScriptedLLM`` injected via the monkeypatched LLM
bootstrap, so resumption is tested end-to-end without a live provider.
"""

import pytest

from kohakuterrarium.bootstrap import agent_init as _agent_init
from kohakuterrarium.bootstrap import llm as _bootstrap_llm
from kohakuterrarium.core.conversation import Conversation
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

    def _fake_create(config, llm_override=None):
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

    def test_snapshot_fresh_returns_snapshot(self, tmp_path):
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            _, eid = store.append_event("alice", "x", {})
            store.save_conversation("alice", [{"role": "user", "content": "snap"}])
            store.state["alice:snapshot_event_id"] = eid
            store.flush()
            out = _load_conversation_with_replay_fallback(store, "alice")
            assert out[0]["content"] == "snap"
        finally:
            store.close()

    def test_snapshot_stale_falls_back_to_replay(self, tmp_path):
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            store.append_event("alice", "user_message", {"content": "fresh"})
            store.append_event("alice", "user_message", {"content": "newer"})
            # Snapshot is older than the last event.
            store.save_conversation("alice", [{"role": "user", "content": "stale"}])
            store.state["alice:snapshot_event_id"] = 1
            store.flush()
            out = _load_conversation_with_replay_fallback(store, "alice")
            # Replay rebuilds from events; "fresh" and "newer" appear.
            contents = [m["content"] for m in out if m["role"] == "user"]
            assert "fresh" in contents
            assert "newer" in contents
        finally:
            store.close()

    def test_missing_snapshot_event_id_uses_snapshot(self, tmp_path):
        # When there's a snapshot but no recorded snapshot_event_id,
        # the snapshot is trusted (avoid false-positive replays).
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            store.append_event("alice", "user_message", {"content": "x"})
            store.save_conversation("alice", [{"role": "user", "content": "snap"}])
            store.flush()
            out = _load_conversation_with_replay_fallback(store, "alice")
            assert out[0]["content"] == "snap"
        finally:
            store.close()

    def test_state_get_raising_treated_as_no_cache(self, tmp_path, monkeypatch):
        # If reading the cached snapshot_event_id from store.state raises
        # (TypeError / KeyError), the helper treats it as "no cache" and
        # falls back to trusting the snapshot rather than crashing.
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            store.append_event("alice", "user_message", {"content": "x"})
            store.save_conversation("alice", [{"role": "user", "content": "snap"}])
            store.flush()

            def _boom_get(key, default=None):
                raise TypeError("state backend exploded")

            monkeypatch.setattr(store.state, "get", _boom_get)
            out = _load_conversation_with_replay_fallback(store, "alice")
            # cached_up_to is None → snapshot is trusted.
            assert out[0]["content"] == "snap"
        finally:
            store.close()


# ── detect_session_type ──────────────────────────────────────────


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


class _FakeAgentForInject:
    def __init__(self):
        self.config = _FakeConfig()
        self.controller = _FakeController()
        self.session = _FakeAgentSession()
        self.executor = _FakeNamed()
        self.trigger_manager = _FakeNamed()
        self.compact_manager = _FakeNamed()
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
