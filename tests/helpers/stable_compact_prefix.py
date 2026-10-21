"""Anonymous systemless compact-prefix reuse through real edit/rerun and resume."""

import json
from copy import deepcopy

from kohakuterrarium.core.branch_continuation import live_branch_path
from kohakuterrarium.core.config_types import AgentConfig
from kohakuterrarium.core.event_inbox import EventEnvelope
from kohakuterrarium.core.events import TriggerEvent
from kohakuterrarium.errors import ConflictError
from kohakuterrarium.session.raw_history import (
    UserMessageSelector,
    append_user_event_pair,
)
from kohakuterrarium.session.resume import resume_agent
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.testing.llm import ScriptedLLM

from tests.helpers.stable_compact_history import (
    _CompactMediaTool,
    _provider,
    _request_text,
    _tool_response,
)

_SUMMARY_ONE = "SUMMARY_ROUND_ONE"
_SUMMARY_TWO = "SUMMARY_ROUND_TWO"
_UNKNOWN = "UNKNOWNRESULT_GAMMA"
_PREAMBLE_MARKER = "\n\nCHECKPOINT_PREAMBLE_ROUND"


def _marker(round_number):
    return f"[Previous context summary (compact round {round_number})]"


def _user_event(events, turn_index, branch_id=1):
    return next(
        event
        for event in events
        if event["type"] == "user_message"
        and event["turn_index"] == turn_index
        and event["branch_id"] == branch_id
    )


async def _fold_unknown_result(agent):
    """Fold a genuine unresolved subagent result into the live turn."""
    event = TriggerEvent(type="subagent_output", content=_UNKNOWN)
    agent._event_inbox.put(EventEnvelope(event))
    assert await agent._drain_mid_turn_pending_inputs(agent.controller) == 1
    folded = agent.controller.conversation.get_messages()[-1]
    assert _UNKNOWN in str(folded.content)
    assert folded.metadata["kind"] == "injected"
    assert folded.metadata["origin_known"] is False


async def _compact(agent, summary):
    manager = agent.compact_manager
    manager.config.keep_recent_turns = 1
    manager._llm = summary
    round_before = manager._compact_count
    assert manager.trigger_compact()
    await manager.wait_for_current()
    await agent._session_output.drain()
    assert manager._compact_count == round_before + 1


def _prompt_text(request):
    """Every prompt-carrying field of a provider request: items and system text."""
    return f"{_request_text(request)} {request.get('instructions') or ''}"


def _round_items(request):
    """Provider payload items of one canonical tool round, in send order."""
    return [
        item
        for item in request["input"]
        if item.get("type") in ("function_call", "function_call_output")
    ]


