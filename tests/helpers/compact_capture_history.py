"""Live long-tool journey: capture-time bound rounds survive compact, edit, branch, cold."""

import asyncio
import json
from copy import deepcopy

import kohakuterrarium.core.agent_message_history as branch_history
from kohakuterrarium.builtins.user_commands.branch import BranchCommand
from kohakuterrarium.core.branch_continuation import live_branch_path
from kohakuterrarium.core.config_types import AgentConfig
from kohakuterrarium.core.conversation_elide import estimate_messages_tokens
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from kohakuterrarium.modules.user_command.base import UserCommandContext
from kohakuterrarium.session.raw_history import UserMessageSelector
from kohakuterrarium.session.resume import resume_agent
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.testing.llm import ScriptedLLM
from tests.helpers.stable_compact_history import _provider


class _LongTurnEcho(BaseTool):
    def __init__(self):
        super().__init__()
        self.calls = []

    @property
    def tool_name(self):
        return "history_echo"

    @property
    def description(self):
        return "Return a deterministic result."

    @property
    def execution_mode(self):
        return ExecutionMode.DIRECT

    def get_parameters_schema(self):
        return {
            "type": "object",
            "properties": {"i": {"type": "integer"}},
            "required": ["i"],
        }

    async def _execute(self, args, **kwargs):
        self.calls.append(args["i"])
        return ToolResult(output=f"RESULT_{args['i']}")


def _request_tool_pairs(request):
    """Every emitted function_call must pair with exactly one matching output."""
    calls = [i for i in request["input"] if i.get("type") == "function_call"]
    results = [i for i in request["input"] if i.get("type") == "function_call_output"]
    call_ids = [item["call_id"] for item in calls]
    assert len(call_ids) == len(set(call_ids))
    assert call_ids == [item["call_id"] for item in results]
    return call_ids


