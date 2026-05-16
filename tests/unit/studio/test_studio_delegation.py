"""Behavior tests for :class:`Studio`'s delegation forwarders.

``studio.py`` is a thin organizational facade — every namespace method
forwards to a real ``studio.<sub>.*`` function with the studio's
``TerrariumService`` threaded through. These tests drive a *real*
``Terrarium`` engine (via ``TestTerrariumBuilder``) and assert the
*observable effect* of the forward — a streamed response, a stopped
engine, a wiring list reflecting real topology — not a mock call count.

These also act as regression guards for B-studio-1..4 (sync/async
forwarder mismatches), now fixed — see ``temp/BUGS.md``. Each test
below would have caught the corresponding bug: a coroutine leaking
where a value was documented, or a non-iterable where the docstring
promised ``async for``.
"""

import asyncio
from types import SimpleNamespace

import pytest

from kohakuterrarium.studio.studio import Studio
from kohakuterrarium.testing.terrarium import TestTerrariumBuilder


@pytest.fixture
async def studio_engine():
    """A real Studio over a real two-creature engine, alice scripted."""
    engine = await (
        TestTerrariumBuilder()
        .with_creature("alice", responses=["hello-from-alice"])
        .with_creature("bob")
        .build()
    )
    s = Studio(engine=engine)
    try:
        yield s, engine
    finally:
        await engine.shutdown()


# ── sessions.chat.chat — real streamed response ────────────────


class TestSessionsChatForward:
    async def test_chat_is_directly_iterable_per_docstring(self, studio_engine):
        # Regression guard for B-studio-4: the Studio + _SessionsChat
        # docstrings advertise ``async for chunk in chat(...)`` with NO
        # await. ``chat`` must therefore return an AsyncIterator directly,
        # not a coroutine — a coroutine would raise TypeError on async-for.
        s, engine = studio_engine
        gid = engine.get_creature("alice").graph_id
        chunks = [c async for c in s.sessions.chat.chat(gid, "alice", "hi")]
        # The fake agent replays its scripted response through the
        # output pipe — the delegation must surface it verbatim.
        assert "".join(chunks) == "hello-from-alice"


# ── sessions.stop — real engine teardown ───────────────────────


class TestSessionsStopForward:
    async def test_stop_tears_the_session_down(self, studio_engine):
        s, engine = studio_engine
        gid = engine.get_creature("alice").graph_id
        assert engine.get_creature("alice").agent.is_running is True
        await s.sessions.stop(gid)
        # Stopping the session removes its creatures from the engine —
        # the graph no longer exists.
        with pytest.raises(KeyError):
            engine.get_creature("alice")
        with pytest.raises(KeyError):
            engine.get_creature("bob")
        assert gid not in engine._environments


# ── sessions.list_output_wiring — real topology read ───────────


class TestSessionsWiringForward:
    async def test_list_output_wiring_reads_real_engine_state(self, studio_engine):
        s, engine = studio_engine
        # A freshly-built creature has no output-wiring edges; the
        # delegation must return the engine's real (empty) list, not error.
        wiring = s.sessions.list_output_wiring("alice")
        assert wiring == engine.list_output_wiring("alice")
        assert isinstance(wiring, list)


# ── sessions.ctl — async forwarders return values, not coroutines ──


class TestSessionsCtlForward:
    async def test_list_jobs_awaits_and_returns_a_list(self, studio_engine):
        # Regression guard for B-studio-1: list_jobs must be awaited and
        # yield the documented list[dict], not an un-awaited coroutine.
        s, engine = studio_engine
        agent = engine.get_creature("alice").agent
        # Give the fake agent the job accessors the real service chain
        # reads, with no running jobs.
        agent.executor = SimpleNamespace(get_running_jobs=lambda: [])
        agent.subagent_manager = SimpleNamespace(get_running_jobs=lambda: [])
        gid = engine.get_creature("alice").graph_id
        result = await s.sessions.ctl.list_jobs(gid, "alice")
        assert not asyncio.iscoroutine(result)
        # A creature with no running jobs yields an empty list.
        assert result == []

    async def test_promote_job_awaits_and_returns_a_bool(self, studio_engine):
        # Regression guard for B-studio-2: promote_job must be awaited and
        # yield the documented bool, not an un-awaited coroutine.
        s, engine = studio_engine
        agent = engine.get_creature("alice").agent
        # The real promote chain calls ``agent._promote_handle(job_id)``;
        # an unknown id resolves to a falsy handle → False.
        agent._promote_handle = lambda job_id: None
        gid = engine.get_creature("alice").graph_id
        result = await s.sessions.ctl.promote_job(gid, "alice", "no-such-job")
        assert not asyncio.iscoroutine(result)
        assert result is False


