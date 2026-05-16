"""End-to-end journey: programmatic single-creature usage.

ONE fat journey test. It builds a creature the way a Python user would
(``Agent.from_path`` on a real on-disk config, wrapped in a
:class:`Creature` inside a real :class:`Terrarium` engine) and drives a
*complete* session start to finish — multi-turn chat, a real builtin
tool call, a sub-agent dispatch, a mid-session model switch, a plugin
toggle, a runtime-setting adjustment, an interrupt, a resume from the
``.kohakutr`` store, a branch/regenerate, and a memory search — every
milestone pinned by a behaviour assertion.

The ONLY seam is the LLM. Both bootstrap import sites
(``bootstrap.llm.create_llm_provider`` and
``bootstrap.agent_init.create_llm_provider``) are monkeypatched to a
:class:`ScriptedLLM`; the model-switch path's two profile helpers in
``core.agent_model`` are part of that same seam (no live provider, no
network). Everything else — the engine, the real ``Agent``, the real
``SessionStore`` on ``tmp_path``, the ``write`` builtin tool, the
``SubAgentManager``, the ``budget`` catalog plugin, ``SessionMemory``
over a deterministic embedder — is the production collaborator.
"""

import asyncio

import numpy as np
import pytest

from kohakuterrarium.bootstrap import agent_init as _agent_init_mod
from kohakuterrarium.bootstrap import llm as _bootstrap_llm_mod
from kohakuterrarium.core import agent_model as _agent_model_mod
from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.conversation import Conversation
from kohakuterrarium.core.events import (
    EventType,
    TriggerEvent,
    create_user_input_event,
)
from kohakuterrarium.llm.profile_types import LLMProfile
from kohakuterrarium.modules.subagent.config import SubAgentConfig
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from kohakuterrarium.modules.trigger.base import BaseTrigger
from kohakuterrarium.session.embedding import BaseEmbedder
from kohakuterrarium.session.memory import SessionMemory
from kohakuterrarium.session.resume import resume_agent
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.creature_host import Creature
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.testing.llm import ScriptedLLM, ScriptEntry

pytestmark = pytest.mark.timeout(60)


# ── deterministic collaborators ──────────────────────────────────


class _HashEmbedder(BaseEmbedder):
    """Deterministic hashed bag-of-words embedder.

    Identical text maps to an identical vector, so an exact-phrase
    semantic query is its own nearest neighbour — the memory-search
    milestone can assert an EXACT hit.
    """

    dimensions = 64

    def encode(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dimensions), dtype=np.float32)
        for row, text in enumerate(texts):
            for token in text.lower().split():
                out[row, hash(token) % self.dimensions] += 1.0
            norm = float(np.linalg.norm(out[row]))
            if norm > 0:
                out[row] /= norm
        return out


class _BlockingTool(BaseTool):
    """DIRECT tool that blocks forever — drives the interrupt milestone."""

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()

    @property
    def tool_name(self) -> str:
        return "block"

    @property
    def description(self) -> str:
        return "Blocks until cancelled."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args, **kwargs) -> ToolResult:
        self.started.set()
        await asyncio.sleep(3600)
        return ToolResult(output="never")


class _SlowBackgroundTool(BaseTool):
    """BACKGROUND tool: completes after a short delay.

    A ``BACKGROUND`` tool is promoted on dispatch — the turn ends
    immediately and the completion arrives later as its own
    ``tool_complete`` TriggerEvent, exercising the executor's
    background path and ``_on_bg_complete`` routing.
    """

    @property
    def tool_name(self) -> str:
        return "slowbg"

    @property
    def description(self) -> str:
        return "A background tool that finishes after a short delay."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.BACKGROUND

    async def _execute(self, args, **kwargs) -> ToolResult:
        await asyncio.sleep(0.05)
        return ToolResult(output="bg-task-finished")


class _OneShotTrigger(BaseTrigger):
    """A real BaseTrigger that fires one event then idles.

    Drives the unified ``TriggerEvent`` model through the real
    ``TriggerManager._run_loop`` — the autonomous-agent path a
    ``TimerTrigger`` takes. ``fired`` lets the journey wait for the
    trigger loop to actually emit.
    """

    def __init__(self, prompt: str) -> None:
        super().__init__(prompt=prompt)
        self._gate = asyncio.Event()
        self._done = False
        self.fired = asyncio.Event()

    async def wait_for_trigger(self) -> TriggerEvent | None:
        if self._done or not self._running:
            await self._gate.wait()
            return None
        self._done = True
        self.fired.set()
        return self._create_event(
            EventType.TIMER, content=self.prompt, context={"trigger": "oneshot"}
        )

    async def _on_stop(self) -> None:
        self._gate.set()


# ── fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def patched_llm(monkeypatch):
    """Inject a ScriptedLLM into BOTH bootstrap entry points.

    ``Agent.from_path`` builds the controller LLM via
    ``bootstrap.llm.create_llm_provider``; ``bootstrap.agent_init``
    imports the symbol directly. Both must be patched or a resumed
    agent reaches for a real provider. The returned holder lets the
    journey set its script before it builds (or resumes) the agent.
    """
    holder: dict[str, list] = {"script": ["OK"]}

    def _fake_create(config, llm_override=None):
        return ScriptedLLM(holder["script"])

    monkeypatch.setattr(_bootstrap_llm_mod, "create_llm_provider", _fake_create)
    monkeypatch.setattr(_agent_init_mod, "create_llm_provider", _fake_create)
    return holder


@pytest.fixture
def patched_model_switch(monkeypatch):
    """Make ``Agent.switch_model`` deterministic without a live provider.

    ``switch_model`` resolves a profile then builds a provider — the
    same external dependency the bootstrap seam stubs. Here the two
    ``core.agent_model`` helpers are pointed at a fixed in-memory
    ``LLMProfile`` and a fresh ``ScriptedLLM``; the *swapping* logic
    (binding ``agent.llm`` / ``controller.llm``, caching the
    identifier, emitting ``session_info``) stays the real Agent code.
    """
    profiles: dict[str, LLMProfile] = {
        "test/fast-model": LLMProfile(
            name="fast-model",
            model="fast-model",
            provider="test",
            backend_type="openai",
        )
    }
    built: list[ScriptedLLM] = []

    def _resolve(data, llm_override=None):
        key = llm_override or data.get("llm")
        return profiles.get(key)

    def _create_from_name(name):
        llm = ScriptedLLM(["switched-model reply"])
        built.append(llm)
        return llm

    monkeypatch.setattr(_agent_model_mod, "resolve_controller_llm", _resolve)
    monkeypatch.setattr(
        _agent_model_mod, "create_llm_from_profile_name", _create_from_name
    )
    return {"profiles": profiles, "built": built}


def _write_config(config_dir, name: str = "pilot") -> str:
    """Write a minimal but complete creature config dir.

    Built from a file (not an in-memory ``AgentConfig``) so the
    ``.kohakutr`` store records a real ``config_path`` and
    ``resume_agent`` can rebuild from it. ``write`` is a real builtin
    tool — the journey calls it for the tool-call milestone.
    """
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.yaml").write_text(
        f"name: {name}\n"
        "controller:\n"
        "  model: scripted-model\n"
        "  tool_format: bracket\n"
        "  include_tools_in_prompt: true\n"
        "  include_hints_in_prompt: false\n"
        "system_prompt: |\n"
        "  You are a programmatic test pilot.\n"
        "input:\n"
        "  type: none\n"
        "output:\n"
        "  type: stdout\n"
        "tools:\n"
        "  - name: write\n"
        "    type: builtin\n",
        encoding="utf-8",
    )
    return str(config_dir)


async def _drain_chat(creature: Creature, message: str) -> str:
    """``Creature.chat`` consumed to completion — the canonical drive."""
    chunks: list[str] = []
    async for chunk in creature.chat(message):
        chunks.append(chunk)
    return "".join(chunks)


def _assistant_text(agent: Agent) -> str:
    last = agent.controller.conversation.get_last_assistant_message()
    assert last is not None, "expected an assistant message in conversation"
    return last.get_text_content()


def _convo_text(agent: Agent) -> str:
    return " ".join(
        m.get_text_content() for m in agent.controller.conversation.get_messages()
    )


# ── the journey ──────────────────────────────────────────────────


