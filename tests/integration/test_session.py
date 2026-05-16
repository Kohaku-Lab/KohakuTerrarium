"""Integration test for the ``session/`` package.

This is the comprehensive USAGE EXAMPLE of session persistence. Each
method runs a COMPLETE workflow end-to-end — never a granular
per-method check. The seam is the LLM only: a ``ScriptedLLM`` is
injected via both bootstrap entry points; everything else (the real
``SessionStore`` writing real ``.kohakutr`` files, a real ``Agent``
attached to it via ``attach_session_store``, the real ``resume_agent``
path, ``SessionMemory`` over a deterministic embedder, copy-on-fork,
v1→v2 migration) is the production collaborator.

The workflows mirror how the codebase actually drives session:
``studio/sessions/lifecycle.py`` builds a ``SessionStore``, calls
``init_meta``, then ``agent.attach_session_store(store)``; the agent's
``SessionOutput`` sink records every turn; ``session/resume.py`` rebuilds
the agent from the ``config_path`` in meta and re-injects state;
``studio/sessions/memory_search.py`` indexes events and searches.
"""

from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from kohakuterrarium.bootstrap import agent_init as _agent_init
from kohakuterrarium.bootstrap import llm as _bootstrap_llm
from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.events import create_user_input_event
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from kohakuterrarium.session.attachment_service import get_attach_state
from kohakuterrarium.session.embedding import BaseEmbedder
from kohakuterrarium.session.errors import (
    AlreadyAttachedError,
    ForkNotStableError,
    NotAttachedError,
)
from kohakuterrarium.session.session import Session
from kohakuterrarium.session.history import (
    collect_branch_metadata,
    collect_user_groups,
    normalize_resumable_events,
    replay_conversation,
)
from kohakuterrarium.session.memory import SessionMemory
from kohakuterrarium.session.migrations import (
    discover_versions,
    ensure_latest_version,
    migrate,
    migration_marker,
    path_for_version,
)
from kohakuterrarium.session.resume import detect_session_type, resume_agent
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.session.version import FORMAT_VERSION, detect_format_version
from kohakuterrarium.testing.llm import ScriptedLLM

pytestmark = pytest.mark.timeout(30)


# ── deterministic collaborators ──────────────────────────────────


class _HashEmbedder(BaseEmbedder):
    """Deterministic embedder: a stable hashed bag-of-words vector.

    Real ``Model2VecEmbedder`` needs a downloaded model; this stand-in
    is fully deterministic so a vector search asserts an EXACT hit.
    Identical text maps to an identical vector, so an exact-phrase
    query is its own nearest neighbour.
    """

    dimensions = 64

    def encode(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dimensions), dtype=np.float32)
        for row, text in enumerate(texts):
            for token in text.lower().split():
                idx = hash(token) % self.dimensions
                out[row, idx] += 1.0
            norm = float(np.linalg.norm(out[row]))
            if norm > 0:
                out[row] /= norm
        return out


class _EchoTool(BaseTool):
    """A direct tool the scripted LLM can call to produce tool events."""

    @property
    def tool_name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "echo a message back"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args, **kwargs) -> ToolResult:
        # The bracket parser maps a tool block's body onto ``content``.
        return ToolResult(output=f"echoed: {args.get('content', '')}")


# ── shared fixtures ──────────────────────────────────────────────


@pytest.fixture
def patched_llm(monkeypatch):
    """Inject a ScriptedLLM into BOTH bootstrap entry points.

    ``Agent.from_path`` / ``Agent(...)`` build the provider via
    ``bootstrap.llm.create_llm_provider``; the controller side imports
    it through ``bootstrap.agent_init``. Both must be patched or a
    resumed agent reaches for a real provider.
    """

    holder: dict[str, list] = {"script": ["OK"]}

    def _fake_create(config, llm_override=None):
        return ScriptedLLM(holder["script"])

    monkeypatch.setattr(_bootstrap_llm, "create_llm_provider", _fake_create)
    monkeypatch.setattr(_agent_init, "create_llm_provider", _fake_create)
    return holder


def _write_agent_config(config_dir, name: str = "scribe") -> str:
    """Write a minimal but complete creature config dir. Returns the path."""
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.yaml").write_text(
        f"name: {name}\n"
        "controller:\n"
        "  tool_format: bracket\n"
        "  include_tools_in_prompt: false\n"
        "  include_hints_in_prompt: false\n"
        "system_prompt: |\n"
        "  You are a test scribe.\n"
        "input:\n"
        "  type: none\n"
        "output:\n"
        "  type: stdout\n"
    )
    return str(config_dir)


def _new_store(path, *, config_path: str, agents: list[str]) -> SessionStore:
    """Create + init a store the way ``studio/sessions/lifecycle.py`` does."""
    store = SessionStore(str(path))
    store.init_meta(
        session_id=path.stem,
        config_type="agent",
        config_path=config_path,
        pwd=str(path.parent),
        agents=agents,
    )
    return store