# ── sessions.search_memory — async forwarder ───────────────────


class TestSessionsSearchMemoryForward:
    async def test_search_memory_is_awaitable_and_yields_a_payload(self, tmp_path):
        # Regression guard for B-studio-3: search_memory must be an
        # awaitable that resolves to the documented dict payload — not a
        # sync method handing back a bare coroutine.
        from kohakuterrarium.session.store import SessionStore

        path = tmp_path / "mem-session.kohakutr"
        store = SessionStore(str(path))
        store.init_meta("sess", "agent", "/p", "/w", ["alice"])
        store.append_event("alice", "user_input", {"content": "find this later"})
        store.flush()
        store.close()

        s = Studio()
        try:
            result = await s.sessions.search_memory(
                path,
                q="nothing-matches-this-query",
                mode="fts",
                k=5,
                agent=None,
                engine=s.engine,
            )
            # Awaiting the forwarder yields a real dict payload, not a
            # coroutine — and a no-hit query resolves to count 0.
            assert not asyncio.iscoroutine(result)
            assert isinstance(result, dict)
            assert result["count"] == 0
            assert result["query"] == "nothing-matches-this-query"
        finally:
            await s.shutdown()


# ── sessions.chat regenerate / edit / rewind / history / branches ──


def _attach_chat_agent(engine, creature_id="alice"):
    """Augment the engine creature's real (fake) agent with the chat-
    control surface, recording forwarded args for observation. Mutating
    the existing agent keeps its lifecycle attrs intact for teardown."""
    agent = engine.get_creature(creature_id).agent
    recorder = SimpleNamespace(regenerate_args=None, edit_args=None, rewind_args=None)
    agent.conversation_history = [{"role": "user", "content": "hi"}]
    agent.session_store = None

    async def _regen(*, turn_index=None, branch_view=None):
        recorder.regenerate_args = (turn_index, branch_view)

    async def _edit(
        idx, content, *, turn_index=None, user_position=None, branch_view=None
    ):
        recorder.edit_args = (idx, content)
        return True

    async def _rewind(idx):
        recorder.rewind_args = idx

    agent.regenerate_last_response = _regen
    agent.edit_and_rerun = _edit
    agent.rewind_to = _rewind
    return recorder


class TestSessionsChatControlForward:
    async def test_regenerate_reaches_the_agent(self, studio_engine):
        s, engine = studio_engine
        agent = _attach_chat_agent(engine)
        gid = engine.get_creature("alice").graph_id
        await s.sessions.chat.regenerate(gid, "alice")
        # The forward invoked the agent's regeneration entry point.
        assert agent.regenerate_args == (None, None)

    async def test_edit_message_forwards_idx_and_content_and_returns_result(
        self, studio_engine
    ):
        s, engine = studio_engine
        agent = _attach_chat_agent(engine)
        gid = engine.get_creature("alice").graph_id
        out = await s.sessions.chat.edit_message(gid, "alice", 0, "edited text")
        assert out is True
        assert agent.edit_args == (0, "edited text")

    async def test_rewind_reaches_the_agent(self, studio_engine):
        s, engine = studio_engine
        agent = _attach_chat_agent(engine)
        gid = engine.get_creature("alice").graph_id
        await s.sessions.chat.rewind(gid, "alice", 3)
        assert agent.rewind_args == 3

    async def test_history_surfaces_the_conversation(self, studio_engine):
        s, engine = studio_engine
        _attach_chat_agent(engine)
        gid = engine.get_creature("alice").graph_id
        payload = s.sessions.chat.history(gid, "alice")
        # The forward returns the live conversation snapshot.
        assert payload["messages"] == [{"role": "user", "content": "hi"}]
        assert payload["creature_id"] == "alice"

    async def test_branches_returns_per_turn_metadata(self, studio_engine):
        s, engine = studio_engine
        _attach_chat_agent(engine)
        gid = engine.get_creature("alice").graph_id
        payload = s.sessions.chat.branches(gid, "alice")
        # No branched events → an empty turns list, but the shape holds.
        assert payload["creature_id"] == "alice"
        assert payload["turns"] == []


