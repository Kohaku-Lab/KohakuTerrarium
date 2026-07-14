"""Behavioral tests for the queue-first batching guarantees.

Written against the PUBLIC surface only (``inject_event`` / ``run`` /
``pause`` / activity observation) — no queue internals — so they encode
the observable contract the redesign targets: simultaneous events batch
into ONE turn with ONE combined delivery banner, instead of each waking
its own serial turn with its own banner.
"""

import asyncio

import pytest

from kohakuterrarium.bootstrap import llm as bootstrap_llm
from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.config_types import AgentConfig, InputConfig, OutputConfig
from kohakuterrarium.core.events import (
    TriggerEvent,
    create_tool_complete_event,
)
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from kohakuterrarium.testing.llm import ScriptedLLM
from kohakuterrarium.testing.output import OutputRecorder


@pytest.fixture
def patched_llm(monkeypatch):
    class _Patch:
        def __init__(self):
            self.script: list = ["OK"]

        def set_script(self, script):
            self.script = script

    p = _Patch()

    def _fake_create(config, llm=None):
        return ScriptedLLM(p.script)

    monkeypatch.setattr(bootstrap_llm, "create_llm_provider", _fake_create)
    from kohakuterrarium.bootstrap import agent_init

    monkeypatch.setattr(agent_init, "create_llm_provider", _fake_create)
    return p


@pytest.fixture
def make_agent(patched_llm, tmp_path):
    def _build(*, script=None):
        if script is not None:
            patched_llm.set_script(script)
        cfg = AgentConfig(
            name="q_agent",
            llm_profile="openai/gpt-4-test",
            model="gpt-4",
            provider="openai",
            api_key_env="",
            system_prompt="You are a test agent.",
            include_tools_in_prompt=True,
            include_hints_in_prompt=False,
            tool_format="bracket",
            agent_path=tmp_path,
            input=InputConfig(type="none"),
            output=OutputConfig(type="stdout"),
        )
        agent = Agent(cfg)
        agent.output_router.default_output = OutputRecorder()
        return agent

    return _build


def _spy_activities(agent) -> list[tuple]:
    captured: list[tuple] = []
    original = agent.output_router.notify_activity

    def spy(activity_type, detail, metadata=None, **kwargs):
        captured.append((activity_type, dict(metadata or {})))
        return original(activity_type, detail, metadata=metadata, **kwargs)

    agent.output_router.notify_activity = spy  # type: ignore[assignment]
    return captured


def _put_after_hops(agent, event, hops: int) -> None:
    """Fire ``agent.inject_event(event)`` after ``hops`` scheduling hops.

    Mimics a completion that reaches the inbox across several
    create_task / done-callback hops: a background TOOL completion lands
    in ~1 hop, a backgroundified SUB-AGENT completion in ~3 (its handle's
    done-callback create_tasks a wrapper that create_tasks the delivery)."""
    if hops <= 0:
        asyncio.ensure_future(agent.inject_event(event))
        return
    asyncio.get_running_loop().call_soon(_put_after_hops, agent, event, hops - 1)


