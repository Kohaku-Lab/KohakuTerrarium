"""Stable media compaction and branch reconstruction workflow support."""

import asyncio
import base64
import json
from copy import deepcopy

import httpx

import kohakuterrarium.core.agent_message_history as branch_history
from kohakuterrarium.core.branch_continuation import live_branch_path
from kohakuterrarium.core.config_types import AgentConfig
from kohakuterrarium.llm.codex_provider import CodexOAuthProvider
from kohakuterrarium.llm.message import ImagePart, TextPart
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from kohakuterrarium.session.raw_history import UserMessageSelector
from kohakuterrarium.session.resume import resume_agent
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.testing.llm import ScriptedLLM
from kohakuterrarium.testing.llm_seam import real_llm_providers


def _parts(label):
    encoded = base64.b64encode(f"IMAGE_{label}".encode()).decode()
    return [
        TextPart(text=f"input_{label}"),
        ImagePart(url=f"data:image/png;base64,{encoded}"),
        TextPart(text=f"after_{label}"),
    ]


def _images(request):
    return [
        base64.b64decode(part["image_url"].split(",", 1)[1])
        for item in request["input"]
        for part in item.get("content", [])
        if part.get("type") == "input_image"
    ]


def _request_text(request):
    return " ".join(
        part.get("text", "")
        for item in request["input"]
        for part in item.get("content", [])
    )


async def _provider(requests, output=None):
    def respond(request):
        requests.append(json.loads(request.content))
        events = [
            {"type": "response.output_text.delta", "delta": "reply"},
            {
                "type": "response.completed",
                "response": {"id": f"r{len(requests)}", "output": []},
            },
        ]
        if output is not None:
            events = output(requests[-1]) + [events[-1]]
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content="".join(
                f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
                for event in events
            ),
        )

    with real_llm_providers():
        llm = CodexOAuthProvider(
            model="gpt-5.4",
            api_key="test-key",
            base_url="https://compact.test/v1",
            extra_body={"responses_reasoning_replay": True},
        )
    await llm.ensure_authenticated()
    initial = llm._client
    llm._client = initial.with_options(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))
    )
    await initial.close()
    return llm