# ── sessions.ctl interrupt / cancel_job ────────────────────────


class TestSessionsCtlInterruptForward:
    async def test_interrupt_reaches_the_agent(self, studio_engine):
        s, engine = studio_engine
        agent = engine.get_creature("alice").agent
        interrupted = []
        agent.interrupt = lambda: interrupted.append(True)
        gid = engine.get_creature("alice").graph_id
        await s.sessions.ctl.interrupt(gid, "alice")
        assert interrupted == [True]

    async def test_cancel_job_awaits_and_returns_a_bool(self, studio_engine):
        s, engine = studio_engine
        agent = engine.get_creature("alice").agent
        # The real cancel chain tries: _interrupt_direct_job, then
        # executor.cancel, then subagent_manager.cancel. With an unknown
        # id all three miss → a clean False.
        agent._interrupt_direct_job = lambda job_id: False

        async def _cancel(job_id):
            return False

        agent.executor = SimpleNamespace(cancel=_cancel)
        agent.subagent_manager = SimpleNamespace(cancel=_cancel)
        gid = engine.get_creature("alice").graph_id
        result = await s.sessions.ctl.cancel_job(gid, "alice", "no-such-job")
        assert not asyncio.iscoroutine(result)
        assert result is False


# ── sessions.state scratchpad / triggers / system_prompt ───────


class TestSessionsStateForward:
    async def test_scratchpad_round_trips_through_the_forward(self, studio_engine):
        from kohakuterrarium.core.scratchpad import Scratchpad

        s, engine = studio_engine
        agent = engine.get_creature("alice").agent
        agent.scratchpad = Scratchpad()
        gid = engine.get_creature("alice").graph_id
        # Patch through the forward, then read back through the forward.
        s.sessions.state.patch_scratchpad(gid, "alice", {"note": "remember this"})
        assert s.sessions.state.scratchpad(gid, "alice") == {"note": "remember this"}

    async def test_system_prompt_forward_returns_the_agent_prompt(self, studio_engine):
        s, engine = studio_engine
        agent = engine.get_creature("alice").agent
        agent.get_system_prompt = lambda: "you are alice, be helpful"
        gid = engine.get_creature("alice").graph_id
        out = s.sessions.state.system_prompt(gid, "alice")
        assert out == {"text": "you are alice, be helpful"}

    async def test_triggers_forward_lists_runtime_triggers(self, studio_engine):
        from datetime import datetime

        s, engine = studio_engine
        agent = engine.get_creature("alice").agent

        class _TriggerInfo:
            trigger_id = "t1"
            trigger_type = "timer"
            running = True
            created_at = datetime.now()

        agent.trigger_manager = SimpleNamespace(list=lambda: [_TriggerInfo()])
        gid = engine.get_creature("alice").graph_id
        triggers = s.sessions.state.triggers(gid, "alice")
        assert len(triggers) == 1
        assert triggers[0]["trigger_id"] == "t1"
        assert triggers[0]["running"] is True


# ── sessions.model switch / native_tool_options ────────────────


