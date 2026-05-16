"""Unit tests for :mod:`kohakuterrarium.modules.output.router`.

Behavior-first: OutputRouter fans typed events + parse events to the
right targets, drives the suppression state machine, tracks completed
outputs for controller feedback, and cascades lifecycle to every
attached module.
"""

from kohakuterrarium.modules.output.event import OutputEvent
from kohakuterrarium.modules.output.router import OutputRouter
from kohakuterrarium.modules.output.router_state import (
    CompletedOutput,
    OutputState,
)
from kohakuterrarium.parsing import (
    AssistantImageEvent,
    BlockEndEvent,
    BlockStartEvent,
    CommandEvent,
    OutputCallEvent,
    SubAgentCallEvent,
    TextEvent,
    ToolCallEvent,
)
from kohakuterrarium.testing.output import OutputRecorder


def _router(**kwargs):
    default = OutputRecorder()
    return OutputRouter(default, **kwargs), default


class TestTextRouting:
    async def test_text_event_streams_to_default(self):
        router, default = _router()
        await router.emit(OutputEvent(type="text", content="hello"))
        assert default.stream_text == "hello"

    async def test_text_in_tool_block_is_suppressed(self):
        router, default = _router(suppress_tool_blocks=True)
        await router.route(BlockStartEvent(block_type="tool"))
        await router.route(TextEvent(text="internal tool chatter"))
        assert default.stream_text == ""
        # State machine entered TOOL_BLOCK.
        assert router.state is OutputState.TOOL_BLOCK

    async def test_text_in_tool_block_passes_when_not_suppressed(self):
        router, default = _router(suppress_tool_blocks=False)
        await router.route(BlockStartEvent(block_type="tool"))
        await router.route(TextEvent(text="visible"))
        assert default.stream_text == "visible"

    async def test_block_end_returns_to_normal(self):
        router, default = _router()
        await router.route(BlockStartEvent(block_type="subagent"))
        assert router.state is OutputState.SUBAGENT_BLOCK
        await router.route(BlockEndEvent(block_type="subagent"))
        assert router.state is OutputState.NORMAL

    async def test_subagent_block_passes_text_when_not_suppressed(self):
        router, default = _router(suppress_subagent_blocks=False)
        await router.route(BlockStartEvent(block_type="subagent"))
        await router.route(TextEvent(text="subagent thinking"))
        assert default.stream_text == "subagent thinking"

    async def test_subagent_block_suppresses_by_default(self):
        router, default = _router(suppress_subagent_blocks=True)
        await router.route(BlockStartEvent(block_type="subagent"))
        await router.route(TextEvent(text="hidden"))
        assert default.stream_text == ""

    async def test_command_block_always_suppresses_text(self):
        router, default = _router()
        await router.route(BlockStartEvent(block_type="command"))
        assert router.state is OutputState.COMMAND_BLOCK
        await router.route(TextEvent(text="cmd internals"))
        assert default.stream_text == ""

    async def test_output_block_suppresses_raw_text(self):
        router, default = _router()
        # block_type starting with "output_" enters OUTPUT_BLOCK.
        await router.route(BlockStartEvent(block_type="output_discord"))
        assert router.state is OutputState.OUTPUT_BLOCK
        await router.route(TextEvent(text="raw output block text"))
        assert default.stream_text == ""

    async def test_secondary_outputs_always_receive_text(self):
        # Secondaries (API stream / session log) get every chunk even when
        # the default output is suppressed inside a tool block.
        router, default = _router(suppress_tool_blocks=True)
        secondary = OutputRecorder()
        router.add_secondary(secondary)
        await router.route(BlockStartEvent(block_type="tool"))
        await router.route(TextEvent(text="suppressed for default"))
        assert default.stream_text == ""
        assert secondary.stream_text == "suppressed for default"


class TestPendingQueues:
    async def test_tool_call_queued_and_drained(self):
        router, _ = _router()
        await router.route(ToolCallEvent(name="bash", args={"cmd": "ls"}))
        drained = router.pending_tool_calls
        assert [t.name for t in drained] == ["bash"]
        # The getter clears the queue.
        assert router.pending_tool_calls == []

    async def test_subagent_and_command_queues(self):
        router, _ = _router()
        await router.route(SubAgentCallEvent(name="explore", args={"task": "x"}))
        await router.route(CommandEvent(command="info", args="bash"))
        assert [s.name for s in router.pending_subagent_calls] == ["explore"]
        assert [c.command for c in router.pending_commands] == ["info"]