class TestProgCreatureJourney:
    """One fat end-to-end test for the programmatic single-creature path."""

    async def test_full_creature_session(
        self, patched_llm, patched_model_switch, tmp_path
    ):
        config_path = _write_config(tmp_path / "creature", name="pilot")
        session_path = tmp_path / "pilot.kohakutr.v2"
        out_file = tmp_path / "artifact.txt"

        # The controller script for the live phase. Each entry is keyed
        # by a substring of the triggering user turn so the run is
        # order-independent and intent-explicit.
        patched_llm["script"] = [
            ScriptEntry("Hello, I am the pilot.", match="introduce"),
            ScriptEntry("Second turn acknowledged.", match="still there"),
            # Tool-call turn: write the artifact file via the real
            # builtin ``write`` tool (bracket form: @@path + body).
            ScriptEntry(
                f"[/write]\n@@path={out_file}\nartifact-body\n[write/]",
                match="write the artifact",
            ),
            ScriptEntry("The write tool ran.", match="Created"),
            # Sub-agent dispatch turn.
            ScriptEntry("[/explore]survey it[explore/]", match="delegate"),
            ScriptEntry("summary: sub-agent reported back", match="explored:done"),
        ]

        # ``pwd`` is the creature's working dir — the ``write`` builtin
        # tool's file guard resolves relative paths against it and
        # rejects writes outside it, so the artifact lives under it.
        agent = Agent.from_path(config_path, pwd=str(tmp_path))
        creature = Creature(creature_id="pilot", name="pilot", agent=agent)

        # A dedicated sub-agent with its OWN deterministic LLM — the
        # VERTICAL composition level, registered exactly as
        # ``bootstrap.subagents`` does (manager + registry).
        sa_cfg = SubAgentConfig(
            name="explore",
            description="Survey a codebase.",
            tools=[],
            system_prompt="You are an explorer.",
            max_turns=1,
        )
        agent.subagent_manager.register(sa_cfg)
        agent.registry.register_subagent("explore", sa_cfg)
        agent.subagent_manager.llm = ScriptedLLM(["explored:done"])

        async with Terrarium() as engine:
            # ---- milestone 1: build creature in a real engine --------
            added = await engine.add_creature(creature)
            assert engine.get_creature("pilot") is creature
            assert added.graph_id != ""
            assert creature.is_running is True

            # ---- milestone 2: multi-turn chat, conversation grows ----
            out1 = await _drain_chat(creature, "introduce yourself")
            assert "Hello, I am the pilot." in out1
            assert agent.llm.call_count == 1
            msgs_after_t1 = len(agent.controller.conversation.get_messages())

            out2 = await _drain_chat(creature, "are you still there")
            assert "Second turn acknowledged." in out2
            assert agent.llm.call_count == 2
            # The conversation accumulated turn 2 on top of turn 1.
            assert len(agent.controller.conversation.get_messages()) > msgs_after_t1

            # ---- milestone 3: a real builtin tool runs ---------------
            out3 = await _drain_chat(creature, "write the artifact file")
            assert "The write tool ran." in out3
            # The tool actually touched the filesystem.
            assert out_file.read_text(encoding="utf-8") == "artifact-body"
            # The tool result fed the follow-up turn's input — the
            # ``write`` tool reports the path it created.
            assert "Created" in _convo_text(agent)
            # Two controller rounds for this turn: call + wrap-up.
            assert agent.llm.call_count == 4

            # ---- milestone 4: sub-agent dispatch routes back ---------
            out4 = await _drain_chat(creature, "delegate the survey")
            assert "summary: sub-agent reported back" in out4
            # The sub-agent's own LLM output reached the parent
            # conversation as a tool-result message.
            assert "explored:done" in _convo_text(agent)
            assert agent.llm.call_count == 6

            # ---- milestone 5: change the model mid-session -----------
            original_llm = agent.llm
            identifier = agent.switch_model("test/fast-model")
            assert identifier == "test/fast-model"
            # The swap took on both the agent and its controller.
            assert agent.llm is not original_llm
            assert agent.controller.llm is agent.llm
            assert agent.llm_identifier() == "test/fast-model"
            # The next turn is served by the switched LLM, proving the
            # swap is live end-to-end.
            out5 = await _drain_chat(creature, "anything at all")
            assert "switched-model reply" in out5
            assert agent.llm.call_count == 1  # fresh provider, first call
            assert original_llm.call_count == 6  # original untouched after switch

            # ---- milestone 6: toggle a plugin ------------------------
            # ``budget`` is a catalog plugin — registered-but-disabled
            # on every agent. Flip it on, then back off.
            assert agent.plugins.is_enabled("budget") is False
            assert agent.plugins.enable("budget") is True
            assert agent.plugins.is_enabled("budget") is True
            assert agent.plugins.disable("budget") is True
            assert agent.plugins.is_enabled("budget") is False

            # ---- milestone 7: adjust a runtime setting ---------------
            # The scratchpad is the agent's read-write runtime state;
            # exercise the full key/value surface — set, get, contains,
            # list_keys, to_dict, delete, clear.
            sp = agent.session.scratchpad
            assert sp.get("focus") is None
            assert "focus" not in sp
            sp.set("focus", "navigation")
            sp.set("phase", "cruise")
            assert sp.get("focus") == "navigation"
            assert "focus" in sp
            assert len(sp) >= 2
            assert set(sp.list_keys()) >= {"focus", "phase"}
            assert sp.to_dict()["phase"] == "cruise"
            # ``delete`` returns True for an existing key, False otherwise.
            assert sp.delete("phase") is True
            assert sp.delete("phase") is False
            assert "phase" not in sp
            # ``to_prompt_section`` renders the visible keys as markdown.
            assert "focus" in sp.to_prompt_section()
            # ``clear`` wipes everything.
            sp.clear()
            assert len(sp) == 0
            assert sp.get("focus") is None
            # Re-set ``focus`` so the rest of the journey is consistent.
            sp.set("focus", "navigation")
            assert sp.get("focus") == "navigation"

            # ---- milestone 7b: introspection + system-prompt edit ----
            # ``get_state`` is the TUI/API monitoring snapshot.
            state = agent.get_state()
            assert state["name"] == "pilot"
            assert state["running"] is True
            assert state["message_count"] == len(
                agent.controller.conversation.get_messages()
            )
            # System prompt hot-edit: append keeps the original text.
            assert "programmatic test pilot" in agent.get_system_prompt()
            agent.update_system_prompt("Runtime addendum: stay terse.")
            assert "Runtime addendum: stay terse." in agent.get_system_prompt()
            assert "programmatic test pilot" in agent.get_system_prompt()

            # ---- milestone 7c: plugin option override ----------------
            # ``budget`` is a catalog plugin; override its turn_budget
            # and confirm the merged options land on the live instance.
            applied = agent.plugin_options.set(
                "budget", {"turn_budget": {"soft": 3, "hard": 8}}
            )
            assert applied["turn_budget"] == {"soft": 3, "hard": 8}
            assert agent.plugins.get_plugin("budget").options["turn_budget"] == {
                "soft": 3,
                "hard": 8,
            }

            # ---- milestone 7d: runtime working-directory switch ------
            # ``agent.workspace`` switches the tool-side cwd without
            # rebuilding the agent. The new dir must exist.
            new_cwd = tmp_path / "pilot-workspace"
            new_cwd.mkdir()
            resolved_cwd = agent.workspace.set(new_cwd)
            assert resolved_cwd == str(new_cwd.resolve())
            assert str(agent.executor._working_dir) == str(new_cwd.resolve())
            assert agent.workspace.get() == str(new_cwd.resolve())
            # Switch it back so later file-touching milestones are sane.
            agent.workspace.set(tmp_path)

            # ---- milestone 7e: hot-plug a trigger (autonomous path) --
            # ``Agent.add_trigger`` runs the real TriggerManager: it
            # starts the trigger and spawns its run loop. When the
            # trigger emits, the loop drives ``_process_event`` exactly
            # like a TimerTrigger would. Bind a fresh scripted LLM for
            # this phase — the switched-model LLM from milestone 5 was
            # consumed by that turn.
            trigger_llm = ScriptedLLM(
                [ScriptEntry("autonomous turn handled", match="wake the pilot")]
            )
            agent.llm = trigger_llm
            agent.controller.llm = trigger_llm
            trig = _OneShotTrigger("wake the pilot")
            trigger_id = await agent.add_trigger(trig)
            assert agent.trigger_manager.get(trigger_id).running is True
            await asyncio.wait_for(trig.fired.wait(), timeout=5.0)
            for _ in range(100):
                if "autonomous turn handled" in _assistant_text(agent):
                    break
                await asyncio.sleep(0.02)
            assert "autonomous turn handled" in _assistant_text(agent)
            assert trigger_llm.call_count == 1
            # Remove it cleanly off the running creature.
            assert await agent.remove_trigger(trigger_id) is True
            assert agent.trigger_manager.get(trigger_id) is None

            # ---- milestone 7f: a BACKGROUND tool completes async -----
            # A ``BACKGROUND`` tool is promoted on dispatch — the turn
            # ends right away; the completion arrives later as its own
            # ``tool_complete`` TriggerEvent and drives a follow-up
            # turn. This is the full async non-blocking tool path.
            slowbg = _SlowBackgroundTool()
            agent.registry.register_tool(slowbg)
            agent.executor.register_tool(slowbg)
            bg_llm = ScriptedLLM(
                [
                    ScriptEntry(
                        "dispatching the bg job [/slowbg][slowbg/]",
                        match="kick off background",
                    ),
                    ScriptEntry("bg job acknowledged", match="bg-task-finished"),
                ]
            )
            agent.llm = bg_llm
            agent.controller.llm = bg_llm
            out_bg = await _drain_chat(creature, "kick off background work")
            assert "dispatching the bg job" in out_bg
            # The completion drives a fresh follow-up turn whose reply
            # acknowledges the real background output.
            for _ in range(100):
                if "bg job acknowledged" in _assistant_text(agent):
                    break
                await asyncio.sleep(0.02)
            assert "bg job acknowledged" in _assistant_text(agent)
            bg_convo = _convo_text(agent)
            # The real background output reached the conversation.
            assert "bg-task-finished" in bg_convo
            # Regression guard for B-fat2-core-1 (FIXED): a ``BACKGROUND``
            # tool's completion was processed TWICE — once via the
            # executor's ``_on_complete`` callback and again via the
            # ``backgroundify`` handle's ``_on_backgroundify_complete`` —
            # so the controller ran an extra follow-up turn (call_count
            # climbed to 3 instead of 2). The fix suppresses the handle
            # callback whenever the executor already delivers the
            # completion. Expected: exactly 2 calls (dispatch + one
            # follow-up).
            for _ in range(100):
                if bg_llm.call_count >= 2:
                    break
                await asyncio.sleep(0.02)
            # Settle: on the unfixed double-fire code a 3rd call would be
            # scheduled here by the duplicate completion.
            await asyncio.sleep(0.3)
            assert bg_llm.call_count == 2

            # ---- milestone 7g: edit_and_rerun rejects a bad index ----
            # An out-of-range message index resolves to None — the call
            # returns False and runs no LLM call (no fresh turn).
            calls_pre_bad_edit = bg_llm.call_count
            bad_edit = await agent.edit_and_rerun(
                message_idx=99999, new_content="never applied"
            )
            assert bad_edit is False
            assert bg_llm.call_count == calls_pre_bad_edit

            # ---- milestone 8: interrupt a turn mid-flight ------------
            block = _BlockingTool()
            agent.registry.register_tool(block)
            agent.executor.register_tool(block)
            # Bind a fresh scripted LLM for the interrupt phase: the
            # first turn hangs on the blocking tool, a later turn
            # recovers cleanly.
            interrupt_llm = ScriptedLLM(
                [
                    ScriptEntry("[/block][block/]", match="hang now"),
                    ScriptEntry("recovered after interrupt", match="recovered ok"),
                ]
            )
            agent.llm = interrupt_llm
            agent.controller.llm = interrupt_llm

            inject_task = asyncio.create_task(
                agent.inject_input("hang now", source="chat")
            )
            await asyncio.wait_for(block.started.wait(), timeout=5.0)
            agent.interrupt()
            await asyncio.wait_for(inject_task, timeout=5.0)
            # The agent survived the interrupt — still alive, no leftover
            # processing task.
            assert agent.is_running is True
            assert agent._processing_task is None
            # A fresh turn runs normally after the interrupt.
            out_recover = await _drain_chat(creature, "recovered ok")
            assert "recovered after interrupt" in out_recover

        # Engine __aexit__ stopped the creature cleanly.
        assert creature.is_running is False

        # ---- milestone 9: resume from the .kohakutr store ------------
        # Attach a real store and replay the recorded session into a
        # fresh creature so the resume path has events to rebuild from.
        record_store = SessionStore(str(session_path))
        record_store.init_meta(
            session_id="pilot",
            config_type="agent",
            config_path=config_path,
            pwd=str(tmp_path),
            agents=["pilot"],
        )
        patched_llm["script"] = [
            ScriptEntry("Resumed pilot, turn one.", match="first recorded"),
            ScriptEntry("Resumed pilot, turn two.", match="second recorded"),
        ]
        rec_agent = Agent.from_path(config_path, pwd=str(tmp_path))
        rec_agent.attach_session_store(record_store)
        await rec_agent.start()
        try:
            await rec_agent._process_event(
                create_user_input_event("first recorded question")
            )
            await rec_agent._process_event(
                create_user_input_event("second recorded question")
            )
        finally:
            await rec_agent.stop()
        recorded_user_turns = [
            e["content"]
            for e in record_store.get_events("pilot")
            if e["type"] == "user_input"
        ]
        assert recorded_user_turns == [
            "first recorded question",
            "second recorded question",
        ]
        record_store.close()

        resumed_agent, resumed_store = resume_agent(session_path)
        await resumed_agent.start()
        try:
            # The resumed conversation, rebuilt from the event log,
            # carries every recorded user turn in order.
            resumed_users = [
                m["content"]
                for m in resumed_agent.controller.conversation.to_messages()
                if m.get("role") == "user"
            ]
            recovered = [m for m in resumed_users if m in recorded_user_turns]
            assert recovered == recorded_user_turns
            # An assistant turn from the recorded run survived the trip.
            assert any(
                m.get("role") == "assistant" and "Resumed pilot" in str(m["content"])
                for m in resumed_agent.controller.conversation.to_messages()
            )
            # Turn/branch counters were restored from the event log.
            assert resumed_agent._turn_index == 2
            assert resumed_agent._branch_id == 1

            # ---- milestone 10: branch / regenerate a turn ------------
            # Re-running the tail turn opens a NEW branch under the same
            # turn index — the retry-button bookkeeping.
            regen_llm = ScriptedLLM(["regenerated turn two"])
            resumed_agent.llm = regen_llm
            resumed_agent.controller.llm = regen_llm
            await resumed_agent.regenerate_last_response()
            assert "regenerated turn two" in _assistant_text(resumed_agent)
            # Same turn, fresh branch — and the event log recorded it.
            assert resumed_agent._turn_index == 2
            assert resumed_agent._branch_id == 2
            assert resumed_agent._max_branch_id_for_turn(2) == 2

            # ---- milestone 10b: conversation JSON round-trip ---------
            # The live conversation serialises to JSON and rebuilds with
            # identical roles + text + context length — the snapshot
            # primitive ``session/store.py`` persists. Done BEFORE any
            # destructive history op so the round-trip sees real content.
            conv = resumed_agent.controller.conversation
            chars = conv.get_context_length()
            assert chars > 0
            assert len(conv) == len(conv.get_messages())
            rebuilt = Conversation.from_json(conv.to_json())
            assert [m.role for m in rebuilt.get_messages()] == [
                m.role for m in conv.get_messages()
            ]
            assert rebuilt.get_context_length() == chars
            assert (
                rebuilt.get_last_assistant_message().get_text_content()
                == conv.get_last_assistant_message().get_text_content()
            )
        finally:
            await resumed_agent.stop()

        # ---- milestone 11: memory search over the session -----------
        # FTS keyword search finds the EXACT recorded user turn.
        fts_memory = SessionMemory(
            str(session_path), embedder=None, store=resumed_store
        )
        events = resumed_store.get_events("pilot")
        assert fts_memory.index_events("pilot", events) > 0
        fts_hits = fts_memory.search("recorded", mode="fts", k=5)
        assert fts_hits, "FTS search returned nothing"
        fts_contents = {h.content for h in fts_hits}
        assert "first recorded question" in fts_contents
        assert "second recorded question" in fts_contents

        # Semantic search over the deterministic embedder: the exact
        # phrase embeds to the exact stored vector → top hit is itself.
        sem_memory = SessionMemory(
            str(session_path), embedder=_HashEmbedder(), store=resumed_store
        )
        sem_memory.index_events("pilot", events)
        assert sem_memory.has_vectors
        sem_hits = sem_memory.search("first recorded question", mode="semantic", k=5)
        assert sem_hits, "semantic search returned nothing"
        assert sem_hits[0].content == "first recorded question"
        resumed_store.close()