async def exercise_compact_media_history(tmp_path, monkeypatch):
    """Preserve selected user media through compact, sibling edits, and cold resume."""
    requests = []
    path = tmp_path / "stable-media.kohakutr"
    summary = ScriptedLLM(["SUMMARY_ANCESTOR_A"])
    llm = await _provider(requests)
    async with Terrarium() as engine:
        creature = await engine.add_creature(
            AgentConfig(name="stablemedia", system_prompt="plain", agent_path=tmp_path),
            llm=llm,
            session=path,
            io="headless",
            pwd=tmp_path,
            start=True,
        )
        agent = creature.agent
        agent.controller.config.include_job_status = False
        store = agent.session_store
        store.write_artifact("ancestor.png", b"ANCESTOR_A")
        ancestor = store.artifacts_dir / "ancestor.png"
        await agent.inject_input(
            [
                TextPart(text="ancestor_A"),
                ImagePart(url="/api/sessions/stable-media/artifacts/ancestor.png"),
            ]
        )
        await agent.inject_input("middle")
        await agent.inject_input(_parts("TAIL_A"))
        await agent._session_output.drain()
        original = dict(live_branch_path(agent))
        raw = store.get_events("stablemedia")
        users = {e["turn_index"]: e for e in raw if e["type"] == "user_message"}
        manager = agent.compact_manager
        manager.config.keep_recent_turns = 1
        manager._llm = summary
        assert manager.trigger_compact()
        await asyncio.wait_for(manager.wait_for_current(), 10)
        await agent._session_output.drain()
        assert manager._compact_count == 1
        assert summary.call_count == 1
        assert "ancestor_A" in str(summary.last_messages)
        assert "TAIL_A" not in str(summary.last_messages)
        record = next(
            e
            for e in reversed(store.get_events("stablemedia"))
            if e["type"] == "compact_complete"
        )
        assert record["compact_source_path"] == [[1, 1], [2, 1]]
        checkpoint = store.load_branch_snapshot("stablemedia", original)
        assert checkpoint["context_cutoff_event_id"] == 0
        replayed = []
        real_replay = branch_history.replay_with_settled_tools

        def observe(events, **kwargs):
            replayed.append([event["event_id"] for event in events])
            return real_replay(events, **kwargs)

        with monkeypatch.context() as observation:
            observation.setattr(branch_history, "replay_with_settled_tools", observe)
            agent._reload_conversation_under_branch_view(original)
        assert replayed
        assert all(
            event_id > checkpoint["watermark"]
            for suffix in replayed
            for event_id in suffix
        ), (checkpoint["watermark"], replayed)
        assert agent.controller.conversation.to_messages() == [
            {key: value for key, value in message.items() if key != "metadata"}
            for message in checkpoint["messages"]
        ]
        async for _ in agent.controller._chat_with_recovery(
            agent.controller.conversation.to_messages()
        ):
            pass
        snapshot_input = deepcopy(requests[-1]["input"])
        with monkeypatch.context() as forced_miss:
            forced_miss.setattr(
                branch_history, "prepare_snapshot", lambda *a, **kw: None
            )
            agent._reload_conversation_under_branch_view(original)
        async for _ in agent.controller._chat_with_recovery(
            agent.controller.conversation.to_messages()
        ):
            pass
        assert requests[-1]["input"] == snapshot_input, (
            snapshot_input,
            requests[-1]["input"],
        )
        assert _images(requests[-1]) == [b"IMAGE_TAIL_A"]
        assert summary.call_count == 1
        ancestor.unlink()
        tail = users[3]
        assert await agent.edit_and_rerun(
            -1,
            [part.to_dict() for part in _parts("TAIL_B")],
            target=UserMessageSelector(tail["event_id"], 3, 1),
            branch_view=original,
        )
        for regenerate in (False, True):
            if regenerate:
                previous_requests = len(requests)
                await agent.regenerate_last_response()
                assert len(requests) == previous_requests + 1
            assert _images(requests[-1]) == [b"IMAGE_TAIL_B"]
            content = next(
                item["content"]
                for item in requests[-1]["input"]
                if item.get("role") == "user"
            )
            assert [part["type"] for part in content] == [
                "input_text",
                "input_image",
                "input_text",
            ]
            assert [
                part["text"] for part in content if part["type"] == "input_text"
            ] == ["input_TAIL_B", "after_TAIL_B"]
            assert "SUMMARY_ANCESTOR_A" in _request_text(requests[-1])
            assert summary.call_count == 1
        source = users[1]
        assert await agent.edit_and_rerun(
            -1,
            [part.to_dict() for part in _parts("SOURCE_B")],
            target=UserMessageSelector(source["event_id"], 1, 1),
            branch_view=original,
        )
        assert _images(requests[-1]) == [b"IMAGE_SOURCE_B"]
        assert "SUMMARY_ANCESTOR_A" not in _request_text(requests[-1])
        assert agent.compact_manager._compact_count == 0
        await agent.inject_input("return original", branch_view=original)
        assert _images(requests[-1]) == [b"IMAGE_TAIL_A"]
        assert "SUMMARY_ANCESTOR_A" in _request_text(requests[-1])
        await agent._session_output.drain()
        saved = deepcopy(store.get_events("stablemedia"))
        assert saved[: len(raw)] == raw
        await engine.remove_creature(creature)
        store.close()
    cold_llm = await _provider(requests)
    restored, cold_store = resume_agent(path, io_mode="plain", llm=cold_llm)
    try:
        await restored.start()
        restored.controller.config.include_job_status = False
        previous_requests = len(requests)
        completed = await restored.run("cold original", timeout=10)
        assert completed.ok
        assert len(requests) == previous_requests + 1
        assert "cold original" in _request_text(requests[-1])
        assert _images(requests[-1]) == [b"IMAGE_TAIL_A"]
        assert "SUMMARY_ANCESTOR_A" in _request_text(requests[-1])
        assert "SOURCE_B" not in _request_text(requests[-1])
        assert summary.call_count == 1
        await restored._session_output.drain()
        events = cold_store.get_events("stablemedia")
        assert events[: len(saved)] == saved
        assert not any(
            e["type"] in ("tool_call", "tool_result", "compact_complete")
            for e in events[len(saved) :]
        )
        request_count = len(requests)
        failed = await restored.run(
            [
                ImagePart(
                    url="/api/sessions/stable-media/artifacts/required-missing.png"
                )
            ],
            raise_on_error=False,
            timeout=10,
        )
        assert failed.status == "error"
        assert "Required local media is unavailable" in failed.error
        assert len(requests) == request_count
    finally:
        await restored.stop()
        cold_store.close()
    await _exercise_compact_tool_media(tmp_path)


class _CompactMediaTool(BaseTool):
    def __init__(self):
        super().__init__()
        self.labels = []

    @property
    def tool_name(self):
        return "compact_media"

    @property
    def description(self):
        return "Produce a labeled image."

    @property
    def execution_mode(self):
        return ExecutionMode.DIRECT

    def get_parameters_schema(self):
        return {
            "type": "object",
            "properties": {"label": {"type": "string"}},
            "required": ["label"],
        }

    async def _execute(self, args, **kwargs):
        label = args["label"]
        self.labels.append(label)
        return ToolResult(
            output=[
                TextPart(text=f"caption_{label}"),
                ImagePart(
                    url="data:image/png;base64,"
                    + base64.b64encode(f"TOOL_{label}".encode()).decode()
                ),
            ]
        )