class TestNamedOutputs:
    async def test_output_event_routed_to_named_module(self):
        discord = OutputRecorder()
        router, default = _router(named_outputs={"discord": discord})
        await router.route(OutputCallEvent(target="discord", content="posted"))
        assert discord.writes == ["posted"]
        # Completed-output tracking records the success for controller feedback.
        completed = router.completed_outputs
        assert len(completed) == 1
        assert completed[0].target == "discord"
        assert completed[0].success is True

    async def test_unknown_target_falls_back_to_default(self):
        router, default = _router()
        await router.route(OutputCallEvent(target="nowhere", content="orphan"))
        # Falls back to default output with a tagged prefix.
        assert default.writes == ["[output_nowhere] orphan"]
        completed = router.get_and_clear_completed_outputs()
        assert completed[0].target == "nowhere(default)"

    async def test_failed_named_output_recorded_as_failure(self):
        class _FailingOutput(OutputRecorder):
            async def write(self, content: str) -> None:
                raise RuntimeError("send failed")

        failing = _FailingOutput()
        router, _ = _router(named_outputs={"bad": failing})
        await router.route(OutputCallEvent(target="bad", content="payload"))
        completed = router.completed_outputs
        assert completed[0].success is False
        assert "send failed" in completed[0].error

    async def test_get_output_feedback_formats_and_clears(self):
        discord = OutputRecorder()
        router, _ = _router(named_outputs={"discord": discord})
        await router.route(OutputCallEvent(target="discord", content="hi"))
        feedback = router.get_output_feedback()
        assert feedback is not None
        assert "## Outputs Sent" in feedback
        assert "[discord]" in feedback
        # Consuming feedback clears the completed list.
        assert router.get_output_feedback() is None

    def test_get_output_targets_lists_named_modules(self):
        router, _ = _router(
            named_outputs={"a": OutputRecorder(), "b": OutputRecorder()}
        )
        assert sorted(router.get_output_targets()) == ["a", "b"]


class _ResumeSpy(OutputRecorder):
    def __init__(self):
        super().__init__()
        self.resumed: list[list] = []
        self.user_inputs: list[str] = []

    async def on_resume(self, events) -> None:
        self.resumed.append(events)

    async def on_user_input(self, text: str) -> None:
        self.user_inputs.append(text)


class TestEmitTypedDispatch:
    """emit() routes each Phase A event type to the right hook set."""

    async def test_processing_events_via_emit_reach_named_outputs(self):
        named = OutputRecorder()
        router, default = _router(named_outputs={"x": named})
        await router.emit(OutputEvent(type="processing_start"))
        await router.emit(OutputEvent(type="processing_end"))
        for out in (default, named):
            assert out.processing_starts == 1
            assert out.processing_ends == 1

    async def test_user_input_event_via_emit_reaches_default_only(self):
        default = _ResumeSpy()
        named = _ResumeSpy()
        router = OutputRouter(default, named_outputs={"x": named})
        await router.emit(OutputEvent(type="user_input", content="hello"))
        # Contract: user_input fans to the default output only.
        assert default.user_inputs == ["hello"]
        assert named.user_inputs == []

    async def test_resume_batch_event_via_emit_replays_to_default(self):
        default = _ResumeSpy()
        router = OutputRouter(default)
        history = [{"type": "text", "content": "old turn"}]
        await router.emit(OutputEvent(type="resume_batch", payload={"events": history}))
        assert default.resumed == [history]

    async def test_output_call_event_with_pending_outputs_queue(self):
        # OutputCallEvent through route() lands in the named output AND
        # the pending_outputs queue is independently drainable.
        router, _ = _router()
        # Manually push to the pending queue to exercise the getter.
        router._pending_outputs.append(
            OutputCallEvent(target="discord", content="queued")
        )
        drained = router.pending_outputs
        assert [o.target for o in drained] == ["discord"]
        assert router.pending_outputs == []


class TestResumeAndUserInputForwarding:
    async def test_on_resume_forwards_to_default_only(self):
        default = _ResumeSpy()
        router = OutputRouter(default)
        secondary = _ResumeSpy()
        router.add_secondary(secondary)
        history = [{"type": "text"}]
        await router.on_resume(history)
        assert default.resumed == [history]
        # Secondary outputs are observers — they do NOT get resume replay.
        assert secondary.resumed == []

    async def test_on_user_input_forwards_to_default(self):
        default = _ResumeSpy()
        router = OutputRouter(default)
        await router.on_user_input("typed this")
        assert default.user_inputs == ["typed this"]


class TestActivityDispatch:
    async def test_notify_activity_reaches_default_and_secondary(self):
        router, default = _router()
        secondary = OutputRecorder()
        router.add_secondary(secondary)
        router.notify_activity("tool_start", "bash", {"job_id": "j1"})
        assert default.activity_types() == ["tool_start"]
        assert default.activities[0].detail == "bash"
        assert secondary.activity_types() == ["tool_start"]

    async def test_unknown_event_type_dispatched_as_activity(self):
        router, default = _router()
        await router.emit(OutputEvent(type="trigger_fired", content="timer"))
        assert default.activity_types() == ["trigger_fired"]
        assert default.activities[0].detail == "timer"

    async def test_metadata_aware_output_gets_structured_activity(self):
        # When the output exposes on_activity_with_metadata and the event
        # carries a payload, the router uses the richer hook.
        class _MetaOutput(OutputRecorder):
            def __init__(self):
                super().__init__()
                self.meta: list[tuple] = []

            def on_activity_with_metadata(self, atype, detail, metadata):
                self.meta.append((atype, detail, metadata))

        default = _MetaOutput()
        router = OutputRouter(default)
        router.notify_activity("tool_done", "bash", {"job_id": "j9"})
        assert default.meta == [("tool_done", "bash", {"job_id": "j9"})]