async def exercise_compact_prefix_history(tmp_path):
    """Reuse an unknown-source summary without expanding raw ancestors."""
    requests = []
    latest_summary = "SUMMARY_ROUND_THREE"
    saved_summary = ScriptedLLM([_SUMMARY_ONE, _SUMMARY_TWO, latest_summary])
    llm = await _provider(requests)
    path = tmp_path / "stable-prefix.kohakutr"
    async with Terrarium() as engine:
        creature = await engine.add_creature(
            AgentConfig(
                name="stableprefix",
                system_prompt="",
                include_tools_in_prompt=False,
                agent_path=tmp_path,
            ),
            llm=llm,
            session=path,
            io="headless",
            pwd=tmp_path,
            start=True,
        )
        agent = creature.agent
        agent.controller.config.include_job_status = False
        agent.controller.conversation._messages = [
            message
            for message in agent.controller.conversation.get_messages()
            if message.role != "system"
        ]
        assert not any(
            message.role == "system"
            for message in agent.controller.conversation.get_messages()
        )
        budget = agent.compact_manager.config.max_tokens
        store = agent.session_store
        assert await agent.inject_input("RAWANCESTOR_ALPHA")
        assert await agent.inject_input("RAWANCESTOR_BETA")
        assert await agent.inject_input("RAWANCESTOR_MIDDLE")
        await agent._session_output.drain()

        await _compact(agent, saved_summary)
        first = next(
            event
            for event in reversed(store.get_events("stableprefix"))
            if event["type"] == "compact_complete"
        )
        assert first["round"] == 1
        assert first["compact_source_path"] == [[1, 1], [2, 1]]

        await _fold_unknown_result(agent)
        assert await agent.inject_input("LIVE_DELTA")
        await agent._session_output.drain()

        await _compact(agent, saved_summary)
        events = store.get_events("stableprefix")
        second = next(
            event for event in reversed(events) if event["type"] == "compact_complete"
        )
        assert second["round"] == 2
        assert second["compact_source_path"] is None
        assert saved_summary.call_count == 2
        digest = str(saved_summary.last_messages)
        assert _SUMMARY_ONE in digest
        assert _UNKNOWN in digest

        for text in ("LIVE_EPSILON", "LIVE_ZETA"):
            assert await agent.inject_input(text)
        await agent._session_output.drain()
        await _compact(agent, saved_summary)
        third = next(
            e
            for e in reversed(store.get_events("stableprefix"))
            if e["type"] == "compact_complete"
        )
        assert third["compact_source_path"] is None
        assert third.get("replaced_from_event_id") == first["replaced_from_event_id"]
        assert third["replaced_to_event_id"] > second["replaced_to_event_id"]

        assert await agent.inject_input("TARGET_ECHO")
        await agent._session_output.drain()
        original = dict(live_branch_path(agent))
        original_messages = deepcopy(agent.controller.conversation.snapshot_messages())
        baseline = deepcopy(requests[-1])
        assert latest_summary in _request_text(baseline)
        assert "RAWANCESTOR_ALPHA" not in _request_text(baseline)

        target = _user_event(store.get_events("stableprefix"), 7)
        before = len(requests)
        assert await agent.edit_and_rerun(
            -1,
            "EDITED_TARGET",
            target=UserMessageSelector(target["event_id"], 7, 1),
            branch_view=original,
        )
        assert len(requests) == before + 1
        edited = _request_text(requests[-1])
        assert "EDITED_TARGET" in edited
        assert "RAWANCESTOR_ALPHA" not in edited
        assert "RAWANCESTOR_BETA" not in edited
        assert edited.count(_marker(3)) == 1
        assert len(requests[-1]["input"]) <= len(baseline["input"])

        await agent._session_output.drain()
        edited_view = dict(live_branch_path(agent))
        edited_messages = deepcopy(agent.controller.conversation.snapshot_messages())
        conversation_identity = agent.controller.conversation
        count_before_switch = len(requests)
        for view, expected in (
            (original, original_messages),
            (edited_view, edited_messages),
            (original, original_messages),
            (edited_view, edited_messages),
        ):
            assert await agent.inject_input(f"/branch 7 {view[7]}")
            await agent._session_output.drain()
            assert agent.controller.conversation is conversation_identity
            assert agent.controller.conversation.snapshot_messages() == expected
            assert live_branch_path(agent) == view
            assert store.state["stableprefix:branch_view"] == {
                str(turn): branch for turn, branch in view.items()
            }
            assert agent.compact_manager._compact_count == 3
            assert len(requests) == count_before_switch
            assert saved_summary.call_count == 3

        before = len(requests)
        await agent.regenerate_last_response()
        assert len(requests) == before + 1
        assert "RAWANCESTOR_ALPHA" not in _request_text(requests[-1])

        ancestor = _user_event(store.get_events("stableprefix"), 1)
        before = len(requests)
        assert await agent.edit_and_rerun(
            -1,
            "EDITED_ANCESTOR",
            target=UserMessageSelector(ancestor["event_id"], 1, 1),
            branch_view=original,
        )
        assert len(requests) == before + 1
        ancestor_text = _request_text(requests[-1])
        assert _SUMMARY_ONE not in ancestor_text
        assert _SUMMARY_TWO not in ancestor_text
        assert latest_summary not in ancestor_text

        before = len(requests)
        assert await agent.inject_input("/branch 7 1")
        assert live_branch_path(agent) == original
        assert agent.controller.conversation.snapshot_messages() == original_messages
        assert len(requests) == before
        await agent.inject_input("return original")
        await agent._session_output.drain()
        assert len(requests) == before + 1
        returned = _request_text(requests[-1])
        assert latest_summary in returned
        assert "RAWANCESTOR_ALPHA" not in returned

        conversation = deepcopy(agent.controller.conversation.to_messages())
        state = deepcopy(store.get_events("stableprefix"))
        checkpoint = deepcopy(store.load_branch_snapshot("stableprefix", original))
        offered = len(requests)
        original_max = agent.compact_manager.config.max_tokens
        agent.compact_manager.config.max_tokens = 8
        try:
            target = _user_event(state, 7)
            try:
                await agent.edit_and_rerun(
                    -1,
                    "OVERSIZED_EDIT",
                    target=UserMessageSelector(target["event_id"], 7, 1),
                    branch_view=original,
                )
            except ConflictError:
                pass
            else:
                raise AssertionError("oversized reconstruction was not rejected")
        finally:
            agent.compact_manager.config.max_tokens = original_max
        assert len(requests) == offered
        assert agent.controller.conversation.to_messages() == conversation
        assert store.get_events("stableprefix") == state
        assert store.load_branch_snapshot("stableprefix", original) == checkpoint
        assert saved_summary.call_count == 3
        assert agent.compact_manager.config.max_tokens == budget

        await agent._session_output.drain()
        saved = deepcopy(store.get_events("stableprefix"))
        await engine.remove_creature(creature)
        store.close()

    cold_llm = await _provider(requests)
    restored, cold_store = resume_agent(path, io_mode="plain", llm=cold_llm)
    try:
        await restored.start()
        restored.controller.config.include_job_status = False
        assert await restored.inject_input("/branch 7 2")
        assert restored.controller.conversation.snapshot_messages() == edited_messages
        assert await restored.inject_input("/branch 7 1")
        assert live_branch_path(restored)[7] == 1
        previous = len(requests)
        completed = await restored.run("cold original", timeout=10)
        assert completed.ok
        assert len(requests) == previous + 1
        cold_text = _request_text(requests[-1])
        assert "cold original" in cold_text
        assert latest_summary in cold_text
        assert "RAWANCESTOR_ALPHA" not in cold_text
        assert "EDITED_ANCESTOR" not in cold_text
        assert saved_summary.call_count == 3
        await restored._session_output.drain()
        events = cold_store.get_events("stableprefix")
        assert events[: len(saved)] == saved
        assert not any(
            event["type"] in ("tool_call", "tool_result", "compact_complete")
            for event in events[len(saved) :]
        )
    finally:
        await restored.stop()
        cold_store.close()
    await _exercise_cutoff_fallback(tmp_path, requests)
    await _exercise_tool_round_repair(tmp_path, requests)


