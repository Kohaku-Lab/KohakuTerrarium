"""Unit tests for :mod:`kohakuterrarium.llm.stream.tool_progress`.

Also drives the OpenAI-compatible and Anthropic stream loops with fake SDK
streams, since they are the two producers of progress reports.
"""

import asyncio
from collections import deque
from types import SimpleNamespace

import pytest

from kohakuterrarium.core.controller import Controller, ControllerConfig
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.llm.anthropic.provider import AnthropicProvider
from kohakuterrarium.llm.openai.provider import OpenAIProvider
from kohakuterrarium.llm.stream.tool_progress import (
    ToolCallProgress,
    observe_tool_call_progress,
)
from kohakuterrarium.modules.output.router import OutputRouter
from kohakuterrarium.session.output import SessionOutput
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.attach._event_stream import StreamOutput
from tests.unit.llm.openai.test_segments import _Chunk, _Client, _Delta, _ToolDelta

pytestmark = pytest.mark.real_llm_provider


def _recorder():
    seen = []

    async def record(payload):
        seen.append(payload)

    return seen, record


class TestThrottle:
    async def test_reports_name_then_every_step_and_a_new_name(self):
        seen, record = _recorder()
        progress = ToolCallProgress(step=100)
        with observe_tool_call_progress(record):
            await progress.update(0, "", 10)
            await progress.update(0, "show_app", 10)
            await progress.update(0, "show_app", 90)
            await progress.update(0, "show_app", 110)
            await progress.update(1, "read", 5)
        assert seen == [
            {"index": 0, "name": "show_app", "chars": 10},
            {"index": 0, "name": "show_app", "chars": 110},
            {"index": 1, "name": "read", "chars": 5},
        ]

    async def test_only_the_observing_task_hears_reports(self):
        seen, record = _recorder()
        with observe_tool_call_progress(record):
            await asyncio.create_task(ToolCallProgress().update(0, "x", 1))
        await ToolCallProgress().update(0, "x", 1)
        assert seen == []

    async def test_a_failing_observer_does_not_break_the_stream(self):
        async def boom(payload):
            raise RuntimeError("observer down")

        with observe_tool_call_progress(boom):
            await ToolCallProgress().update(0, "x", 1)


class TestProviders:
    async def test_openai_stream_reports_argument_growth(self):
        html = "<p>" + "x" * 5000 + "</p>"
        chunks = [
            _Chunk(_Delta(tool_calls=[_ToolDelta(0, id="c1", name="show_app")])),
            *(
                _Chunk(_Delta(tool_calls=[_ToolDelta(0, arguments=html[i : i + 1000])]))
                for i in range(0, len(html), 1000)
            ),
        ]
        provider = OpenAIProvider(api_key="sk-test", model="gpt-x")
        provider._client = _Client(chunks)
        seen, record = _recorder()
        with observe_tool_call_progress(record):
            async for _ in provider._raw_stream_chat(
                [{"role": "user", "content": "q"}]
            ):
                pass
        assert [p["chars"] for p in seen] == [0, 3000]
        assert {p["name"] for p in seen} == {"show_app"}

    async def test_anthropic_stream_reports_tool_use_json_growth(self):
        pieces = ['{"html": "', "y" * 2500, '"}']
        events = [
            SimpleNamespace(type="message_start", message=SimpleNamespace(usage=None)),
            SimpleNamespace(
                type="content_block_start",
                index=0,
                content_block={"type": "tool_use", "id": "t1", "name": "show_app"},
            ),
            *(
                SimpleNamespace(
                    type="content_block_delta",
                    index=0,
                    delta=SimpleNamespace(type="input_json_delta", partial_json=p),
                )
                for p in pieces
            ),
        ]

        async def create(**kwargs):
            async def stream():
                for event in events:
                    yield event

            return stream()

        provider = AnthropicProvider(api_key="k", model="claude-sonnet-4-6")
        provider._client = SimpleNamespace(messages=SimpleNamespace(create=create))
        seen, record = _recorder()
        with observe_tool_call_progress(record):
            async for _ in provider._raw_stream_chat(
                [{"role": "user", "content": "q"}]
            ):
                pass
        assert [p["chars"] for p in seen] == [10, 2510]
        assert provider.last_tool_calls[0].name == "show_app"


class TestControllerPath:
    async def test_progress_reaches_the_websocket_and_is_never_recorded(self, tmp_path):
        html = "z" * 4500
        chunks = [
            _Chunk(_Delta(tool_calls=[_ToolDelta(0, id="c1", name="show_app")])),
            _Chunk(_Delta(tool_calls=[_ToolDelta(0, arguments=html[:2500])])),
            _Chunk(_Delta(tool_calls=[_ToolDelta(0, arguments=html[2500:])])),
        ]
        provider = OpenAIProvider(api_key="sk-test", model="gpt-x")
        provider._client = _Client(chunks)
        queue, log = asyncio.Queue(), deque()
        store = SessionStore(tmp_path / "s.kohakutr")
        try:
            controller = Controller(provider, ControllerConfig(), registry=Registry())
            router = OutputRouter(
                StreamOutput(
                    "w", queue, log, agent=SimpleNamespace(_turn_index=1, _branch_id=1)
                )
            )
            router.add_secondary(SessionOutput("w", store, None))
            controller.output_router = router
            async for _ in controller._run_native_completion(
                [{"role": "user", "content": "q"}], []
            ):
                pass
            frames = []
            while not queue.empty():
                frames.append(queue.get_nowait())
            progress = [f for f in frames if f["type"] == "tool_args_progress"]
            assert [(f["name"], f["chars"]) for f in progress] == [
                ("show_app", 0),
                ("show_app", 2500),
            ]
            assert all(f["type"] != "activity" for f in frames)
            assert not any(
                e["type"] == "tool_args_progress" for e in store.get_events("w")
            )
            assert not any(f.get("type") == "tool_args_progress" for f in log)
        finally:
            store.close()
