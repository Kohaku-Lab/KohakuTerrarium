"""Model recovery status delivery through the real controller output path."""

import asyncio
from collections import deque
from types import SimpleNamespace

from kohakuterrarium.core.controller import Controller, ControllerConfig
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.llm.model_recovery_status import (
    observe_model_recovery,
    notify_model_recovery,
)
from kohakuterrarium.modules.output.event import OutputEvent
from kohakuterrarium.modules.output.router import OutputRouter
from kohakuterrarium.studio.attach._event_stream import StreamOutput
from kohakuterrarium.studio.attach.io import _session_info_frame
from tests.unit.llm.test_openai_ws import Ev, MESSAGES, completed
from tests.unit.llm.test_responses_ws_recovery import provider  # noqa: F401


async def test_controller_recovery_is_transient_and_preserves_text(provider):
    queue = asyncio.Queue()
    log = deque()
    controller = Controller(provider, ControllerConfig(), registry=Registry())
    output = StreamOutput(
        "worker", queue, log, agent=SimpleNamespace(_turn_index=4, _branch_id=2)
    )
    controller.output_router = OutputRouter(output)
    provider._client.responses.connection.scripts = [
        [Ev(type="response.created"), ConnectionError("lost")],
        [
            Ev(type="response.created"),
            Ev(type="response.output_text.delta", delta="answer"),
            completed(),
        ],
    ]
    events = [event async for event in controller._run_text_completion(MESSAGES)]
    frames = []
    while not queue.empty():
        frames.append(queue.get_nowait())
    statuses = [frame for frame in frames if frame["type"] == "model_recovery"]
    assert [frame["phase"] for frame in statuses] == ["reconnecting", None]
    assert {frame["source"] for frame in statuses} == {"worker"}
    assert {frame["branch_id"] for frame in statuses} == {2}
    assert len({frame["request_id"] for frame in statuses}) == 1
    assert statuses[1]["sequence"] > statuses[0]["sequence"]
    assert all(frame["type"] != "activity" for frame in frames)
    assert controller._last_assistant_content == "answer"
    assert events
    assert not log


async def test_attach_snapshot_and_end_clear_recovery_without_history():
    queue = asyncio.Queue()
    log = deque()
    agent = SimpleNamespace(
        _turn_index=4, _branch_id=2, config=SimpleNamespace(model="m")
    )
    output = StreamOutput("worker", queue, log, agent=agent)
    agent.output_router = OutputRouter(output)
    creature = SimpleNamespace(agent=agent, name="worker")
    payload = {
        "request_id": "req",
        "request_started_at": 12,
        "sequence": 1,
        "phase": "waiting",
    }
    await agent.output_router.emit(
        OutputEvent(type="model_recovery", surface="status", payload=payload)
    )
    snapshot = _session_info_frame(creature)["model_recovery"]
    assert snapshot == {**payload, "turn_index": 4, "branch_id": 2}
    assert not log
    await agent.output_router.on_processing_end()
    assert _session_info_frame(creature)["model_recovery"]["phase"] is None


async def test_child_tasks_do_not_inherit_controller_recovery_observer():
    frames = []

    async def notify(payload):
        frames.append(payload)

    with observe_model_recovery(notify):
        await asyncio.create_task(notify_model_recovery({"request_id": "child"}))
        await notify_model_recovery({"request_id": "main"})
    await notify_model_recovery({"request_id": "outside"})
    assert frames == [{"request_id": "main"}]


async def test_controller_interrupt_closes_stream_in_the_owning_task(provider):
    controller = Controller(provider, ControllerConfig(), registry=Registry())
    controller._interrupted = True
    connection = provider._client.responses.connection
    connection.scripts = [[Ev(type="response.output_text.delta", delta="partial")]]
    _ = [event async for event in controller._run_text_completion(MESSAGES)]
    assert connection.closed
    assert not provider._ws_session.busy


async def test_observer_accepts_provider_iterators_without_aclose():
    class Iterator:
        def __init__(self):
            self.remaining = iter(["text"])

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self.remaining)
            except StopIteration:
                raise StopAsyncIteration from None

    provider = SimpleNamespace(chat=lambda *args, **kwargs: Iterator())
    controller = Controller(provider, ControllerConfig(), registry=Registry())
    assert [chunk async for chunk in controller._chat_with_recovery(MESSAGES)] == [
        "text"
    ]