async def _exercise_cutoff_fallback(tmp_path, requests):
    """Exercise public edit and cold resume of a legacy cutoff checkpoint.

    The persisted fixture has an unpositioned message and watermark equal to
    its cutoff. The target and clear event are seeded through SessionStore;
    the initial turn, targeted edit and cold follow-up use the running agent.
    """
    path = tmp_path / "cutoff-legacy.kohakutr"
    llm = await _provider(requests)
    async with Terrarium() as engine:
        creature = await engine.add_creature(
            AgentConfig(
                name="cutofflegacy",
                system_prompt="",
                include_tools_in_prompt=False,
                agent_path=tmp_path,
            ),
            llm=llm,
            session=path,
            io="headless",
            pwd=tmp_path,
            start=True,
        )
        agent = creature.agent
        agent.controller.config.include_job_status = False
        agent.controller.conversation._messages = [
            message
            for message in agent.controller.conversation.get_messages()
            if message.role != "system"
        ]
        store = agent.session_store
        assert await agent.inject_input("CLEARED_TURN")
        await agent._session_output.drain()
        assert "CLEARED_TURN" in _request_text(requests[-1])

        # The clear itself is a real persisted event; everything at or below it
        # is the cleared prefix the reconstruction must not resurrect.
        _, cutoff = store.append_event(
            "cutofflegacy", "context_cleared", {}, turn_index=1, branch_id=1
        )
        # Watermark == cutoff, and the single stored message has no turn
        # coordinates, so the checkpoint's own prefix can never be re-derived.
        store.save_branch_snapshot(
            "cutofflegacy",
            [{"role": "assistant", "content": "UNANCHORED_CHECKPOINT"}],
            branch={
                "turn_index": 1,
                "branch_id": 1,
                "parent_branch_path": [],
                "context_cutoff_event_id": cutoff,
            },
            watermark=cutoff,
            compact_state={"compact_count": 0, "last_compact_time": 0.0},
        )
        append_user_event_pair(
            store, "cutofflegacy", {"content": "STALE_TARGET"}, 2, 1, [(1, 1)]
        )
        target = _user_event(store.get_events("cutofflegacy"), 2)

        before = len(requests)
        assert await agent.edit_and_rerun(
            -1,
            "EDITED_TARGET",
            target=UserMessageSelector(target["event_id"], 2, 1),
            branch_view={1: 1},
        )
        assert len(requests) == before + 1
        fresh = _request_text(requests[-1])
        assert "EDITED_TARGET" in fresh
        assert "CLEARED_TURN" not in fresh
        assert "UNANCHORED_CHECKPOINT" not in fresh
        # The cutoff reconstruction drops the cleared turn entirely: the fresh
        # prompt carries the edited target and nothing before it.
        assert len(requests[-1]["input"]) == 1
        assert agent._context_cutoff_event_id == cutoff
        assert agent._branch_execution_error is None
        reply = agent.controller.conversation.get_messages()[-1]
        assert reply.role == "assistant"
        assert "reply" in str(reply.content)

        await agent._session_output.drain()
        edited_view = dict(live_branch_path(agent))
        persisted = store.load_branch_snapshot("cutofflegacy", edited_view)
        assert persisted["context_cutoff_event_id"] == cutoff
        saved = deepcopy(store.get_events("cutofflegacy"))
        await engine.remove_creature(creature)
        store.close()

    cold_llm = await _provider(requests)
    restored, cold_store = resume_agent(path, io_mode="plain", llm=cold_llm)
    try:
        await restored.start()
        restored.controller.config.include_job_status = False
        assert restored._context_cutoff_event_id == cutoff
        previous = len(requests)
        completed = await restored.run("cutoff follow-up", timeout=10)
        assert completed.ok
        assert len(requests) == previous + 1
        cold_text = _request_text(requests[-1])
        assert "cutoff follow-up" in cold_text
        assert "EDITED_TARGET" in cold_text
        assert "CLEARED_TURN" not in cold_text
        assert "UNANCHORED_CHECKPOINT" not in cold_text
        assert restored._context_cutoff_event_id == cutoff
        await restored._session_output.drain()
        events = cold_store.get_events("cutofflegacy")
        assert events[: len(saved)] == saved
        assert not any(
            event["type"] in ("tool_call", "tool_result", "compact_complete")
            for event in events[len(saved) :]
        )
        resumed = cold_store.load_branch_snapshot(
            "cutofflegacy", dict(live_branch_path(restored))
        )
        assert resumed["context_cutoff_event_id"] == cutoff
    finally:
        await restored.stop()
        cold_store.close()