async def exercise_live_long_tool_journey(tmp_path, monkeypatch, *, tightened=False):
    """Six direct calls, compact, then public edit/branch/cold: ``keep_recent_turns=8``
    consumes rounds 0-2 and retains 3-5; every reconstruction keeps the summary,
    drops consumed text, and preserves the retained pairs."""

    requests = []
    step = 0

    def response(body):
        nonlocal step
        i = step
        step += 1
        if i < 6:
            item = {
                "type": "function_call",
                "id": f"item{i}",
                "call_id": f"call{i}",
                "name": "history_echo",
                "arguments": json.dumps({"i": i}),
            }
            return [
                {"type": "response.output_text.delta", "delta": f"RAW_SEGMENT_{i}"},
                {"type": "response.output_item.done", "item": item},
            ]
        return [{"type": "response.output_text.delta", "delta": "LIVE_FINAL"}]

    path = tmp_path / f"live-long-tools-{tightened}.kohakutr"
    llm = await _provider(requests, response)
    tool = _LongTurnEcho()
    summary = ScriptedLLM(["COMPACT_DIGEST"])
    async with Terrarium() as engine:
        creature = await engine.add_creature(
            AgentConfig(
                name="longturn",
                system_prompt="SYS",
                tool_format="native",
                include_tools_in_prompt=False,
                agent_path=tmp_path,
            ),
            llm=llm,
            tools=[tool],
            session=path,
            io="headless",
            pwd=tmp_path,
            start=True,
        )
        agent = creature.agent
        agent.controller.config.include_job_status = False
        agent.controller.config.elide_tool_results = False
        assert await agent.inject_input("ONE_TURN_GOAL")
        await agent._session_output.drain()
        assert tool.calls == list(range(6))
        manager = agent.compact_manager
        manager.config.keep_recent_turns = 1 if tightened else 8
        if tightened:
            messages = agent.controller.conversation.get_messages()
            live_budget = estimate_messages_tokens(messages[:1] + messages[8:])
            manager.config.target = (
                live_budget + manager._summary_max_tokens() + 0.5
            ) / manager.config.max_tokens
            agent.controller._last_usage = {
                "prompt_tokens": estimate_messages_tokens(messages)
            }
        manager._llm = summary
        assert manager.trigger_compact()
        await asyncio.wait_for(manager.wait_for_current(), 10)
        await agent._session_output.drain()
        assert summary.call_count == 1
        assert "RAW_SEGMENT_0" in str(summary.last_messages)
        assert "RESULT_2" in str(summary.last_messages)
        assert "RAW_SEGMENT_3" not in str(summary.last_messages)
        events = agent.session_store.get_events("longturn")
        complete = next(e for e in reversed(events) if e["type"] == "compact_complete")
        assert complete["messages_compacted"] == 7
        assert len(complete["compact_result_event_ids"]) == 3
        # Settled replay keeps the summary and retained pairs, drops consumed text.
        replayed = branch_history.replay_with_settled_tools(
            events, branch_view=dict(live_branch_path(agent)), include_metadata=True
        )
        replay_text = str(replayed)
        assert "COMPACT_DIGEST" in replay_text
        assert sum(m.get("content") == "ONE_TURN_GOAL" for m in replayed) == int(
            tightened
        )
        for i in range(3):
            assert f"RAW_SEGMENT_{i}" not in replay_text
            assert f"RESULT_{i}" not in replay_text
        for i in range(3, 6):
            assert f"RAW_SEGMENT_{i}" in replay_text
            assert f"RESULT_{i}" in replay_text
        replay_calls = [c["id"] for m in replayed for c in m.get("tool_calls", [])]
        assert (
            replay_calls
            == [m["tool_call_id"] for m in replayed if m.get("role") == "tool"]
            == ["call3", "call4", "call5"]
        )
        snapshot = str(agent.controller.conversation.snapshot_messages())
        assert "COMPACT_DIGEST" in snapshot
        assert "RAW_SEGMENT_0" not in snapshot
        assert snapshot.count("ONE_TURN_GOAL") == int(tightened)
        # Public edit: one request with the summary/pairs, no tool re-execution.
        assert await agent.inject_input("SECOND_TARGET")
        await agent._session_output.drain()
        target = next(
            e
            for e in agent.session_store.get_events("longturn")
            if e["type"] == "user_message" and e["turn_index"] == 2
        )
        before = len(requests)
        assert await agent.edit_and_rerun(
            -1,
            "EDITED_SECOND_TARGET",
            target=UserMessageSelector(target["event_id"], 2, 1),
            branch_view=dict(live_branch_path(agent)),
        )
        assert len(requests) == before + 1
        edited = requests[-1]
        assert "EDITED_SECOND_TARGET" in str(edited)
        assert "COMPACT_DIGEST" in str(edited)
        assert "RAW_SEGMENT_0" not in str(edited)
        for i in range(3):
            assert f"RESULT_{i}" not in str(edited)
        assert tool.calls == list(range(6))
        assert _request_tool_pairs(edited) == ["call3", "call4", "call5"]
        # BranchCommand original/edit switches: zero requests, stable warm view.
        branch = BranchCommand()
        context = UserCommandContext(agent=agent)
        before = len(requests)
        assert not (await branch.execute("2 1", context)).error
        original_view = agent.controller.conversation.snapshot_messages()
        assert not (await branch.execute("2 2", context)).error
        edited_view = agent.controller.conversation.snapshot_messages()
        assert len(requests) == before
        assert original_view != edited_view
        assert "EDITED_SECOND_TARGET" in str(edited_view)
        assert "COMPACT_DIGEST" in str(original_view)
        assert "RAW_SEGMENT_0" not in str(original_view)
        assert not (await branch.execute("2 1", context)).error
        assert agent.controller.conversation.snapshot_messages() == original_view
        saved = deepcopy(agent.session_store.get_events("longturn"))
        await engine.remove_creature(creature)
        agent.session_store.close()
    # Cold resume reruns the switches: zero requests, same dictionaries, no network.
    cold_llm = await _provider(requests)
    cold, cold_store = resume_agent(path, io_mode="plain", llm=cold_llm)
    cold_tool = _LongTurnEcho()
    cold.add_tool(cold_tool)
    try:
        await cold.start()
        cold.controller.config.include_job_status = False
        cold_context = UserCommandContext(agent=cold)
        # Runtime-owned system messages are captured before any switch, so both
        # reconstructions must reuse exactly that block.
        cold_system = [
            m
            for m in cold.controller.conversation.snapshot_messages()
            if m["role"] == "system"
        ]
        assert cold_system
        before = len(requests)
        assert not (await branch.execute("2 1", cold_context)).error
        cold_original = cold.controller.conversation.snapshot_messages()
        assert not (await branch.execute("2 2", cold_context)).error
        cold_edited = cold.controller.conversation.snapshot_messages()
        assert len(requests) == before
        assert [m for m in cold_original if m["role"] == "system"] == cold_system
        assert [m for m in cold_edited if m["role"] == "system"] == cold_system
        assert [m for m in cold_original if m["role"] != "system"] == [
            m for m in original_view if m["role"] != "system"
        ]
        assert [m for m in cold_edited if m["role"] != "system"] == [
            m for m in edited_view if m["role"] != "system"
        ]
        assert cold.controller.llm is cold_llm
        assert cold_tool.calls == []
        await cold._session_output.drain()
        assert cold_store.get_events("longturn")[: len(saved)] == saved
    finally:
        await cold.stop()
        cold_store.close()