class TestLifecycle:
    async def test_start_stop_cascade_to_named_outputs(self):
        named = OutputRecorder()
        router, default = _router(named_outputs={"x": named})
        await router.start()
        assert default.is_running and named.is_running
        await router.stop()
        assert not default.is_running and not named.is_running

    async def test_flush_cascades(self):
        named = OutputRecorder()
        router, default = _router(named_outputs={"x": named})
        await router.flush()
        assert default._flushed == 1
        assert named._flushed == 1

    async def test_processing_hooks_reach_all_tiers(self):
        named = OutputRecorder()
        secondary = OutputRecorder()
        router, default = _router(named_outputs={"x": named})
        router.add_secondary(secondary)
        await router.on_processing_start()
        await router.on_processing_end()
        for out in (default, named, secondary):
            assert out.processing_starts == 1
            assert out.processing_ends == 1

    def test_reset_clears_pending_but_keeps_completed(self):
        router, _ = _router()
        router._pending_tool_calls.append(ToolCallEvent(name="t"))
        router._completed_outputs.append(CompletedOutput(target="t", content="c"))
        router.reset()
        assert router._pending_tool_calls == []
        # completed_outputs survives reset (cleared only via feedback consume).
        assert len(router._completed_outputs) == 1

    def test_clear_all_wipes_completed_too(self):
        router, _ = _router()
        router._completed_outputs.append(CompletedOutput(target="t", content="c"))
        router.clear_all()
        assert router._completed_outputs == []

    async def test_remove_secondary_stops_copies(self):
        router, _ = _router()
        secondary = OutputRecorder()
        router.add_secondary(secondary)
        router.remove_secondary(secondary)
        await router.route(TextEvent(text="after removal"))
        assert secondary.stream_text == ""

    def test_link_router_tolerates_slotted_output(self):
        # _maybe_link_router best-effort sets output._router; an output
        # with __slots__ that forbids the attribute must not crash the
        # router constructor.
        class _Slotted(OutputRecorder):
            __slots__ = ()

        # _Slotted still inherits OutputRecorder's __dict__-less? It does
        # not declare new slots so attribute setting still works; instead
        # use a genuinely strict object to hit the except branch.
        class _Strict:
            __slots__ = ("a",)

            async def start(self): ...

            async def stop(self): ...

            async def flush(self): ...

            async def write(self, c): ...

        # Must construct without raising even though _router can't be set.
        router = OutputRouter(_Strict())
        assert router.default_output is not None


class _ImageSpy(OutputRecorder):
    def __init__(self):
        super().__init__()
        self.images: list[dict] = []

    def on_assistant_image(self, url, **kwargs):
        self.images.append({"url": url, **kwargs})


class TestAssistantImageRouting:
    async def test_parse_event_image_fans_to_default_and_secondary(self):
        # The parse-event path (_handle_assistant_image) works correctly —
        # both the default and secondary outputs get the image.
        default = _ImageSpy()
        router = OutputRouter(default)
        secondary = _ImageSpy()
        router.add_secondary(secondary)
        await router.route(AssistantImageEvent(url="http://x/y.png", detail="high"))
        assert default.images == [
            {
                "url": "http://x/y.png",
                "detail": "high",
                "source_type": None,
                "source_name": None,
                "revised_prompt": None,
            }
        ]
        assert secondary.images[0]["url"] == "http://x/y.png"

    async def test_image_handler_exception_does_not_break_fan_out(self):
        # A raising on_assistant_image on one output must not stop the
        # image reaching the others — the handler error is swallowed.
        class _BadImageOutput(OutputRecorder):
            def on_assistant_image(self, url, **kwargs):
                raise RuntimeError("bad image handler")

        default = _BadImageOutput()
        router = OutputRouter(default)
        good = _ImageSpy()
        router.add_secondary(good)
        await router.route(AssistantImageEvent(url="http://x/z.png"))
        # The healthy secondary still received the image.
        assert good.images[0]["url"] == "http://x/z.png"

    async def test_typed_assistant_image_event_does_not_crash(self):
        # Regression guard for B-modules-1 (fixed): AssistantImageEvent
        # is now imported in router.py, so emitting a typed
        # assistant_image event fans it to renderers instead of raising.
        # Contract (event.py docstring): 'assistant_image' is a valid
        # OutputEvent.type whose payload carries the image fields. Emitting
        # it through the bus must fan the image to outputs, NOT raise.
        router, default = _router()
        await router.emit(
            OutputEvent(
                type="assistant_image",
                payload={"url": "http://x/y.png", "detail": "auto"},
            )
        )