def _tool_response(body):
    last = body["input"][-1]
    text = " ".join(part.get("text", "") for part in last.get("content", []))
    if last.get("role") == "user" and text.startswith("produce "):
        label = text.split()[-1]
        return [
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "function_call",
                    "id": f"item_{label}",
                    "call_id": f"call_{label}",
                    "name": "compact_media",
                    "arguments": json.dumps({"label": label}),
                },
            }
        ]
    events = []
    if last.get("type") == "function_call_output":
        label = last["call_id"].removeprefix("call_")
        events.append(
            {
                "type": "response.output_item.done",
                "item": {
                    "id": f"reason_{label}",
                    "type": "reasoning",
                    "summary": [],
                    "content": [{"type": "reasoning_text", "text": f"reason_{label}"}],
                },
            }
        )
    return events + [{"type": "response.output_text.delta", "delta": "tool reply"}]


def _assert_live_tool(request):
    assert "[Previous context summary (compact round 1)]" in _request_text(request)
    assert "CONSUMED_OLD_RESULT" in _request_text(request)
    calls = [item for item in request["input"] if item.get("type") == "function_call"]
    assert [
        (item["call_id"], item["name"], json.loads(item["arguments"])) for item in calls
    ] == [("call_LIVE", "compact_media", {"label": "LIVE"})]
    results = [
        item for item in request["input"] if item.get("type") == "function_call_output"
    ]
    assert [item["call_id"] for item in results] == ["call_LIVE"]
    assert [part["type"] for part in results[0]["output"]] == [
        "input_text",
        "input_image",
    ]
    assert results[0]["output"][0]["text"] == "caption_LIVE"
    assert (
        base64.b64decode(results[0]["output"][1]["image_url"].split(",", 1)[1])
        == b"TOOL_LIVE"
    )
    reasoning = [item for item in request["input"] if item.get("type") == "reasoning"]
    assert [item["content"] for item in reasoning] == [
        [{"type": "reasoning_text", "text": "reason_LIVE"}]
    ]
    assert "reason_OLD" not in str(request["input"])


async def _exercise_compact_tool_media(tmp_path):
    requests = []
    llm = await _provider(requests, _tool_response)
    tool = _CompactMediaTool()
    summary = ScriptedLLM(["CONSUMED_OLD_RESULT"])
    path = tmp_path / "compact-tools.kohakutr"
    async with Terrarium() as engine:
        creature = await engine.add_creature(
            AgentConfig(
                name="compacttools",
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
        await creature.run("produce OLD", timeout=10)
        await creature.run("middle", timeout=10)
        await creature.run("produce LIVE", timeout=10)
        await agent._session_output.drain()
        assert tool.labels == ["OLD", "LIVE"]
        store = agent.session_store
        before = store.get_events("compacttools")
        results = [event for event in before if event["type"] == "tool_result"]
        assert len(results) == 2
        manager = agent.compact_manager
        manager.config.keep_recent_turns = 1
        manager._llm = summary
        assert manager.trigger_compact()
        await asyncio.wait_for(manager.wait_for_current(), 10)
        await agent._session_output.drain()
        complete = next(
            event
            for event in reversed(store.get_events("compacttools"))
            if event["type"] == "compact_complete"
        )
        assert results[0]["event_id"] in complete["compact_result_event_ids"]
        assert results[1]["event_id"] not in complete["compact_result_event_ids"]
        assert complete["compact_source_path"] == [[1, 1], [2, 1]]
        assert "caption_OLD" in str(summary.last_messages)
        assert "caption_LIVE" not in str(summary.last_messages)
        old_image = results[0]["output"][1]["image_url"]["url"]
        (store.artifacts_dir / old_image.split("/artifacts/", 1)[1]).unlink()
        await creature.run("continue compact", timeout=10)
        _assert_live_tool(requests[-1])
        assert tool.labels == ["OLD", "LIVE"]
        assert summary.call_count == 1
        await agent._session_output.drain()
        saved = deepcopy(store.get_events("compacttools"))
        await engine.remove_creature(creature)
        store.close()
    cold_llm = await _provider(requests, _tool_response)
    cold, store = resume_agent(path, io_mode="plain", llm=cold_llm)
    replay_tool = _CompactMediaTool()
    cold.add_tool(replay_tool)
    try:
        await cold.start()
        cold.controller.config.include_job_status = False
        cold.controller.config.elide_tool_results = False
        previous_requests = len(requests)
        completed = await cold.run("cold continuation", timeout=10)
        assert completed.ok
        assert len(requests) == previous_requests + 1
        assert "cold continuation" in _request_text(requests[-1])
        _assert_live_tool(requests[-1])
        assert replay_tool.labels == []
        assert tool.labels == ["OLD", "LIVE"]
        assert summary.call_count == 1
        await cold._session_output.drain()
        assert store.get_events("compacttools")[: len(saved)] == saved
    finally:
        await cold.stop()
        store.close()