class TestSessionIntegration:
    """Each method = one full session workflow, start to finish."""

    async def test_run_record_close_reopen_resume(self, patched_llm, tmp_path):
        """The canonical session integration workflow.

        init store → real Agent attaches it → run chat turns that write
        events / record turns / scratchpad / compact state → close →
        reopen → resume_agent rebuilds → assert the resumed conversation
        equals the recorded turns, scratchpad restored, compact restored.
        """
        config_dir = tmp_path / "creature"
        config_path = _write_agent_config(config_dir, name="scribe")
        session_path = tmp_path / "scribe.kohakutr.v2"

        # ---- phase 1: a live agent runs turns against a real store ----
        store = _new_store(session_path, config_path=config_path, agents=["scribe"])
        patched_llm["script"] = [
            "Hello, I am the scribe.",
            "[/echo]persisted[echo/]",
            "The echo tool ran and I recorded it.",
        ]
        agent = Agent.from_path(config_path)
        echo = _EchoTool()
        agent.registry.register_tool(echo)
        agent.executor.register_tool(echo)
        agent.attach_session_store(store)
        await agent.start()
        try:
            await agent._process_event(create_user_input_event("introduce yourself"))
            await agent._process_event(create_user_input_event("use the echo tool"))
            # Drive the SessionOutput activity surface the runtime uses:
            # notify_activity → on_activity_with_metadata → _record_activity
            # dispatch table. Each call must land as a typed event row.
            # Emitted between turns so the next turn's on_processing_end
            # writes a fresh conversation snapshot covering them.
            router = agent.output_router
            router.notify_activity(
                "token_usage",
                "[scribe] usage",
                metadata={
                    "prompt_tokens": 120,
                    "completion_tokens": 45,
                    "total_tokens": 165,
                    "cached_tokens": 30,
                },
            )
            router.notify_activity(
                "turn_token_usage",
                "[scribe] turn",
                metadata={
                    "turn_index": 2,
                    "prompt_tokens": 80,
                    "completion_tokens": 20,
                    "cached_tokens": 10,
                    "total_tokens": 100,
                    "cost_usd": 0.0021,
                },
            )
            router.notify_activity(
                "subagent_start",
                "[research] dig into auth",
                metadata={"task": "dig into auth", "job_id": "sa-1"},
            )
            router.notify_activity(
                "subagent_done",
                "[research] found it",
                metadata={
                    "job_id": "sa-1",
                    "subagent": "research",
                    "result": "auth uses JWT",
                    "turns": 2,
                    "tools_used": ["grep"],
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                },
            )
            router.notify_activity(
                "scratchpad_write",
                "[scribe] project",
                metadata={"key": "project", "action": "set", "size_bytes": 9},
            )
            router.notify_activity(
                "compact_start", "[scribe] compacting", metadata={"round": 1}
            )
            router.notify_activity(
                "compact_complete",
                "[scribe] compacted",
                metadata={
                    "round": 1,
                    "summary": "summarized turn one",
                    "messages_compacted": 2,
                },
            )
            # The on_assistant_image OutputModule hook records an
            # assistant_image event row; emitted here (between turns) so
            # the next turn's on_processing_end snapshot covers it.
            agent._session_output.on_assistant_image(
                "out/pic.png",
                detail="high",
                source_type="tool",
                source_name="image_gen",
                revised_prompt="a terrarium at dusk",
            )
            # The agent writes scratchpad through SessionOutput on each
            # turn-end; seed a value and run one more turn so it persists
            # and the snapshot covers every activity event above.
            agent.session.scratchpad.set("project", "terrarium")
            await agent._process_event(create_user_input_event("remember the project"))
        finally:
            await agent.stop()

        # The SessionOutput sink restored cumulative token totals from
        # state and accumulated the live token_usage call on top.
        assert agent._session_output._total_input_tokens == 120
        assert agent._session_output._total_output_tokens == 45
        assert agent._session_output._total_cached_tokens == 30
        # The turn_token_usage activity also wrote a turn_rollup row.
        rollup = store.get_turn_rollup("scribe", 2)
        assert rollup is not None
        assert rollup["tokens_in"] == 80
        assert rollup["tokens_out"] == 20
        assert rollup["cost_usd"] == 0.0021
        rollups = store.list_turn_rollups("scribe")
        assert [r["turn_index"] for r in rollups] == [2]

        # Channel messages + job records + a sub-agent conversation
        # snapshot — the other SessionStore tables the runtime writes.
        store.save_channel_message(
            "broadcast",
            {"sender": "scribe", "content": "the scribe greets the channel"},
        )
        store.save_channel_message(
            "broadcast",
            {"sender": "scribe", "content": "a second channel broadcast message"},
        )
        store.save_job(
            "job-echo-1",
            {"tool": "echo", "status": "completed", "output": "echoed: persisted"},
        )
        # The subagent_done activity above already persisted a run-0
        # conversation via SessionOutput, so the next run index is 1.
        run = store.next_subagent_run("scribe", "research")
        assert run == 1
        store.save_subagent(
            parent="scribe",
            name="research",
            run=run,
            meta={"task": "dig into auth", "success": True, "turns": 2},
            conv_json='[{"role": "user", "content": "dig"}]',
        )
        assert store.next_subagent_run("scribe", "research") == 2

        # FTS keyword search over the live store finds the channel text.
        search_hits = store.search("channel", k=5)
        assert search_hits
        assert any(
            "channel" in str(h.get("meta", {}).get("type", "")) for h in search_hits
        )

        # Persist compact state the way the runtime does (compact_manager
        # writes this; attach_session_store reads it back on resume).
        store.save_state("scribe", compact_count=3)

        # The append-only event log is the source of truth on resume.
        # The ``user_input`` events ARE exactly the real user turns
        # (tool-feedback messages never get a ``user_input`` event).
        events = store.get_events("scribe")
        recorded_user_turns = [
            e["content"] for e in events if e["type"] == "user_input"
        ]
        assert recorded_user_turns == [
            "introduce yourself",
            "use the echo tool",
            "remember the project",
        ]
        # The event log captured the tool call + result for turn 2.
        event_types = [e["type"] for e in events]
        assert "user_message" in event_types
        assert "tool_call" in event_types
        assert "tool_result" in event_types
        tool_results = [e for e in events if e["type"] == "tool_result"]
        assert tool_results[0]["output"] == "echoed: persisted"
        # Every recorded turn carries a stable monotonic turn_index.
        turn_indices = sorted(
            {e["turn_index"] for e in events if e["type"] == "user_input"}
        )
        assert turn_indices == [1, 2, 3]
        # The SessionOutput activity dispatch wrote one typed row each.
        token_evt = next(e for e in events if e["type"] == "token_usage")
        assert token_evt["prompt_tokens"] == 120
        assert token_evt["cached_tokens"] == 30
        # The turn_token_usage payload carries the metadata's turn_index
        # in its body; the event's own ``turn_index`` field is stamped
        # by SessionOutput from the agent's live turn counter.
        turn_tok_evt = next(e for e in events if e["type"] == "turn_token_usage")
        assert turn_tok_evt["prompt_tokens"] == 80
        sa_call = next(e for e in events if e["type"] == "subagent_call")
        assert sa_call["task"] == "dig into auth"
        sa_result = next(e for e in events if e["type"] == "subagent_result")
        assert sa_result["name"] == "research"
        assert sa_result["output"] == "auth uses JWT"
        pad_evt = next(e for e in events if e["type"] == "scratchpad_write")
        assert pad_evt["key"] == "project"
        compact_done = next(e for e in events if e["type"] == "compact_complete")
        assert compact_done["summary"] == "summarized turn one"
        img_evt = next(e for e in events if e["type"] == "assistant_image")
        assert img_evt["url"] == "out/pic.png"
        assert img_evt["revised_prompt"] == "a terrarium at dusk"
        # The token_usage handler also rolled cumulative totals into state.
        usage_state = store.state.get("scribe:token_usage")
        assert usage_state["total_input_tokens"] == 120
        assert usage_state["total_cached_tokens"] == 30

        # token_usage() read API derives per-loop counters from events.
        usage = store.token_usage("scribe")
        assert usage["prompt_tokens"] == 120
        assert usage["completion_tokens"] == 45
        # token_usage(agent=None) is a hard error — no silent "main" pick.
        with pytest.raises(ValueError, match="explicit agent name"):
            store.token_usage(None)
        # include_subagents surfaces the research run keyed by its path.
        # The subagent_done activity carried prompt=10/completion=5, and
        # the explicit save_subagent above added a second run with no
        # event tokens — so it falls back to the zero shape.
        sub_usage = store.token_usage("scribe", include_subagents=True)
        sub_map = sub_usage["subagents"]
        assert "scribe:subagent:research:0" in sub_map
        assert sub_map["scribe:subagent:research:0"]["prompt_tokens"] == 10
        assert sub_map["scribe:subagent:research:0"]["completion_tokens"] == 5
        assert sub_map["scribe:subagent:research:1"] == {
            "total_tokens": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cached_tokens": 0,
        }
        # include_attached is always a dict even with no attached agents.
        att_usage = store.token_usage("scribe", include_attached=True)
        assert att_usage["attached"] == {}
        # by_turn reads the turn_rollup table written by turn_token_usage.
        by_turn = store.token_usage("scribe", by_turn=True)["by_turn"]
        assert {"turn_index": 2, "prompt": 80, "completion": 20, "cached": 10} in (
            by_turn
        )
        # token_usage_all_loops enumerates every controller loop: the
        # main agent first, then each sub-agent run by path.
        loops = dict(store.token_usage_all_loops())
        assert "scribe" in loops
        assert loops["scribe"]["prompt_tokens"] == 120
        assert "scribe:subagent:research:0" in loops
        assert loops["scribe:subagent:research:0"]["completion_tokens"] == 5

        # Persist resumable triggers the way the runtime does on stop —
        # save_state writes the triggers list, load_triggers reads it
        # back, and resume hands it to the rebuilt agent.
        store.save_state(
            "scribe",
            triggers=[
                {"trigger_id": "hb", "type": "timer", "prompt": "tick", "interval": 99}
            ],
        )
        assert store.load_triggers("scribe")[0]["trigger_id"] == "hb"
        assert store.load_triggers("nobody") == []
        store.close()

        # ---- version probe + session-type detection on the closed file ----
        # detect_format_version reads the stamped version off disk.
        assert detect_format_version(session_path) == FORMAT_VERSION
        # A missing path is a hard FileNotFoundError, not a silent 1.
        with pytest.raises(FileNotFoundError):
            detect_format_version(tmp_path / "nope.kohakutr.v2")
        # detect_session_type reads config_type out of meta.
        assert detect_session_type(session_path) == "agent"

        # ---- phase 2: reopen the file fresh, resume the agent ----
        reopened = SessionStore(str(session_path))
        try:
            # Counters were rebuilt from disk — no events lost on reopen.
            assert reopened.get_events("scribe") == events
            # Channel + sub-agent counters survived the reopen: the next
            # write picks up where the closed store left off rather than
            # overwriting key m000000 / run 0.
            channel_msgs = reopened.get_channel_messages("broadcast")
            assert [m["content"] for m in channel_msgs] == [
                "the scribe greets the channel",
                "a second channel broadcast message",
            ]
            assert reopened._next_channel_seq("broadcast") == 2
            # Two research runs were saved (run 0 via SessionOutput, run 1
            # explicitly) — the counter restored to the next free index.
            assert reopened.next_subagent_run("scribe", "research") == 2
            # Job records round-tripped through the jobs table.
            job = reopened.load_job("job-echo-1")
            assert job["status"] == "completed"
            assert reopened.load_job("missing-job") is None
            # Sub-agent conversation + meta round-tripped (run 1 = the
            # explicit save above).
            sa_meta = reopened.load_subagent_meta("scribe", "research", 1)
            assert sa_meta["success"] is True
            sa_conv = reopened.load_subagent_conversation("scribe", "research", 1)
            assert "dig" in sa_conv
            assert reopened.load_subagent_meta("scribe", "research", 9) is None
        finally:
            reopened.close()

        resumed_agent, resumed_store = resume_agent(session_path)
        # ``resume_agent`` returns the agent un-started (the caller runs
        # it). ``start()`` builds the compact manager, which restores
        # compact_count from the re-attached store.
        await resumed_agent.start()
        try:
            # The agent was rebuilt from the config_path in meta and
            # re-aligned to the saved name.
            assert resumed_agent.config.name == "scribe"
            # The resumed conversation, rebuilt from the event log,
            # carries every recorded user turn in order. Tool-feedback
            # messages also land as role=user, so the real turns are an
            # ordered subsequence — extract them by exact match.
            resumed_messages = resumed_agent.controller.conversation.to_messages()
            resumed_user_msgs = [
                m["content"] for m in resumed_messages if m.get("role") == "user"
            ]
            recovered = [m for m in resumed_user_msgs if m in recorded_user_turns]
            assert recovered == recorded_user_turns
            # An assistant turn from the live run survived the round-trip.
            assert any(
                m.get("role") == "assistant" and "scribe" in str(m.get("content", ""))
                for m in resumed_messages
            )
            # The tool result the agent produced survived the
            # round-trip into the resumed conversation (the runtime
            # feeds tool output back as a turn-feedback message, so it
            # rides along in the restored snapshot).
            assert any(
                "echoed: persisted" in str(m.get("content", ""))
                for m in resumed_messages
            )
            # Scratchpad restored exactly.
            assert resumed_agent.session.scratchpad.get("project") == "terrarium"
            # Compact state restored from store.state.
            assert resumed_agent.compact_manager._compact_count == 3
            # The store was re-attached and marked running for continued
            # recording.
            assert resumed_store.load_meta()["status"] == "running"
            assert resumed_store.load_meta()["format_version"] == FORMAT_VERSION
            # The saved resumable triggers were staged onto the agent for
            # the trigger manager to pick up.
            staged = getattr(resumed_agent, "_pending_resume_triggers", None)
            assert staged and staged[0]["trigger_id"] == "hb"
        finally:
            await resumed_agent.stop()
            resumed_store.close()

        # ---- resume again, this time forcing the ``plain`` IO mode ----
        # resume_agent builds CLI input + stdout output from io_mode and
        # still restores the full conversation.
        plain_agent, plain_store = resume_agent(session_path, io_mode="plain")
        try:
            assert type(plain_agent.input).__name__ == "CLIInput"
            assert type(plain_agent.output_router.default_output).__name__ == (
                "StdoutOutput"
            )
            plain_users = [
                m["content"]
                for m in plain_agent.controller.conversation.to_messages()
                if m.get("role") == "user"
            ]
            assert all(t in plain_users for t in recorded_user_turns)
        finally:
            plain_store.close()
        # An unknown io_mode is rejected loudly before any agent is built.
        with pytest.raises(ValueError, match="Unknown IO mode"):
            resume_agent(session_path, io_mode="bogus-mode")

        # ---- the Session facade + Wave-F attach/detach workflow ----
        # ``Session`` wraps a store + optional agent; attaching an agent
        # routes its events under a ``<host>:attached:<role>:<seq>``
        # namespace (mirrors studio/sessions + attachment_service).
        host_path = tmp_path / "host.kohakutr.v2"
        host_store = _new_store(host_path, config_path=config_path, agents=["scribe"])
        host_session = Session(host_store, name="host-session")
        assert host_session.name == "host-session"
        assert host_session.path == str(host_path)
        assert host_session.store is host_store

        patched_llm["script"] = ["The attached helper reporting in."]
        helper = Agent.from_path(config_path)
        await helper.start()
        try:
            # attach_agent routes a fresh SessionOutput sink under the
            # attached namespace; its turns land there, not under host.
            host_session.attach_agent(helper, role="helper")
            attach_state = get_attach_state(helper)
            assert attach_state is not None
            assert attach_state["role"] == "helper"
            assert attach_state["host"] == "scribe"
            attached_prefix = attach_state["prefix"]
            assert attached_prefix == "scribe:attached:helper:0"
            # Re-attaching to the SAME session is an idempotent no-op.
            host_session.attach_agent(helper, role="helper")
            # Attaching to a DIFFERENT session is refused.
            other_store = _new_store(
                tmp_path / "other.kohakutr.v2",
                config_path=config_path,
                agents=["scribe"],
            )
            other_session = Session(other_store, name="other")
            with pytest.raises(AlreadyAttachedError):
                other_session.attach_agent(helper, role="helper")
            other_store.close()

            # Run a turn — the helper's events go to the attached prefix.
            # SessionOutput (the attach sink) does NOT record user_input
            # itself (the agent's own store does that), but the streamed
            # assistant text lands under the attached namespace.
            await helper._process_event(create_user_input_event("attached turn one"))
            attached_events = host_store.get_events(attached_prefix)
            attached_text = "".join(
                e.get("content", "")
                for e in attached_events
                if e["type"] == "text_chunk"
            )
            assert "attached helper reporting in" in attached_text
            assert any(e["type"] == "processing_end" for e in attached_events)
            # The host namespace carries the agent_attached lineage event.
            host_events = host_store.get_events("scribe")
            assert any(e["type"] == "agent_attached" for e in host_events)
            # discover_attached_agents surfaces the attached namespace.
            discovered = host_store.discover_attached_agents()
            assert any(d["namespace"] == attached_prefix for d in discovered)
            # token_usage(include_attached=True) keys it under the prefix.
            host_usage = host_store.token_usage("scribe", include_attached=True)
            assert attached_prefix in host_usage["attached"]

            # detach unwires the sink and emits the agent_detached event.
            host_session.detach_agent(helper)
            assert get_attach_state(helper) is None
            host_events_after = host_store.get_events("scribe")
            assert any(e["type"] == "agent_detached" for e in host_events_after)
            # Detaching an unattached agent is a hard error.
            with pytest.raises(NotAttachedError):
                host_session.detach_agent(helper)
        finally:
            await helper.stop()

        # Session.fork is the async facade over SessionStore.fork: it
        # off-threads the copy and returns a new Session with no agent.
        host_store.flush()
        host_fork_point = host_store.get_events("scribe")[0]["event_id"]
        forked_session = await host_session.fork(
            at_event_id=host_fork_point, name="host-child"
        )
        try:
            assert forked_session.name == "host-child"
            assert forked_session.agent is None
            assert Path(forked_session.path).exists()
            child_meta = forked_session.store.load_meta()
            assert child_meta["lineage"]["fork"]["fork_point"] == host_fork_point
        finally:
            forked_session.store.close()
        host_store.close()

    async def test_memory_index_and_search_finds_exact_turn(
        self, patched_llm, tmp_path
    ):
        """Run an agent, then index + search its session memory.

        Mirrors ``studio/sessions/memory_search.py``: open the store,
        build a ``SessionMemory`` over an embedder, ``index_events`` per
        agent, then ``search``. Asserts the EXACT recorded user turn is
        the top hit for both FTS and semantic modes.
        """
        config_dir = tmp_path / "creature"
        config_path = _write_agent_config(config_dir, name="seeker")
        session_path = tmp_path / "seeker.kohakutr.v2"

        store = _new_store(session_path, config_path=config_path, agents=["seeker"])
        patched_llm["script"] = [
            "I will note the authentication subsystem.",
            "Understood, the database migration is scheduled.",
        ]
        agent = Agent.from_path(config_path)
        echo = _EchoTool()
        agent.registry.register_tool(echo)
        agent.executor.register_tool(echo)
        agent.attach_session_store(store)
        await agent.start()
        try:
            await agent._process_event(
                create_user_input_event("the authentication subsystem has a bug")
            )
            await agent._process_event(
                create_user_input_event("plan the database migration carefully")
            )
        finally:
            await agent.stop()

        store.flush()
        events = store.get_events("seeker")

        # FTS keyword search (always available).
        fts_memory = SessionMemory(str(session_path), embedder=None, store=store)
        indexed = fts_memory.index_events("seeker", events)
        assert indexed > 0
        fts_hits = fts_memory.search("authentication", mode="fts", k=5)
        assert fts_hits, "FTS search returned nothing"
        assert fts_hits[0].content == "the authentication subsystem has a bug"
        assert fts_hits[0].block_type == "user"
        # Re-indexing the same events is a no-op — the indexed counter
        # already covers them, so no duplicate blocks land.
        assert fts_memory.index_events("seeker", events) == 0
        # FTS-only memory reports no vector index.
        fts_stats = fts_memory.get_stats()
        assert fts_stats["has_vectors"] is False
        assert fts_stats["vec_blocks"] == 0
        assert fts_stats["fts_blocks"] == indexed

        # Semantic search over the deterministic embedder. The exact
        # phrase embeds to the exact stored vector → top hit is itself.
        sem_memory = SessionMemory(
            str(session_path), embedder=_HashEmbedder(), store=store
        )
        sem_memory.index_events("seeker", events)
        assert sem_memory.has_vectors
        sem_hits = sem_memory.search(
            "plan the database migration carefully", mode="semantic", k=5
        )
        assert sem_hits, "semantic search returned nothing"
        assert sem_hits[0].content == "plan the database migration carefully"
        # The vector index now carries one block per indexed FTS block.
        sem_stats = sem_memory.get_stats()
        assert sem_stats["has_vectors"] is True
        assert sem_stats["dimensions"] == 64
        assert sem_stats["vec_blocks"] == sem_stats["fts_blocks"]

        # Hybrid mode fuses FTS + semantic via reciprocal rank fusion;
        # the exact phrase still rises to the top.
        hybrid_hits = sem_memory.search(
            "the authentication subsystem has a bug", mode="hybrid", k=5
        )
        assert hybrid_hits
        assert hybrid_hits[0].content == "the authentication subsystem has a bug"
        # "auto" mode resolves to hybrid when an embedder is present.
        auto_hits = sem_memory.search("database migration", mode="auto", k=5)
        assert auto_hits
        # Agent-scoped filtering keeps only this agent's blocks.
        scoped = sem_memory.search("authentication", mode="fts", k=5, agent="seeker")
        assert scoped and all(h.agent == "seeker" for h in scoped)
        assert sem_memory.search("authentication", mode="fts", k=5, agent="ghost") == []

        # Run a third turn that calls a tool, then incrementally index
        # ONLY the new events — _extract_blocks emits a tool block and
        # the indexed counter advances past the original two turns.
        patched_llm["script"] = ["[/echo]search-target-token[echo/]", "noted."]
        agent2 = Agent.from_path(config_path)
        echo2 = _EchoTool()
        agent2.registry.register_tool(echo2)
        agent2.executor.register_tool(echo2)
        agent2.attach_session_store(store)
        await agent2.start()
        try:
            await agent2._process_event(
                create_user_input_event("echo the search target now")
            )
        finally:
            await agent2.stop()
        store.flush()
        all_events = store.get_events("seeker")
        assert len(all_events) > len(events)
        new_blocks = fts_memory.index_events("seeker", all_events)
        assert new_blocks > 0
        tool_hits = fts_memory.search("echo", mode="fts", k=10)
        assert any(h.block_type == "tool" for h in tool_hits)

        # An embedder-less memory asked for "semantic" mode logs a
        # warning and degrades to FTS rather than returning nothing.
        degraded = fts_memory.search("authentication", mode="semantic", k=5)
        assert degraded and degraded[0].content == (
            "the authentication subsystem has a bug"
        )
        # An unknown mode also falls back to FTS (no crash).
        bogus = fts_memory.search("authentication", mode="not-a-mode", k=5)
        assert bogus and bogus[0].block_type == "user"

        # close() releases the native handles SessionMemory opened.
        # Re-opening the same file embedder-less must rediscover the
        # vector table the semantic memory persisted (vec_dimensions in
        # memory_state) so search-only consumers still see vectors.
        sem_stat_before = sem_memory.get_stats()
        sem_memory.close()
        fts_memory.close()
        reopened_mem = SessionMemory(str(session_path), embedder=None, store=store)
        try:
            # The ``has_vectors`` property tracks the reopened vec table
            # even though the embedder-less stats row reports the
            # NullEmbedder (no live model attached).
            assert reopened_mem.has_vectors is True
            reopened_stats = reopened_mem.get_stats()
            assert reopened_stats["has_vectors"] is False
            # The persisted vec table carries the blocks the semantic
            # memory wrote — the count survived the close + reopen.
            assert reopened_stats["vec_blocks"] == sem_stat_before["vec_blocks"]
            assert reopened_stats["vec_blocks"] > 0
        finally:
            reopened_mem.close()
        store.close()

    async def test_fork_at_event_copies_lineage(self, patched_llm, tmp_path):
        """Run an agent, then fork its session at a mid-stream event.

        Asserts copy-on-fork lineage: the child is an independent file
        carrying only events ≤ the fork point, a ``lineage.fork`` record
        pointing back at the parent, and the parent records the child in
        ``forked_children``.
        """
        config_dir = tmp_path / "creature"
        config_path = _write_agent_config(config_dir, name="brancher")
        session_path = tmp_path / "brancher.kohakutr.v2"

        store = _new_store(session_path, config_path=config_path, agents=["brancher"])
        patched_llm["script"] = [
            "First response.",
            "Second response.",
            "Third response.",
        ]
        agent = Agent.from_path(config_path)
        agent.attach_session_store(store)
        await agent.start()
        try:
            await agent._process_event(create_user_input_event("turn one"))
            await agent._process_event(create_user_input_event("turn two"))
            await agent._process_event(create_user_input_event("turn three"))
        finally:
            await agent.stop()

        # Seed the other tables so the fork's wholesale-copy paths are
        # exercised: channels, jobs, sub-agent runs all cross into the
        # child verbatim.
        store.save_channel_message(
            "team", {"sender": "brancher", "content": "a message in the team channel"}
        )
        store.save_job("job-fork-1", {"tool": "echo", "status": "completed"})
        run = store.next_subagent_run("brancher", "helper")
        store.save_subagent(
            parent="brancher",
            name="helper",
            run=run,
            meta={"task": "assist", "success": True},
            conv_json='[{"role": "user", "content": "assist"}]',
        )
        store.flush()
        all_events = store.get_events("brancher")
        # Pick the fork point: the user_input event for "turn two".
        turn_two_evt = next(
            e
            for e in all_events
            if e["type"] == "user_input" and e.get("content") == "turn two"
        )
        fork_point_id = turn_two_evt["event_id"]
        events_at_or_before = [e for e in all_events if e["event_id"] <= fork_point_id]

        fork_path = tmp_path / "brancher-fork.kohakutr.v2"
        parent_session_id = store.session_id
        child = store.fork(str(fork_path), at_event_id=fork_point_id, name="my-fork")
        try:
            # Child is a real, independent file.
            assert fork_path.exists()
            assert child.path == str(fork_path)
            # Child carries exactly the events ≤ fork point — nothing after.
            child_events = child.get_events("brancher")
            assert [e["event_id"] for e in child_events] == [
                e["event_id"] for e in events_at_or_before
            ]
            child_max = max(e["event_id"] for e in child_events)
            assert child_max == fork_point_id
            parent_max = max(e["event_id"] for e in all_events)
            assert parent_max > fork_point_id  # parent still has turn three
            # Lineage points back at the parent at the right event.
            child_meta = child.load_meta()
            lineage = child_meta["lineage"]["fork"]
            assert lineage["fork_point"] == fork_point_id
            assert lineage["parent_session_id"] == parent_session_id
            assert lineage["fork_mutation"] is None
            assert child_meta["name"] == "my-fork"
            # Child got a fresh, distinct session id.
            child_session_id = child_meta["session_id"]
            assert child_session_id != parent_session_id
            # The non-event tables were copied wholesale into the child,
            # and the child's counters were re-derived so it can append.
            assert [m["content"] for m in child.get_channel_messages("team")] == [
                "a message in the team channel"
            ]
            assert child.load_job("job-fork-1")["status"] == "completed"
            assert child.load_subagent_meta("brancher", "helper", 0)["success"] is True
            assert child.next_subagent_run("brancher", "helper") == 1
            # forked_children is parent-local bookkeeping — it does NOT
            # cross into the child.
            assert "forked_children" not in child_meta
        finally:
            child.close()

        # Parent records the child in forked_children.
        children = store.load_meta()["forked_children"]
        assert len(children) == 1
        assert children[0]["session_id"] == child_session_id
        assert children[0]["fork_point"] == fork_point_id

        # Fork with a ``mutate`` callable: the fork-point event is
        # rewritten before it lands in the child.
        def redact(event: dict) -> dict:
            event = dict(event)
            event["content"] = "[redacted]"
            return event

        mutated_path = tmp_path / "brancher-redacted.kohakutr.v2"
        mutated_child = store.fork(
            str(mutated_path), at_event_id=fork_point_id, mutate=redact
        )
        try:
            mutated_events = mutated_child.get_events("brancher")
            fork_evt = next(e for e in mutated_events if e["event_id"] == fork_point_id)
            assert fork_evt["content"] == "[redacted]"
            assert mutated_child.load_meta()["lineage"]["fork"]["fork_mutation"] == (
                "redact"
            )
        finally:
            mutated_child.close()

        # Forking onto an existing path is refused (no silent overwrite).
        with pytest.raises(FileExistsError):
            store.fork(str(fork_path), at_event_id=fork_point_id)
        # event_id 0 is invalid — event ids start at 1.
        with pytest.raises(ValueError):
            store.fork(str(tmp_path / "bad.kohakutr.v2"), at_event_id=0)
        # No event at-or-before a too-low-but-valid id → refuse, don't
        # produce an empty child. event_id 1 always exists here, so use
        # a fork point above the range only when nothing matches: pick a
        # brand-new store with a single event and fork below it instead.
        empty_src_path = tmp_path / "emptyish.kohakutr.v2"
        empty_src = _new_store(
            empty_src_path, config_path=config_path, agents=["brancher"]
        )
        empty_src.append_event("brancher", "user_message", {"content": "only one"})
        empty_src.flush()
        # at_event_id is global; event 1 here may not be id 1 (counters
        # are global across stores in one process is NOT the case —
        # each store has its own). The lone event has the lowest id, so
        # forking at id-1-below-it finds nothing.
        lone_id = empty_src.get_events("brancher")[0]["event_id"]
        if lone_id > 1:
            with pytest.raises(ValueError, match="No events found"):
                empty_src.fork(
                    str(tmp_path / "void.kohakutr.v2"), at_event_id=lone_id - 1
                )
        empty_src.close()

        # A mutate callable that RAISES is wrapped into a RuntimeError —
        # the partial child file is cleaned up, not left half-written.
        def boom(event: dict) -> dict:
            raise RuntimeError("mutator blew up")

        boom_path = tmp_path / "brancher-boom.kohakutr.v2"
        with pytest.raises(RuntimeError, match="mutate callable raised"):
            store.fork(str(boom_path), at_event_id=fork_point_id, mutate=boom)
        assert not boom_path.exists()  # partial output removed

        # A mutate callable that returns a NON-dict is a TypeError, and
        # again leaves no partial file behind.
        def wrong_type(event: dict) -> str:
            return "not a dict"

        badtype_path = tmp_path / "brancher-badtype.kohakutr.v2"
        with pytest.raises(TypeError, match="must return a dict or None"):
            store.fork(str(badtype_path), at_event_id=fork_point_id, mutate=wrong_type)
        assert not badtype_path.exists()

        # Binary artifacts written to the parent are shallow-copied into
        # the child's own artifacts dir on fork.
        store.write_artifact("note.txt", b"forked artifact bytes")
        art_fork_path = tmp_path / "brancher-with-art.kohakutr.v2"
        art_child = store.fork(str(art_fork_path), at_event_id=fork_point_id)
        try:
            child_art = art_child.artifacts_dir / "note.txt"
            assert child_art.exists()
            assert child_art.read_bytes() == b"forked artifact bytes"
        finally:
            art_child.close()

        # Forking across a still-running job is unstable: append an
        # unclosed tool_call, then declare its id pending.
        _key, open_call_id = store.append_event(
            "brancher",
            "tool_call",
            {"name": "slow", "call_id": "pending-tool"},
        )
        store.flush()
        with pytest.raises(ForkNotStableError):
            store.fork(
                str(tmp_path / "unstable.kohakutr.v2"),
                at_event_id=open_call_id,
                pending_job_ids={"pending-tool"},
            )
        store.close()

    async def test_forked_session_resumes_at_fork_point(self, patched_llm, tmp_path):
        """A resumed fork must NOT replay turns after the fork point.

        Same fork workflow as above, but here we resume the forked file
        and assert its conversation stops exactly at the fork point.

        Regression guard for B-session-2 (FIXED): ``store.fork`` used to
        copy the parent's ``conversation`` snapshot and ``snapshot_event_id``
        verbatim — the child's event log was correctly truncated, but the
        stale snapshot still held post-fork turns and its
        ``snapshot_event_id`` outranked the child's last event, so
        ``resume_agent`` trusted the snapshot and replayed cut turns. The
        fix: fork no longer copies the conversation snapshot and strips
        ``snapshot_event_id`` from the child's state, so resume replays
        from the truncated event log.
        """
        config_dir = tmp_path / "creature"
        config_path = _write_agent_config(config_dir, name="brancher")
        session_path = tmp_path / "brancher.kohakutr.v2"

        store = _new_store(session_path, config_path=config_path, agents=["brancher"])
        patched_llm["script"] = ["First.", "Second.", "Third."]
        agent = Agent.from_path(config_path)
        agent.attach_session_store(store)
        await agent.start()
        try:
            await agent._process_event(create_user_input_event("turn one"))
            await agent._process_event(create_user_input_event("turn two"))
            await agent._process_event(create_user_input_event("turn three"))
        finally:
            await agent.stop()

        store.flush()
        all_events = store.get_events("brancher")
        turn_two_evt = next(
            e
            for e in all_events
            if e["type"] == "user_input" and e.get("content") == "turn two"
        )

        # Synthesize a regenerated branch of turn 2 and a follow-up turn 3
        # under it, so the history helpers have a real multi-branch
        # stream to resolve. branch_id=2 is the regen; branch 1 is the
        # original recorded by the agent.
        store.append_event(
            "brancher",
            "user_message",
            {"content": "turn two"},
            turn_index=2,
            branch_id=2,
        )
        store.append_event(
            "brancher",
            "text_chunk",
            {"content": "Second response, regenerated.", "chunk_seq": 0},
            turn_index=2,
            branch_id=2,
        )
        store.append_event(
            "brancher",
            "user_message",
            {"content": "turn three on regen"},
            turn_index=3,
            branch_id=2,
            parent_branch_path=[(2, 2)],
        )
        store.append_event(
            "brancher",
            "text_chunk",
            {"content": "Third response under regen.", "chunk_seq": 0},
            turn_index=3,
            branch_id=2,
            parent_branch_path=[(2, 2)],
        )
        store.flush()
        branched_events = store.get_events("brancher")

        # collect_branch_metadata sees both branches of turn 2.
        branch_meta = collect_branch_metadata(branched_events)
        assert branch_meta[2]["branches"] == [1, 2]
        assert branch_meta[2]["latest_branch"] == 2
        # collect_user_groups buckets the two turn-2 branches by their
        # user_message content. Both say "turn two" → one group.
        groups = collect_user_groups(branched_events)
        assert len(groups[2]["groups"]) == 1
        assert groups[2]["groups"][0]["content"] == "turn two"
        # Default replay follows the latest branch at every level.
        default_msgs = replay_conversation(branched_events)
        default_users = [m["content"] for m in default_msgs if m.get("role") == "user"]
        assert "turn three on regen" in default_users
        # Pinning turn 2 back to branch 1 hides the regen subtree —
        # turn 3's only branch lives under (2, 2), so it disappears.
        pinned_msgs = replay_conversation(branched_events, branch_view={2: 1})
        pinned_users = [m["content"] for m in pinned_msgs if m.get("role") == "user"]
        assert "turn three on regen" not in pinned_users
        assert "turn two" in pinned_users

        fork_path = tmp_path / "brancher-fork.kohakutr.v2"
        child = store.fork(str(fork_path), at_event_id=turn_two_evt["event_id"])
        child.close()
        store.close()

        forked_agent, forked_store = resume_agent(fork_path)
        try:
            forked_users = [
                m["content"]
                for m in forked_agent.controller.conversation.to_messages()
                if m.get("role") == "user"
            ]
            # The fork was cut at "turn two" — "turn three" must be gone.
            assert "turn three" not in forked_users
            # Resume restored the agent's turn/branch counters from the
            # truncated event log — the leaf is turn 2, branch 1.
            assert forked_agent._turn_index == 2
            assert forked_agent._branch_id == 1
        finally:
            forked_store.close()

        # normalize_resumable_events synthesizes an interrupted result
        # for a tool_call with no matching tool_result.
        unfinished = [
            {"type": "tool_call", "name": "slow", "call_id": "c-open", "event_id": 1},
            {"type": "user_message", "content": "hi", "event_id": 2},
        ]
        normalized = normalize_resumable_events(unfinished)
        synth = [e for e in normalized if e.get("_synthetic_resume")]
        assert len(synth) == 1
        assert synth[0]["type"] == "tool_result"
        assert synth[0]["interrupted"] is True
        # When the job id is declared live, no synthetic event is added.
        still_live = normalize_resumable_events(unfinished, live_job_ids={"c-open"})
        assert not [e for e in still_live if e.get("_synthetic_resume")]

    async def test_v1_to_v2_migration_round_trip(self, patched_llm, tmp_path):
        """Write a v1-format session, migrate it, resume from the result.

        ``ensure_latest_version`` discovers the bare v1 file, runs the
        ``v1_to_v2`` migrator (event-log driven), and returns the
        ``.v2`` path. The original v1 file is preserved untouched; the
        resumed agent's conversation reflects the v1 history.
        """
        config_dir = tmp_path / "creature"
        config_path = _write_agent_config(config_dir, name="legacy")
        # v1 uses the bare ``.kohakutr`` path (no version suffix).
        v1_path = tmp_path / "legacy.kohakutr"

        # Build a v1 store: format_version 1, observability-style event
        # log (user_input + text + tool_call/tool_result), no v2
        # state-bearing events. This is what an old framework wrote.
        v1 = SessionStore(str(v1_path))
        v1.meta["format_version"] = 1
        v1.init_meta(
            session_id="legacy",
            config_type="agent",
            config_path=config_path,
            pwd=str(tmp_path),
            agents=["legacy"],
        )
        # init_meta stamps the current FORMAT_VERSION; force v1 back.
        v1.meta["format_version"] = 1
        v1.append_event("legacy", "user_input", {"content": "old question one"})
        v1.append_event("legacy", "text", {"content": "old answer one"})
        v1.append_event("legacy", "user_input", {"content": "old question two"})
        v1.append_event(
            "legacy", "tool_call", {"name": "echo", "call_id": "c1", "args": {}}
        )
        v1.append_event(
            "legacy",
            "tool_result",
            {"name": "echo", "call_id": "c1", "output": "old tool output"},
        )
        v1.append_event("legacy", "text", {"content": "old answer two"})
        # v1 also wrote channel / sub-agent / job tables — the migrator
        # must carry every one of these across verbatim.
        v1.save_channel_message(
            "legacy-channel", {"sender": "legacy", "content": "an old channel note"}
        )
        v1.save_job("legacy-job", {"tool": "echo", "status": "completed"})
        v1.save_subagent(
            parent="legacy",
            name="oldhelper",
            run=v1.next_subagent_run("legacy", "oldhelper"),
            meta={"task": "legacy assist", "success": True},
            conv_json='[{"role": "user", "content": "legacy"}]',
        )
        v1.flush()
        v1.close()

        # detect_format_version reads the bare file's stamped version.
        assert detect_format_version(v1_path) == 1
        # discover_versions finds the lone v1 file before migration.
        pre_versions = discover_versions(v1_path)
        assert [v for v, _ in pre_versions] == [1]
        # path_for_version maps the version → on-disk path convention.
        assert path_for_version(v1_path, 1) == v1_path
        assert path_for_version(v1_path, 2) == tmp_path / "legacy.kohakutr.v2"

        # Migrate: ensure_latest_version returns the .v2 path and leaves
        # the original v1 file in place.
        migrated_path = ensure_latest_version(v1_path)
        assert migrated_path != v1_path
        assert migrated_path.exists()
        assert v1_path.exists(), "original v1 file must be preserved"
        assert migrated_path == path_for_version(v1_path, 2)

        # After migration discover_versions sees both files; the newest
        # readable one is what ensure_latest_version returned.
        post_versions = dict(discover_versions(v1_path))
        assert set(post_versions) == {1, 2}

        # The migrated file is v2 and carries migration lineage.
        migrated = SessionStore(str(migrated_path))
        try:
            mmeta = migrated.load_meta()
            assert mmeta["format_version"] == 2
            assert mmeta["migrated_from"]["source_version"] == 1
            # The v1 event log was translated into v2 state-bearing events.
            mtypes = [e["type"] for e in migrated.get_events("legacy")]
            assert "user_message" in mtypes
            assert "text_chunk" in mtypes
            assert "tool_result" in mtypes
            # The non-event tables were carried across by the migrator.
            assert [
                m["content"] for m in migrated.get_channel_messages("legacy-channel")
            ] == ["an old channel note"]
            assert migrated.load_job("legacy-job")["status"] == "completed"
            assert (
                migrated.load_subagent_meta("legacy", "oldhelper", 0)["success"] is True
            )
        finally:
            migrated.close()

        # Migration is idempotent: re-running reuses the existing .v2
        # file rather than rebuilding (or erroring on the existing file).
        re_migrated = migrate(v1_path, 2)
        assert re_migrated == migrated_path
        assert ensure_latest_version(migrated_path) == migrated_path

        # Resume from the bare v1 path: resume_agent migrates internally
        # and rebuilds the agent. The v1 history replays into the
        # conversation.
        resumed_agent, resumed_store = resume_agent(v1_path)
        try:
            messages = resumed_agent.controller.conversation.to_messages()
            users = [m["content"] for m in messages if m.get("role") == "user"]
            assert users == ["old question one", "old question two"]
            # Assistant text from the v1 log survived the migration.
            assistant_text = " ".join(
                str(m.get("content", ""))
                for m in messages
                if m.get("role") == "assistant"
            )
            assert "old answer one" in assistant_text
            assert "old answer two" in assistant_text
            # The migrated tool result is a tool-role message.
            tool_msgs = [m for m in messages if m.get("role") == "tool"]
            assert any(m.get("content") == "old tool output" for m in tool_msgs)
        finally:
            resumed_store.close()

        # ---- the snapshot-fallback migration path ----
        # A v1 session that never streamed events: its history lives
        # ONLY in the conversation snapshot. The migrator must fall back
        # to synthesizing v2 events from that snapshot.
        snap_v1_path = tmp_path / "snaponly.kohakutr"
        snap_v1 = SessionStore(str(snap_v1_path))
        snap_v1.meta["format_version"] = 1
        snap_v1.init_meta(
            session_id="snaponly",
            config_type="agent",
            config_path=config_path,
            pwd=str(tmp_path),
            agents=["legacy"],
        )
        snap_v1.meta["format_version"] = 1
        snap_v1.save_conversation(
            "legacy",
            [
                {"role": "system", "content": "You are a test scribe."},
                {"role": "user", "content": "snapshot question"},
                {"role": "assistant", "content": "snapshot answer"},
            ],
        )
        snap_v1.flush()
        snap_v1.close()

        snap_migrated_path = ensure_latest_version(snap_v1_path)
        assert snap_migrated_path == path_for_version(snap_v1_path, 2)
        snap_migrated = SessionStore(str(snap_migrated_path))
        try:
            # The snapshot was translated into state-bearing events:
            # a system_prompt_set plus the user/assistant exchange.
            stypes = [e["type"] for e in snap_migrated.get_events("legacy")]
            assert "system_prompt_set" in stypes
            assert "user_message" in stypes
            replayed = replay_conversation(snap_migrated.get_events("legacy"))
            assert {"role": "user", "content": "snapshot question"} in replayed
            assert any(
                m.get("role") == "assistant"
                and "snapshot answer" in str(m.get("content", ""))
                for m in replayed
            )
        finally:
            snap_migrated.close()

        # ---- migration registry edge cases ----
        # migration_marker is a parseable ISO-8601 UTC timestamp.
        marker = migration_marker()
        assert marker.endswith("+00:00")
        parsed = datetime.fromisoformat(marker)
        assert parsed.tzinfo is not None
        # migrate() on a non-existent path is FileNotFoundError, not a
        # silent no-op.
        with pytest.raises(FileNotFoundError):
            migrate(tmp_path / "ghost.kohakutr", 2)
        # migrate() on a file ALREADY at/above the target version is an
        # identity no-op — it returns the same path untouched.
        already_v2 = path_for_version(v1_path, 2)
        assert migrate(already_v2, 2) == already_v2
        # ensure_latest_version on a path with NO files on disk returns
        # the input path (caller gets a normal FileNotFound at open).
        absent = tmp_path / "never-existed.kohakutr"
        assert ensure_latest_version(absent) == absent
        # A target version with no registered migrator chain is rejected.
        with pytest.raises((ValueError, RuntimeError)):
            migrate(v1_path, 99)