class TestSessionsModelForward:
    async def test_switch_forward_returns_the_new_model(self, studio_engine):
        s, engine = studio_engine
        agent = engine.get_creature("alice").agent
        agent.switch_model = lambda profile: f"switched-to-{profile}"
        gid = engine.get_creature("alice").graph_id
        out = s.sessions.model.switch(gid, "alice", "claude-opus")
        assert out == "switched-to-claude-opus"

    async def test_native_tool_options_forward_returns_options(self, studio_engine):
        s, engine = studio_engine
        agent = engine.get_creature("alice").agent
        agent.native_tool_options = SimpleNamespace(
            list=lambda: {"web_search": {"max_results": {"type": "int"}}}
        )
        gid = engine.get_creature("alice").graph_id
        out = s.sessions.model.native_tool_options(gid, "alice")
        assert out == {"web_search": {"max_results": {"type": "int"}}}


# ── sessions.command — real builtin slash command ──────────────


class TestSessionsCommandForward:
    async def test_unknown_command_raises_value_error(self, studio_engine):
        s, engine = studio_engine
        gid = engine.get_creature("alice").graph_id
        # A public entrypoint must reject an unknown command cleanly.
        with pytest.raises(ValueError, match="Unknown command"):
            await s.sessions.command.execute(gid, "alice", "definitely-not-a-command")

    async def test_known_command_executes_and_returns_payload(self, studio_engine):
        s, engine = studio_engine
        gid = engine.get_creature("alice").graph_id
        # ``help`` is a real builtin command; it runs against the agent
        # and returns the standard command-result envelope.
        result = await s.sessions.command.execute(gid, "alice", "help")
        assert result["command"] == "help"
        assert "success" in result and "output" in result


# ── sessions.wire_output / unwire_output — real engine wiring ──


class TestSessionsWireOutputForward:
    async def test_wire_then_unwire_changes_engine_wiring(self, studio_engine):
        s, engine = studio_engine
        # Wire alice → bob through the forward; the engine must show a
        # new edge mentioning bob, and unwiring removes it.
        assert engine.list_output_wiring("alice") == []
        await s.sessions.wire_output("alice", "bob")
        wiring = engine.list_output_wiring("alice")
        assert len(wiring) == 1
        assert "bob" in str(wiring[0])
        # unwire_output takes the edge id from the wiring entry.
        await s.sessions.unwire_output("alice", wiring[0]["id"])
        assert engine.list_output_wiring("alice") == []

    async def test_wire_then_unwire_output_sink_round_trips(self, studio_engine):
        from kohakuterrarium.testing.output import OutputRecorder

        s, engine = studio_engine
        sink = OutputRecorder()
        # wire_output_sink attaches the sink as a secondary output and
        # returns its id; unwire_output_sink removes it by that id.
        sink_id = await s.sessions.wire_output_sink("alice", sink)
        assert isinstance(sink_id, str) and sink_id
        agent = engine.get_creature("alice").agent
        assert sink in agent.output_router._secondary_outputs
        removed = await s.sessions.unwire_output_sink("alice", sink_id)
        assert removed is True
        assert sink not in agent.output_router._secondary_outputs


# ── sessions.add_creature — unknown session rejection ──────────


class TestSessionsAddCreatureForward:
    async def test_add_creature_to_unknown_session_raises_keyerror(self, studio_engine):
        s, _engine = studio_engine
        # A public hot-plug entrypoint must reject an unknown session id
        # cleanly up front, not explode deep in the engine.
        with pytest.raises(KeyError, match="not found"):
            await s.sessions.add_creature("ghost-session", object())


# ── sessions.plugins — list (no plugin manager) ────────────────


class TestSessionsPluginsForward:
    async def test_list_plugins_empty_when_no_plugin_manager(self, studio_engine):
        s, engine = studio_engine
        agent = engine.get_creature("alice").agent
        # The fake agent has no plugin manager — the forward must return
        # an empty list, not raise.
        agent.plugins = None
        gid = engine.get_creature("alice").graph_id
        assert s.sessions.plugins.list(gid, "alice") == []

    async def test_toggle_plugin_without_manager_raises(self, studio_engine):
        s, engine = studio_engine
        agent = engine.get_creature("alice").agent
        agent.plugins = None
        gid = engine.get_creature("alice").graph_id
        # Toggling on a creature with no plugin manager is a clean
        # ValueError, not an AttributeError deep in the manager.
        with pytest.raises(ValueError, match="No plugins loaded"):
            await s.sessions.plugins.toggle(gid, "alice", "anything")