async def _exercise_tool_round_repair(tmp_path, requests):
    """Repair a checkpoint's missing canonical tool round through public edits.

    The persisted fixture omits a completed tool round from its checkpoint,
    while retaining that round's real execution and payload in raw history.
    """
    path = tmp_path / "round-repair.kohakutr"
    llm = await _provider(requests, _tool_response)
    tool = _CompactMediaTool()
    async with Terrarium() as engine:
        creature = await engine.add_creature(
            AgentConfig(
                name="roundrepair",
                system_prompt="plain",
                tool_format="native",
                agent_path=tmp_path,
            ),
            llm=llm,
            session=path,
            tools=[tool],
            io="headless",
            pwd=tmp_path,
            start=True,
        )
        agent = creature.agent
        agent.controller.config.include_job_status = False
        agent.controller.config.elide_tool_results = False
        store = agent.session_store
        await creature.run("produce OLD", timeout=10)
        await agent._session_output.drain()
        assert tool.labels == ["OLD"]
        round_one = store.load_branch_snapshot("roundrepair", {1: 1})
        assert round_one is not None
        kept = [
            message
            for message in round_one["messages"]
            if message["role"] != "tool" and not message.get("tool_calls")
        ]
        assert [message["role"] for message in kept] == [
            "system",
            "user",
            "assistant",
        ]
        # Only the stored checkpoint can contribute this marker, so seeing it in
        # the repaired prompt proves the checkpoint prefix was reused instead of
        # a raw replay of the same events.
        kept[0]["content"] += _PREAMBLE_MARKER

        await creature.run("middle", timeout=10)
        await agent._session_output.drain()
        live_round = _round_items(requests[-1])
        assert [item["type"] for item in live_round] == [
            "function_call",
            "function_call_output",
        ]
        assert live_round[0]["call_id"] == live_round[1]["call_id"]
        fixture = {
            "branch": {
                "turn_index": 1,
                "branch_id": 1,
                "parent_branch_path": [],
                "context_cutoff_event_id": 0,
            },
            "watermark": round_one["watermark"],
            "compact_state": round_one["compact_state"],
        }
        events = store.get_events("roundrepair")
        tool_events = [
            event for event in events if event["type"] in ("tool_call", "tool_result")
        ]
        assert len(tool_events) == 2
        target = _user_event(events, 2)

        def store_legacy_checkpoint():
            store.save_branch_snapshot(
                "roundrepair",
                deepcopy(kept),
                branch=deepcopy(fixture["branch"]),
                watermark=fixture["watermark"],
                compact_state=deepcopy(fixture["compact_state"]),
            )

        store_legacy_checkpoint()
        before = len(requests)
        assert await agent.edit_and_rerun(
            -1,
            "EDITED_MIDDLE",
            target=UserMessageSelector(target["event_id"], 2, 1),
            branch_view={1: 1, 2: 1},
        )
        assert len(requests) == before + 1
        repaired = requests[-1]
        calls = [
            item for item in repaired["input"] if item.get("type") == "function_call"
        ]
        results = [
            item
            for item in repaired["input"]
            if item.get("type") == "function_call_output"
        ]
        assert [
            (item["call_id"], item["name"], json.loads(item["arguments"]))
            for item in calls
        ] == [("call_OLD", "compact_media", {"label": "OLD"})]
        assert [item["call_id"] for item in results] == ["call_OLD"]
        assert results[0]["output"][0]["text"] == "caption_OLD"
        # The repaired round must be byte-identical to the round the live turn
        # sent: same canonical payload, same pairing, nothing duplicated.
        assert _round_items(repaired) == live_round
        assert "CHECKPOINT_PREAMBLE_ROUND" in _prompt_text(repaired)
        assert "EDITED_MIDDLE" in _request_text(repaired)
        assert agent._branch_execution_error is None
        assert tool.labels == ["OLD"]
        assert [
            event
            for event in store.get_events("roundrepair")
            if event["type"] in ("tool_call", "tool_result")
        ] == tool_events

        await agent._session_output.drain()
        first_reload = deepcopy(repaired["input"])
        store_legacy_checkpoint()
        before = len(requests)
        assert await agent.edit_and_rerun(
            -1,
            "EDITED_MIDDLE",
            target=UserMessageSelector(target["event_id"], 2, 1),
            branch_view={1: 1, 2: 1},
        )
        assert len(requests) == before + 1
        # A second public reload of the same legacy checkpoint must reproduce
        # the whole repaired prompt, not merely a payload of equal length.
        assert requests[-1]["input"] == first_reload
        assert "CHECKPOINT_PREAMBLE_ROUND" in _prompt_text(requests[-1])
        assert tool.labels == ["OLD"]
        assert agent._branch_execution_error is None

        await agent._session_output.drain()
        selected = dict(live_branch_path(agent))
        switch_messages = deepcopy(agent.controller.conversation.snapshot_messages())
        switch_requests = len(requests)
        assert await agent.inject_input("/branch 2 1")
        assert live_branch_path(agent) == {1: 1, 2: 1}
        assert await agent.inject_input(f"/branch 2 {selected[2]}")
        assert agent.controller.conversation.snapshot_messages() == switch_messages
        assert len(requests) == switch_requests
        assert tool.labels == ["OLD"]
        await agent._session_output.drain()
        repaired_summary = ScriptedLLM(["REPAIRED_ROUND_SUMMARY"])
        await _compact(agent, repaired_summary)
        completion = next(
            e
            for e in reversed(store.get_events("roundrepair"))
            if e["type"] == "compact_complete"
        )
        assert completion["compact_result_event_ids"] == [tool_events[-1]["event_id"]]
        assert _round_items(requests[-1]) == live_round
        await creature.run("after repaired compact", timeout=10)
        assert "REPAIRED_ROUND_SUMMARY" in _request_text(requests[-1])
        assert _round_items(requests[-1]) == []
        await agent._session_output.drain()
        tail = _user_event(store.get_events("roundrepair"), agent._turn_index)
        assert await agent.edit_and_rerun(
            -1,
            "EDITED_COMPACT_TAIL",
            target=UserMessageSelector(
                tail["event_id"], tail["turn_index"], tail["branch_id"]
            ),
            branch_view=dict(live_branch_path(agent)),
        )
        assert "REPAIRED_ROUND_SUMMARY" in _request_text(requests[-1])
        assert _round_items(requests[-1]) == []
        assert tool.labels == ["OLD"]
        assert repaired_summary.call_count == 1
        await agent._session_output.drain()
        await engine.remove_creature(creature)
        store.close()
