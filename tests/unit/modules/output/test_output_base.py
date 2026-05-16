"""Unit tests for :mod:`kohakuterrarium.modules.output.base`.

Behavior-first: BaseOutputModule lifecycle, the default ``emit()``
switch that forwards typed events to legacy hooks, and the optional
``on_activity_with_metadata`` upgrade path.
"""

from kohakuterrarium.modules.output.base import BaseOutputModule, OutputModule
from kohakuterrarium.modules.output.event import OutputEvent


class _SpyOutput(BaseOutputModule):
    """Concrete output that records every hook invocation."""

    def __init__(self):
        super().__init__()
        self.written: list[str] = []
        self.streamed: list[str] = []
        self.flushed = 0
        self.proc_starts = 0
        self.proc_ends = 0
        self.activities: list[tuple[str, str]] = []
        self.images: list[dict] = []
        self.user_inputs: list[str] = []
        self.resumes: list[list] = []

    async def write(self, content: str) -> None:
        self.written.append(content)

    async def write_stream(self, chunk: str) -> None:
        self.streamed.append(chunk)

    async def flush(self) -> None:
        self.flushed += 1

    async def on_processing_start(self) -> None:
        self.proc_starts += 1

    async def on_processing_end(self) -> None:
        self.proc_ends += 1

    def on_activity(self, activity_type: str, detail: str) -> None:
        self.activities.append((activity_type, detail))

    def on_assistant_image(self, url, **kwargs) -> None:
        self.images.append({"url": url, **kwargs})

    async def on_user_input(self, text: str) -> None:
        self.user_inputs.append(text)

    async def on_resume(self, events) -> None:
        self.resumes.append(events)


class _MetadataAwareOutput(_SpyOutput):
    def __init__(self):
        super().__init__()
        self.metadata_activities: list[tuple[str, str, dict]] = []

    def on_activity_with_metadata(self, activity_type, detail, metadata) -> None:
        self.metadata_activities.append((activity_type, detail, metadata))


class TestLifecycle:
    async def test_start_sets_running(self):
        out = _SpyOutput()
        assert out.is_running is False
        await out.start()
        assert out.is_running is True

    async def test_stop_flushes_then_clears_running(self):
        # Contract: stop() must flush buffered content BEFORE going down.
        out = _SpyOutput()
        await out.start()
        await out.stop()
        assert out.flushed == 1
        assert out.is_running is False

    async def test_default_write_stream_falls_back_to_write(self):
        # A subclass that only implements write() still gets streaming
        # via the base default.
        class _WriteOnly(BaseOutputModule):
            def __init__(self):
                super().__init__()
                self.calls: list[str] = []

            async def write(self, content: str) -> None:
                self.calls.append(content)

        out = _WriteOnly()
        await out.write_stream("chunk")
        assert out.calls == ["chunk"]


class TestEmitForwarding:
    async def test_text_event_forwards_to_write_stream(self):
        out = _SpyOutput()
        await out.emit(OutputEvent(type="text", content="hello"))
        assert out.streamed == ["hello"]

    async def test_text_event_with_non_string_content_is_dropped(self):
        # Defensive: emit() only forwards str content for text events.
        out = _SpyOutput()
        await out.emit(OutputEvent(type="text", content=["not", "a", "string"]))
        assert out.streamed == []

    async def test_processing_lifecycle_events_forward(self):
        out = _SpyOutput()
        await out.emit(OutputEvent(type="processing_start"))
        await out.emit(OutputEvent(type="processing_end"))
        assert out.proc_starts == 1
        assert out.proc_ends == 1

    async def test_user_input_event_forwards(self):
        out = _SpyOutput()
        await out.emit(OutputEvent(type="user_input", content="hi agent"))
        assert out.user_inputs == ["hi agent"]

    async def test_assistant_image_event_unpacks_payload(self):
        out = _SpyOutput()
        await out.emit(
            OutputEvent(
                type="assistant_image",
                payload={
                    "url": "http://x/y.png",
                    "detail": "high",
                    "source_name": "dalle",
                    "revised_prompt": "a cat",
                },
            )
        )
        assert out.images == [
            {
                "url": "http://x/y.png",
                "detail": "high",
                "source_type": None,
                "source_name": "dalle",
                "revised_prompt": "a cat",
            }
        ]

    async def test_resume_batch_event_forwards_events_list(self):
        out = _SpyOutput()
        history = [{"type": "text", "content": "old"}]
        await out.emit(OutputEvent(type="resume_batch", payload={"events": history}))
        assert out.resumes == [history]

    async def test_activity_event_falls_through_to_on_activity(self):
        out = _SpyOutput()
        await out.emit(OutputEvent(type="tool_start", content="bash"))
        assert out.activities == [("tool_start", "bash")]

    async def test_activity_event_with_payload_uses_metadata_hook(self):
        # When the output exposes on_activity_with_metadata AND the event
        # carries a payload, the richer hook is used instead.
        out = _MetadataAwareOutput()
        await out.emit(
            OutputEvent(type="tool_done", content="bash", payload={"job_id": "j1"})
        )
        assert out.metadata_activities == [("tool_done", "bash", {"job_id": "j1"})]
        # The plain on_activity hook must NOT also fire.
        assert out.activities == []

    async def test_activity_event_without_payload_uses_plain_hook(self):
        # Even on a metadata-aware output, an empty payload routes to the
        # plain hook (the metadata branch is gated on a truthy payload).
        out = _MetadataAwareOutput()
        await out.emit(OutputEvent(type="tool_error", content="boom"))
        assert out.metadata_activities == []
        assert out.activities == [("tool_error", "boom")]


class TestDefaultNoOpHooks:
    """The base class supplies safe no-op defaults for every optional hook."""

    async def test_minimal_subclass_only_needs_write(self):
        # A subclass implementing only the abstract write() must still be a
        # usable OutputModule — every other hook defaults to a no-op and
        # must not raise.
        class _Minimal(BaseOutputModule):
            def __init__(self):
                super().__init__()
                self.got: list[str] = []

            async def write(self, content: str) -> None:
                self.got.append(content)

        out = _Minimal()
        # None of these are overridden — exercise the base defaults.
        await out.flush()
        await out.on_processing_start()
        await out.on_processing_end()
        out.on_activity("tool_start", "bash")
        out.on_assistant_image("http://x/y.png")
        await out.on_user_input("hi")
        await out.on_resume([{"type": "text"}])
        # write() still works after all the no-ops.
        await out.write("real")
        assert out.got == ["real"]


class TestProtocol:
    def test_concrete_output_satisfies_protocol(self):
        assert isinstance(_SpyOutput(), OutputModule)