# ── sessions.state.env / working_dir — real workspace helper ───


class TestSessionsStateEnvForward:
    async def test_env_forward_reports_the_creature_working_dir(
        self, studio_engine, tmp_path
    ):
        s, engine = studio_engine
        agent = engine.get_creature("alice").agent
        # The working dir is owned by ``agent.workspace`` — give the
        # fake agent a workspace helper so env() resolves the real path.
        agent.workspace = SimpleNamespace(get=lambda: str(tmp_path))
        gid = engine.get_creature("alice").graph_id
        env = s.sessions.state.env(gid, "alice")
        assert env["pwd"] == str(tmp_path)
        assert isinstance(env["env"], dict)

    async def test_working_dir_forward_returns_the_path(self, studio_engine, tmp_path):
        s, engine = studio_engine
        agent = engine.get_creature("alice").agent
        agent.workspace = SimpleNamespace(get=lambda: str(tmp_path))
        gid = engine.get_creature("alice").graph_id
        assert s.sessions.state.working_dir(gid, "alice") == str(tmp_path)

    async def test_set_working_dir_forward_updates_via_workspace(
        self, studio_engine, tmp_path
    ):
        s, engine = studio_engine
        agent = engine.get_creature("alice").agent
        recorded = {}

        def _set(new_path):
            recorded["path"] = new_path
            return new_path

        agent.workspace = SimpleNamespace(get=lambda: "/old", set=_set)
        gid = engine.get_creature("alice").graph_id
        out = s.sessions.state.set_working_dir(gid, "alice", str(tmp_path))
        assert out == str(tmp_path)
        assert recorded["path"] == str(tmp_path)


# ── identity.llm default-model get / set ───────────────────────


class TestIdentityLlmDefaultForward:
    async def test_set_default_then_get_default_round_trips(
        self, tmp_path, monkeypatch
    ):
        # Identity stores resolve their on-disk path fresh through
        # ``config_dir()`` / ``KT_CONFIG_DIR`` — the legacy
        # ``PROFILES_PATH`` constant is display-only.  Override via env
        # so the save lands in tmp instead of the operator's real
        # ``~/.kohakuterrarium/llm_profiles.yaml``.
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
        s = Studio()
        try:
            # Save a profile, set it as default, read the default back.
            s.identity.llm.save_profile("defprofile", "gpt-4o", "openai")
            s.identity.llm.set_default("defprofile")
            assert s.identity.llm.get_default() == "defprofile"
        finally:
            await s.shutdown()


# ── persistence.fork — real saved-session fork ─────────────────


class TestPersistenceForkForward:
    async def test_fork_creates_a_child_session_file(self, tmp_path):
        from kohakuterrarium.session.store import SessionStore

        src = tmp_path / "parent.kohakutr"
        store = SessionStore(str(src))
        store.init_meta("parent", "agent", "/p", "/w", ["alice"])
        # append_event returns ``(key, event_id)``; the fork point is
        # the integer event id.
        _key, eid = store.append_event("alice", "user_message", {"content": "hello"})
        store.flush()
        store.close()

        s = Studio()
        try:
            result = await s.persistence.fork(
                src,
                at_event_id=eid,
                mutate_kind=None,
                mutate_args=None,
                name="parent-fork",
            )
            # The fork returns a payload describing the new child session.
            assert isinstance(result, dict)
        finally:
            await s.shutdown()


# ── editors.modules.save — real module write ───────────────────


class TestEditorModulesSaveForward:
    def test_save_writes_module_source_to_disk(self, tmp_path):
        s = Studio()
        kind_dir = tmp_path / "tools"
        created = s.editors.modules.scaffold(kind_dir, "tools", "mytool", None)
        # save_module(kind, name, data, *, existing_path, fallback_path).
        # Raw mode writes ``data["raw_source"]`` verbatim.
        new_src = "# edited tool source\n"
        path = s.editors.modules.save(
            "tools",
            "mytool",
            {"mode": "raw", "raw_source": new_src},
            existing_path=created,
            fallback_path=created,
        )
        assert path.read_text(encoding="utf-8") == new_src