class TestBatchingInvariants:
    async def test_multi_hop_completion_batches_with_direct_one(self, make_agent):
        # A bg TOOL completion reaches the inbox in ~1 hop; a
        # backgroundified SUB-AGENT completion in ~3. A simultaneous MIX
        # must still batch into ONE turn — the settle window spans the hop
        # gap. Regression guard for a single ``sleep(0)``, which coalesces
        # only the shallow path and would slip the sub-agent to turn 2.
        agent = make_agent(script=["batched across hops"])
        await agent.start()
        try:
            captured = _spy_activities(agent)
            # Both fire in the same tick: one lands immediately, one after a
            # done-callback + create_task chain (2 hops behind).
            _put_after_hops(
                agent, create_tool_complete_event(job_id="bash_fast", content="f"), 0
            )
            _put_after_hops(
                agent, create_tool_complete_event(job_id="agent_slow", content="s"), 2
            )
            for _ in range(200):
                await asyncio.sleep(0.01)
                if (
                    agent._event_inbox.empty()
                    and agent._processing_task is None
                    and agent.llm.call_count >= 1
                ):
                    break
            banners = [m for k, m in captured if k == "background_result"]
            assert len(banners) == 1, (
                "a 1-hop and a multi-hop completion firing together must "
                f"batch into ONE delivery banner, got {len(banners)}"
            )
            assert {j["job_id"] for j in banners[0]["jobs"]} == {
                "bash_fast",
                "agent_slow",
            }
            assert agent.llm.call_count == 1
        finally:
            await agent.stop()

    async def test_folded_user_input_advances_turn_behind_nonuser_primary(
        self, make_agent, tmp_path
    ):
        # A fresh user_input FOLDED behind a non-user primary (a background
        # completion claimed in the SAME batch) must still advance the turn,
        # so its session record carries the NEW (turn_index, branch_id) — not
        # the previous turn's stale ids (FE-replay / session-grouping guard).
        from kohakuterrarium.core.events import create_user_input_event
        from kohakuterrarium.session.store import SessionStore

        store = SessionStore(str(tmp_path / "s.kohakutr"))
        store.init_meta(
            session_id="s1",
            config_type="agent",
            config_path="x",
            pwd=str(tmp_path),
            agents=["q_agent"],
        )
        agent = make_agent()
        agent.attach_session_store(store)
        await agent.start()
        try:
            before = agent._turn_index
            # Initial batch: background completion (primary) + fresh user input.
            await agent._begin_batch(
                [
                    create_tool_complete_event(job_id="bash_1", content="done"),
                    create_user_input_event("hi there"),
                ]
            )
            assert agent._turn_index == before + 1, (
                "a fresh user_input in the batch must advance the turn even "
                "when folded behind a non-user primary"
            )
            injected = [
                e
                for e in store.get_events("q_agent")
                if e["type"] == "user_input_injected"
            ]
            assert len(injected) == 1
            assert injected[0]["turn_index"] == before + 1, (
                "the folded user_input's session record must carry the NEW "
                "turn_index, not the previous turn's"
            )
        finally:
            await agent.stop()
            store.close()

    async def test_three_simultaneous_completions_one_turn_one_banner(self, make_agent):
        # Three background completions firing in the same tick while idle
        # must drain as ONE batch → ONE turn → ONE combined delivery
        # banner, not three separate banners / turns.
        agent = make_agent(script=["all three landed"])
        await agent.start()
        try:
            captured = _spy_activities(agent)
            events = [
                create_tool_complete_event(job_id=f"bash_{c}", content=c)
                for c in ("a", "b", "c")
            ]
            # Fire all three "simultaneously" — one gather, one tick.
            await asyncio.gather(*(agent.inject_event(e) for e in events))

            banners = [m for k, m in captured if k == "background_result"]
            assert len(banners) == 1, (
                "three simultaneous completions must produce exactly ONE "
                f"combined delivery banner, got {len(banners)}"
            )
            jobs = banners[0].get("jobs", [])
            assert {j["job_id"] for j in jobs} == {"bash_a", "bash_b", "bash_c"}
            # Exactly ONE turn ran (one processing cycle).
            starts = [k for k, _ in captured if k == "processing_start"]
            # processing_start is emitted as an OutputEvent, not activity —
            # assert the LLM was called exactly once instead (one turn).
            assert agent.llm.call_count == 1
            _ = starts
        finally:
            await agent.stop()

    async def test_mixed_busy_batch_one_banner_and_queue_drains(self, make_agent):
        # tool_complete + subagent_output + user_input arriving DURING a
        # turn fold into it: one combined delivery banner for the two
        # completions, and all three reach the conversation in the turn.
        started = asyncio.Event()
        release = asyncio.Event()

        class _SlowTool(BaseTool):
            @property
            def tool_name(self):
                return "slow"

            @property
            def description(self):
                return "slow"

            @property
            def execution_mode(self):
                return ExecutionMode.DIRECT

            async def _execute(self, args, **kwargs):
                started.set()
                await release.wait()
                return ToolResult(output="slow done")

        agent = make_agent(script=["[/slow]x=1[slow/]", "wrapped up"])
        agent.registry.register_tool(_SlowTool())
        agent.executor.register_tool(_SlowTool())
        await agent.start()
        try:
            captured = _spy_activities(agent)
            turn = asyncio.create_task(agent.inject_input("start", source="web"))
            await asyncio.wait_for(started.wait(), timeout=5)
            # All three fold into the running turn.
            await agent.inject_event(
                create_tool_complete_event(job_id="grep_1", content="hits")
            )
            await agent.inject_event(
                TriggerEvent(type="subagent_output", content="sub", job_id="agent_2")
            )
            await agent.inject_input("also this", source="web")
            release.set()
            await asyncio.wait_for(turn, timeout=10)

            banners = [m for k, m in captured if k == "background_result"]
            assert (
                len(banners) == 1
            ), "the two folded completions must share ONE combined banner"
            assert {j["job_id"] for j in banners[0]["jobs"]} == {"grep_1", "agent_2"}
            # Inbox fully drained at turn end.
            assert agent._event_inbox.empty()
            all_user = "\n".join(
                str(m.content)
                for m in agent.controller.conversation.get_messages()
                if getattr(m, "role", None) == "user"
            )
            assert "also this" in all_user
            assert "grep_1" in all_user and "agent_2" in all_user
        finally:
            release.set()
            await agent.stop()

    async def test_two_concurrent_runs_share_one_turn(self, make_agent):
        # Two await_turn drivers issued together fold into ONE turn — one
        # LLM round, both results resolved (not two serial turns).
        agent = make_agent(script=["single shared round"])
        await agent.start()
        try:
            r1, r2 = await asyncio.gather(
                agent.run("first", raise_on_error=False),
                agent.run("second", raise_on_error=False),
            )
            assert r1.status == "ok" and r2.status == "ok"
            assert "single shared round" in r1.text
            assert "single shared round" in r2.text
            # ONE LLM round total — proof the two runs shared one turn.
            assert agent.llm.call_count == 1
        finally:
            await agent.stop()

    async def test_pause_accumulates_then_resume_drains_in_one_turn(self, make_agent):
        # Events queued while paused all drain together in ONE turn on
        # resume (one LLM round covering the whole burst).
        agent = make_agent(script=["drained the burst"])
        await agent.start()
        try:
            agent.pause()
            for c in ("x", "y", "z"):
                assert (
                    await agent.inject_event(
                        create_tool_complete_event(job_id=f"t_{c}", content=c)
                    )
                    is False
                )
            assert len(agent._event_inbox) == 3
            agent.resume()
            for _ in range(200):
                await asyncio.sleep(0.01)
                if agent._event_inbox.empty() and agent._processing_task is None:
                    if agent.llm.call_count >= 1:
                        break
            assert agent._event_inbox.empty()
            # One turn drained all three (single LLM round).
            assert agent.llm.call_count == 1
        finally:
            await agent.stop()

    async def test_event_appended_at_turn_end_is_processed(self, make_agent):
        # No lost wakeup: an event injected right after a turn settles is
        # picked up by the consumer (the queue never sits non-empty idle).
        agent = make_agent(script=["one", "two"])
        await agent.start()
        try:
            r1 = await agent.run("first", raise_on_error=False)
            assert r1.status == "ok"
            # Inject immediately after the turn ends.
            r2 = await agent.run("second", raise_on_error=False)
            assert r2.status == "ok"
            assert agent._event_inbox.empty()
        finally:
            await agent.stop()


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
