"""Integration tests for output routing and isolation."""

from kohakuterrarium.modules.output.router import OutputRouter, OutputState
from kohakuterrarium.parsing import (
    BlockEndEvent,
    BlockStartEvent,
    OutputCallEvent,
    TextEvent,
)
from kohakuterrarium.testing import OutputRecorder


class TestOutputRouterState:
    """Test output router state machine."""

    async def test_normal_text_goes_to_default(self):
        """Text in NORMAL state goes to default output."""
        recorder = OutputRecorder()
        router = OutputRouter(default_output=recorder)

        await router.route(TextEvent(text="hello world"))
        assert "hello world" in recorder.stream_text

    async def test_tool_block_suppresses_text(self):
        """Text inside tool block is suppressed."""
        recorder = OutputRecorder()
        router = OutputRouter(default_output=recorder)

        await router.route(BlockStartEvent(block_type="tool"))
        await router.route(TextEvent(text="tool internals"))
        await router.route(BlockEndEvent(block_type="tool"))

        assert recorder.stream_text == ""  # suppressed
        assert router.state == OutputState.NORMAL  # back to normal

    async def test_subagent_block_suppresses_text(self):
        """Text inside subagent block is suppressed."""
        recorder = OutputRecorder()
        router = OutputRouter(default_output=recorder)

        await router.route(BlockStartEvent(block_type="subagent"))
        await router.route(TextEvent(text="subagent internals"))
        await router.route(BlockEndEvent(block_type="subagent"))

        assert recorder.stream_text == ""

    async def test_text_before_and_after_tool_block(self):
        """Text before and after tool block reaches output."""
        recorder = OutputRecorder()
        router = OutputRouter(default_output=recorder)

        await router.route(TextEvent(text="before "))
        await router.route(BlockStartEvent(block_type="tool"))
        await router.route(TextEvent(text="suppressed"))
        await router.route(BlockEndEvent(block_type="tool"))
        await router.route(TextEvent(text="after"))

        assert recorder.stream_text == "before after"

    async def test_named_output_routes_to_correct_module(self):
        """OutputCallEvent goes to correct named module."""
        default_rec = OutputRecorder()
        discord_rec = OutputRecorder()

        router = OutputRouter(
            default_output=default_rec,
            named_outputs={"discord": discord_rec},
        )

        await router.route(OutputCallEvent(target="discord", content="Hello Discord!"))

        # Discord module got the message
        assert "Hello Discord!" in discord_rec.all_text
        # Default did NOT get it
        assert "Hello Discord!" not in default_rec.all_text

    async def test_unknown_output_falls_back_to_default(self):
        """Unknown target falls back to default output."""
        recorder = OutputRecorder()
        router = OutputRouter(default_output=recorder)

        await router.route(OutputCallEvent(target="nonexistent", content="fallback"))

        assert "fallback" in recorder.all_text

    async def test_completed_outputs_tracked(self):
        """Completed outputs are tracked for feedback."""
        recorder = OutputRecorder()
        target_rec = OutputRecorder()

        router = OutputRouter(
            default_output=recorder,
            named_outputs={"api": target_rec},
        )

        await router.route(OutputCallEvent(target="api", content="result"))

        outputs = router.completed_outputs
        assert len(outputs) == 1
        assert outputs[0].target == "api"
        assert outputs[0].success is True

    async def test_activity_goes_to_activity_not_write(self):
        """on_activity notifications don't produce write() calls."""
        recorder = OutputRecorder()

        recorder.on_activity("tool_start", "[bash] job_123")
        recorder.on_activity("tool_done", "[bash] OK")

        # Activities recorded
        assert len(recorder.activities) == 2
        # But NO text written
        assert not recorder.has_output


class TestOutputRouterLifecycle:
    """Test router processing lifecycle."""

    async def test_processing_start_end(self):
        """Processing start/end notifications reach output."""
        recorder = OutputRecorder()
        router = OutputRouter(default_output=recorder)

        await router.on_processing_start()
        assert recorder.processing_starts == 1

        await router.on_processing_end()
        assert recorder.processing_ends == 1

    async def test_reset_clears_pending(self):
        """Reset clears pending events but not completed outputs."""
        recorder = OutputRecorder()
        target_rec = OutputRecorder()
        router = OutputRouter(
            default_output=recorder,
            named_outputs={"api": target_rec},
        )

        await router.route(OutputCallEvent(target="api", content="tracked"))
        assert len(router.completed_outputs) == 1

        router.reset()
        # Completed outputs survive reset
        assert len(router.completed_outputs) == 1

        router.clear_all()
        # clear_all removes everything
        assert len(router.completed_outputs) == 0
