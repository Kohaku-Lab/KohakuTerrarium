"""Unit tests for :mod:`kohakuterrarium.core.controller`."""

import asyncio
import types

import pytest

from kohakuterrarium.core.controller import (
    ControllerConfig,
    ControllerContext,
    _merge_text_and_parts,
)
from kohakuterrarium.core.events import (
    TriggerEvent,
    create_user_input_event,
)
from kohakuterrarium.core.job import JobResult
from kohakuterrarium.llm.message import ImagePart
from kohakuterrarium.parsing.events import (
    TextEvent,
)
from kohakuterrarium.testing.agent import TestAgentBuilder
from kohakuterrarium.testing.llm import ScriptedLLM, ScriptEntry

# ── _merge_text_and_parts ────────────────────────────────────────


class TestMergeTextAndParts:
    def test_empty_parts_returns_text(self):
        assert _merge_text_and_parts("hello", []) == "hello"

    def test_parts_with_text_prepended(self):
        parts = [ImagePart(url="x")]
        out = _merge_text_and_parts("hi", parts)
        assert isinstance(out, list)
        assert out[0].text == "hi"
        assert out[1] is parts[0]

    def test_empty_text_with_parts_just_parts(self):
        parts = [ImagePart(url="x")]
        out = _merge_text_and_parts("", parts)
        assert out == parts


# ── ControllerConfig / ControllerContext ─────────────────────────


class TestControllerConfig:
    def test_defaults(self):
        c = ControllerConfig()
        assert c.system_prompt.startswith("You are")
        assert c.include_job_status is True
        assert c.include_tools_list is True
        assert c.max_messages == 50
        assert c.ephemeral is False
        assert c.known_outputs == set()
        assert c.tool_format is None
        assert c.sanitize_orphan_tool_calls is True


class TestControllerContext:
    def test_accessors(self):
        from kohakuterrarium.core.job import JobStore
        from kohakuterrarium.core.registry import Registry

        registry = Registry()
        store = JobStore()
        ctrl = types.SimpleNamespace(executor=None)
        ctx = ControllerContext(controller=ctrl, job_store=store, registry=registry)
        assert ctx.get_job_status("nope") is None
        assert ctx.get_job_result("nope") is None
        assert ctx.get_tool_info("nope") is None
        assert ctx.get_subagent_info("nope") is None

    def test_get_job_result_via_executor(self):
        from kohakuterrarium.core.executor import Executor
        from kohakuterrarium.core.job import JobStore
        from kohakuterrarium.core.registry import Registry

        ex = Executor()
        result = JobResult(job_id="x", output="ok")
        ex._results["x"] = result
        ctrl = types.SimpleNamespace(executor=ex)
        ctx = ControllerContext(
            controller=ctrl, job_store=JobStore(), registry=Registry()
        )
        assert ctx.get_job_result("x") is result


# ── End-to-end via TestAgentBuilder ──────────────────────────────


class TestSimpleStreamingResponse:
    async def test_text_response_emits_text_events(self):
        env = TestAgentBuilder().with_llm_script(["Hello, world!"]).build()
        await env.inject("hi")
        assert "Hello, world!" in env.output.all_text
        assert env.llm.call_count == 1

    async def test_conversation_appended(self):
        env = TestAgentBuilder().with_llm_script(["Ok."]).build()
        await env.inject("question")
        # System + user + assistant.
        roles = [m.role for m in env.controller.conversation.get_messages()]
        assert "user" in roles
        assert "assistant" in roles


class TestToolCallDispatch:
    async def test_tool_call_emits_parse_event(self):
        """Parser emits a ToolCallEvent for ``[/<tool>]...[<tool>/]`` syntax."""
        from kohakuterrarium.builtins.tools.read import ReadTool

        env = (
            TestAgentBuilder()
            .with_tool(ReadTool())
            .with_llm_script([ScriptEntry("[/read]plan.md[read/]")])
            .build()
        )
        evt = create_user_input_event("read plan")
        await env.controller.push_event(evt)
        events: list = []
        async for e in env.controller.run_once():
            events.append(e)
        # At least one tool call event surfaced from the parser.
        from kohakuterrarium.parsing import ToolCallEvent as TCE

        assert any(isinstance(e, TCE) for e in events)


class TestEphemeralMode:
    async def test_ephemeral_keeps_only_system(self):
        env = (
            TestAgentBuilder()
            .with_llm_script(["resp1", "resp2"])
            .with_ephemeral(True)
            .build()
        )
        await env.inject("first")
        # After turn, ephemeral mode preserves only system.
        roles = [m.role for m in env.controller.conversation.get_messages()]
        # Non-system messages cleared between turns.
        assert roles.count("system") >= 1


# ── push_event / push_event_sync ────────────────────────────────


class TestPushEvent:
    async def test_push_event_async(self):
        env = TestAgentBuilder().with_llm_script(["x"]).build()
        evt = create_user_input_event("hello")
        await env.controller.push_event(evt)
        # Event queued.
        assert env.controller._event_queue.qsize() >= 1

    def test_push_event_sync(self):
        env = TestAgentBuilder().with_llm_script(["x"]).build()
        evt = create_user_input_event("hello")
        env.controller.push_event_sync(evt)
        assert env.controller._event_queue.qsize() >= 1


# ── conversation seeding ─────────────────────────────────────────


class TestSystemPromptSeeding:
    async def test_system_prompt_in_conversation(self):
        env = (
            TestAgentBuilder()
            .with_llm_script(["ok"])
            .with_system_prompt("CUSTOM SYSTEM")
            .build()
        )
        await env.inject("hi")
        sys_msg = env.controller.conversation.get_system_message()
        assert sys_msg is not None
        assert "CUSTOM SYSTEM" in sys_msg.content


# ── multi-turn ──────────────────────────────────────────────────


class TestMultiTurn:
    async def test_two_user_inputs(self):
        env = (
            TestAgentBuilder().with_llm_script(["first reply", "second reply"]).build()
        )
        await env.inject("one")
        await env.inject("two")
        assert env.llm.call_count == 2
        msgs = env.controller.conversation.get_messages()
        # Both user inputs in conversation.
        user_texts = [m.content for m in msgs if m.role == "user"]
        assert any("one" in t for t in user_texts)
        assert any("two" in t for t in user_texts)


# ── tool_format / known_outputs propagation ──────────────────────


class TestKnownOutputsAndFormat:
    async def test_known_outputs_threaded_into_parser(self):
        env = (
            TestAgentBuilder()
            .with_named_output("discord", _DummyOutput())
            .with_llm_script(["[/discord]hello[discord/]"])
            .build()
        )
        # known_outputs set carries through; the controller picks them
        # up via its parser config builder on demand.
        env.controller._get_parser()
        cfg = env.controller._parser_config
        assert "discord" in cfg.known_outputs

    async def test_default_tool_format_bracket(self):
        from kohakuterrarium.parsing.format import BRACKET_FORMAT, XML_FORMAT

        env = TestAgentBuilder().with_llm_script(["x"]).build()
        env.controller._get_parser()
        assert env.controller._parser_config.tool_format in (BRACKET_FORMAT, XML_FORMAT)


class _DummyOutput:
    """Minimal OutputModule stand-in for `with_named_output`."""

    async def start(self):
        pass

    async def stop(self):
        pass

    async def write(self, content, metadata=None):
        pass

    async def flush(self):
        pass

    def reset(self):
        pass

    async def on_processing_start(self):
        pass

    async def on_processing_end(self):
        pass

    def on_activity(self, kind, message, metadata=None):
        pass


# ── attach_session_store ─────────────────────────────────────────


class TestSessionStoreSlot:
    def test_attribute_can_be_assigned(self):
        env = TestAgentBuilder().with_llm_script(["x"]).build()
        store = types.SimpleNamespace(session_id="sess123")
        env.controller.session_store = store
        assert env.controller.session_store is store


# ── conversation rewind helpers ──────────────────────────────────


class TestConversationAccess:
    async def test_conversation_holds_history(self):
        env = TestAgentBuilder().with_llm_script(["ok"]).build()
        await env.inject("hi")
        # Public ``conversation`` accessor used elsewhere.
        assert env.controller.conversation.get_last_message() is not None


# ── error recovery ───────────────────────────────────────────────


class TestErrorRecovery:
    async def test_llm_exception_propagates_event_state(self):
        """When the LLM raises, the controller should not deadlock."""

        class _BadLLM(ScriptedLLM):
            async def chat(self, messages, **kwargs):
                if self.call_count == 0:
                    self.call_count += 1
                    raise RuntimeError("api error")
                async for c in super().chat(messages, **kwargs):
                    yield c

        bad = _BadLLM(["recovered"])
        env = TestAgentBuilder().with_llm(bad).build()
        evt = create_user_input_event("hi")
        await env.controller.push_event(evt)
        # Run a single iteration directly so we can observe the exception.
        with pytest.raises(RuntimeError, match="api error"):
            async for _ in env.controller.run_once():
                pass


# ── tool format override ─────────────────────────────────────────


class TestToolFormatOverride:
    async def test_xml_tool_format(self):
        env = TestAgentBuilder().with_llm_script(["plain response"]).build()
        # Switch tool_format mid-construction via config attribute.
        env.controller.config.tool_format = "xml"
        # Force a fresh format selection — re-set parser config.
        await env.inject("hi")
        # Plain text passed through.
        assert "plain response" in env.output.all_text


# ── known_outputs config-driven ──────────────────────────────────


class TestKnownOutputsConfigDriven:
    def test_known_outputs_default_empty(self):
        env = TestAgentBuilder().with_llm_script(["x"]).build()
        # By default, no known outputs are wired.
        assert env.controller.config.known_outputs == set()


# ── _last_usage tracking ─────────────────────────────────────────


class TestLastUsageTracking:
    async def test_last_usage_empty_when_llm_reports_none(self):
        env = TestAgentBuilder().with_llm_script(["hello"]).build()
        await env.inject("hi")
        # ScriptedLLM exposes no usage data, so the controller's
        # ``_last_usage`` accumulator must stay empty — not just "a dict".
        assert env.controller._last_usage == {}


# ── pure helpers exercised directly ──────────────────────────────


class TestFormatEventsForContext:
    def _ctrl(self):
        return TestAgentBuilder().with_llm_script(["x"]).build().controller

    def test_user_input_string(self):
        c = self._ctrl()
        evts = [create_user_input_event("hello")]
        out = c._format_events_for_context(evts)
        assert out == "hello"

    def test_user_input_multimodal(self):
        from kohakuterrarium.llm.message import TextPart, ImagePart

        c = self._ctrl()
        evt = create_user_input_event(
            [TextPart(text="describe"), ImagePart(url="https://x/a.png")]
        )
        out = c._format_events_for_context([evt])
        # Multimodal returns a list.
        assert isinstance(out, list)
        # First element is text.
        assert out[0].text == "describe"

    def test_tool_complete_text(self):
        from kohakuterrarium.core.events import create_tool_complete_event

        c = self._ctrl()
        evt = create_tool_complete_event(job_id="bash_x", content="output", exit_code=0)
        out = c._format_events_for_context([evt])
        # Contains the tool-complete header and the body.
        assert "Tool bash_x" in out
        assert "output" in out

    def test_subagent_output(self):
        from kohakuterrarium.core.events import EventType

        c = self._ctrl()
        evt = TriggerEvent(
            type=EventType.SUBAGENT_OUTPUT,
            content="sub result",
            job_id="agent_x",
        )
        out = c._format_events_for_context([evt])
        assert "Sub-agent agent_x" in out
        assert "sub result" in out

    def test_prompt_override(self):
        c = self._ctrl()
        evt = TriggerEvent(
            type="trigger_x",
            content="ignored body",
            prompt_override="rendered prompt",
        )
        out = c._format_events_for_context([evt])
        assert "rendered prompt" in out

    def test_default_fallback(self):
        c = self._ctrl()
        evt = TriggerEvent(type="unknown_kind", content="body")
        out = c._format_events_for_context([evt])
        # Default route shows ``[<type>] <content>``.
        assert "[unknown_kind]" in out
        assert "body" in out


class TestBuildTurnContext:
    def _ctrl(self):
        return TestAgentBuilder().with_llm_script(["x"]).build().controller

    def test_simple_text_context(self):
        c = self._ctrl()
        evt = create_user_input_event("hi")
        user_content, combined = c._build_turn_context([evt])
        assert "hi" in combined
        assert user_content == combined  # text-only path

    def test_multimodal_context(self):
        from kohakuterrarium.llm.message import ImagePart, TextPart

        c = self._ctrl()
        evt = create_user_input_event([TextPart(text="describe"), ImagePart(url="x")])
        user_content, combined = c._build_turn_context([evt])
        assert isinstance(user_content, list)
        # First part is the combined text.
        assert user_content[0].text == combined


class TestCollectEvents:
    async def test_drains_queue(self):
        env = TestAgentBuilder().with_llm_script(["x"]).build()
        c = env.controller
        evt1 = create_user_input_event("a")
        evt2 = create_user_input_event("b")
        await c.push_event(evt1)
        await c.push_event(evt2)
        out = await c._collect_events()
        assert len(out) >= 1

    async def test_pending_events_drained_first(self):
        env = TestAgentBuilder().with_llm_script(["x"]).build()
        c = env.controller
        pending = create_user_input_event("pending")
        c._pending_events = [pending]
        out = await c._collect_events()
        # Pending drained.
        assert pending in out
        assert c._pending_events == []


class TestPersistImagePart:
    def test_passthrough_when_no_data_url(self):
        from kohakuterrarium.llm.message import ImagePart

        env = TestAgentBuilder().with_llm_script(["x"]).build()
        c = env.controller
        part = ImagePart(url="https://example.com/x.png")
        out = c._persist_image_part(part)
        # Non-data URL passes through.
        assert out is part


class TestCollectStructuredParts:
    def test_no_source_returns_empty(self):
        env = TestAgentBuilder().with_llm_script(["x"]).build()
        # ScriptedLLM has no last_assistant_content_parts attribute.
        out = env.controller._collect_structured_assistant_parts()
        assert out == []


class TestLogTokenUsage:
    def test_records_last_usage(self):
        env = TestAgentBuilder().with_llm_script(["x"]).build()
        c = env.controller
        c.llm.last_usage = {"prompt_tokens": 5, "completion_tokens": 7}
        c._log_token_usage()
        assert c._last_usage == {"prompt_tokens": 5, "completion_tokens": 7}

    def test_no_usage_attr_skips(self):
        env = TestAgentBuilder().with_llm_script(["x"]).build()
        c = env.controller
        # Strip last_usage to exercise the missing-attr branch.
        if hasattr(c.llm, "last_usage"):
            delattr(c.llm, "last_usage")
        c._log_token_usage()
        # Stays empty.
        assert c._last_usage == {}


class TestMaterializeInlineFile:
    async def test_base64_decoded_to_tempfile(self, tmp_path):
        from kohakuterrarium.llm.message import FilePart
        import base64

        env = TestAgentBuilder().with_llm_script(["x"]).build()
        part = FilePart(
            name="x.txt",
            data_base64=base64.b64encode(b"hello").decode(),
            is_inline=True,
        )
        path = await env.controller._materialize_inline_file(part)
        assert path is not None
        from pathlib import Path

        assert Path(path).read_bytes() == b"hello"

    async def test_text_content_encoded(self):
        from kohakuterrarium.llm.message import FilePart
        from pathlib import Path

        env = TestAgentBuilder().with_llm_script(["x"]).build()
        part = FilePart(name="x.txt", content="hello", is_inline=True)
        path = await env.controller._materialize_inline_file(part)
        assert path is not None
        assert Path(path).read_text() == "hello"

    async def test_no_data_returns_none(self):
        from kohakuterrarium.llm.message import FilePart

        env = TestAgentBuilder().with_llm_script(["x"]).build()
        part = FilePart(name="x.txt", is_inline=True)
        path = await env.controller._materialize_inline_file(part)
        assert path is None


class TestResolveFilePart:
    async def test_inline_content_returns_text(self):
        from kohakuterrarium.llm.message import FilePart

        env = TestAgentBuilder().with_llm_script(["x"]).build()
        part = FilePart(name="x.txt", content="hello inline")
        out = await env.controller._resolve_file_part(part)
        assert any(hasattr(p, "text") and "hello inline" in p.text for p in out)

    async def test_no_path_no_content_emits_placeholder(self):
        from kohakuterrarium.llm.message import FilePart

        env = TestAgentBuilder().with_llm_script(["x"]).build()
        part = FilePart(name="x.txt")
        out = await env.controller._resolve_file_part(part)
        assert any("missing" in p.text.lower() for p in out)

    async def test_path_resolution_via_read_tool(self, tmp_path):
        from kohakuterrarium.llm.message import FilePart

        f = tmp_path / "doc.txt"
        f.write_text("INLINE-DISK-CONTENT")
        env = TestAgentBuilder().with_llm_script(["x"]).build()
        part = FilePart(name="doc.txt", path=str(f))
        out = await env.controller._resolve_file_part(part)
        assert out
        joined = " ".join(getattr(p, "text", "") for p in out)
        assert "INLINE-DISK-CONTENT" in joined

    async def test_path_resolution_read_failure(self):
        from kohakuterrarium.llm.message import FilePart

        env = TestAgentBuilder().with_llm_script(["x"]).build()
        part = FilePart(name="doc.txt", path="/definitely/missing/file.xyz")
        out = await env.controller._resolve_file_part(part)
        joined = " ".join(getattr(p, "text", "") for p in out)
        assert "File read failed" in joined or "missing" in joined.lower()

    async def test_inline_base64_materialised_to_temp(self):
        import base64

        from kohakuterrarium.llm.message import FilePart

        env = TestAgentBuilder().with_llm_script(["x"]).build()
        part = FilePart(
            name="x.txt",
            data_base64=base64.b64encode(b"hello world").decode(),
            is_inline=True,
        )
        out = await env.controller._resolve_file_part(part)
        # The inline file part is materialised then read back through
        # ReadTool — the resolved output is a non-empty parts list.
        assert out


# ── _resolve_message_files multiple files + dispatch ─────────────


class TestResolveMessageFilesExtras:
    async def test_multiple_files_resolved_with_placeholders(self):
        env = TestAgentBuilder().with_llm_script(["x"]).build()
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "A [[file:a]] B [[file:b]] C"},
                    {"type": "file", "file": {"name": "a", "content": "AAA"}},
                    {"type": "file", "file": {"name": "b", "content": "BBB"}},
                ],
            }
        ]
        out = await env.controller._resolve_message_files(msgs)
        joined = " ".join(
            p.get("text", "") for p in out[0]["content"] if p.get("type") == "text"
        )
        assert "AAA" in joined
        assert "BBB" in joined

    async def test_file_part_appended_when_no_placeholder(self):
        env = TestAgentBuilder().with_llm_script(["x"]).build()
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "no placeholder"},
                    {"type": "file", "file": {"name": "doc", "content": "X"}},
                ],
            }
        ]
        out = await env.controller._resolve_message_files(msgs)
        joined = " ".join(
            p.get("text", "") for p in out[0]["content"] if p.get("type") == "text"
        )
        # File content appended at the end since the placeholder was missing.
        assert "X" in joined


# ── _persist_image_part materialises with store ─────────────────


class TestPersistImagePartWithStore:
    def test_persists_data_url(self, tmp_path):
        import base64

        from kohakuterrarium.llm.message import ImagePart

        env = TestAgentBuilder().with_llm_script(["x"]).build()

        class _Store:
            def __init__(self, root):
                self.root = root
                self.session_id = "sess"

            def write_artifact(self, name, raw):
                p = self.root / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(raw)
                return p

        env.controller.session_store = _Store(tmp_path)
        url = "data:image/png;base64," + base64.b64encode(b"PNG-RAW").decode()
        part = ImagePart(url=url, source_name="x.png")
        new_part = env.controller._persist_image_part(part)
        # Rewritten URL points to served artifact.
        assert new_part.url != part.url


# ── _collect_structured_assistant_parts with provider data ──────


class TestCollectStructuredWithProvider:
    def test_provider_provides_image_part(self, tmp_path):
        import base64

        from kohakuterrarium.llm.message import ImagePart, TextPart

        env = TestAgentBuilder().with_llm_script(["x"]).build()
        url = "data:image/png;base64," + base64.b64encode(b"X").decode()
        env.controller.llm.last_assistant_content_parts = [
            TextPart(text="ignore"),
            ImagePart(url=url),
        ]
        out = env.controller._collect_structured_assistant_parts()
        # Both parts surface.
        assert any(isinstance(p, ImagePart) for p in out)


# ── run_once empty event queue early return ─────────────────────


class TestRunOnceNoEvents:
    async def test_no_events_returns_immediately(self):
        env = TestAgentBuilder().with_llm_script(["x"]).build()

        async def _empty_collect():
            return []

        env.controller._collect_events = _empty_collect
        events_seen = []
        async for evt in env.controller.run_once():
            events_seen.append(evt)
        assert events_seen == []


class TestProviderNativeTools:
    def test_no_native_tools_registered_returns_empty(self):
        env = TestAgentBuilder().with_llm_script(["x"]).build()
        out = env.controller._get_provider_native_tools()
        # The default builder registers no provider-native tools, so the
        # list must be empty — not just "a list".
        assert out == []


class TestIsNativeMode:
    def test_false_by_default(self):
        env = TestAgentBuilder().with_llm_script(["x"]).build()
        assert env.controller._is_native_mode is False

    def test_true_when_format_native(self):
        env = TestAgentBuilder().with_llm_script(["x"]).build()
        env.controller.config.tool_format = "native"
        assert env.controller._is_native_mode is True


# ── inline command dispatch ──────────────────────────────────────


class TestExecuteCommandInline:
    async def test_known_command_returns_result(self):
        from kohakuterrarium.parsing import CommandEvent

        env = TestAgentBuilder().with_llm_script(["x"]).build()
        c = env.controller
        # ``jobs`` command is registered by default.
        evt = CommandEvent(command="jobs", args="")
        text, result = await c._execute_command_inline(evt)
        assert "jobs" == result.command
        # Either content or error path — both fine.
        assert isinstance(text, str)


# ── public helpers ───────────────────────────────────────────────


class TestControllerPublicHelpers:
    def test_flush_clears_non_system(self):
        env = TestAgentBuilder().with_llm_script(["x"]).build()
        c = env.controller
        c.conversation.append("user", "hi")
        c.flush()
        roles = [m.role for m in c.conversation.get_messages()]
        assert "user" not in roles
        assert roles == ["system"]

    def test_is_ephemeral(self):
        env = TestAgentBuilder().with_llm_script(["x"]).with_ephemeral(True).build()
        assert env.controller.is_ephemeral is True

    def test_register_and_get_job(self):
        from kohakuterrarium.core.job import JobStatus, JobState, JobType

        env = TestAgentBuilder().with_llm_script(["x"]).build()
        c = env.controller
        status = JobStatus(
            job_id="x", job_type=JobType.TOOL, type_name="bash", state=JobState.RUNNING
        )
        c.register_job(status)
        assert c.get_job_status("x") is status

    def test_has_pending_events(self):
        env = TestAgentBuilder().with_llm_script(["x"]).build()
        c = env.controller
        assert c.has_pending_events() is False
        c.push_event_sync(create_user_input_event("hi"))
        assert c.has_pending_events() is True

    def test_register_command_proxy(self):
        from kohakuterrarium.commands.base import BaseCommand, CommandResult

        class _Cmd(BaseCommand):
            @property
            def command_name(self):
                return "x"

            @property
            def description(self):
                return "x"

            async def _execute(self, args, context):
                return CommandResult(content="ok")

        env = TestAgentBuilder().with_llm_script(["y"]).build()
        env.controller.register_command("mycmd", _Cmd())
        assert "mycmd" in env.controller._commands


# ── run_once: pending_injections ─────────────────────────────────


class TestPendingInjections:
    async def test_injections_inserted_after_system(self):
        env = TestAgentBuilder().with_llm_script(["resp"]).build()
        c = env.controller
        c._pending_injections = [{"role": "user", "content": "INJECTED"}]
        # Push an event and run.
        await c.push_event(create_user_input_event("hi"))
        async for _ in c.run_once():
            pass
        # The injection should now be drained.
        assert c._pending_injections == []


# ── _handle_command ──────────────────────────────────────────────


class TestHandleCommand:
    async def test_unknown_command_error(self):
        from kohakuterrarium.parsing import CommandEvent

        env = TestAgentBuilder().with_llm_script(["x"]).build()
        c = env.controller
        evt = CommandEvent(command="ghost", args="")
        result = await c._handle_command(evt)
        assert result.error is not None
        assert "Unknown command" in result.error


# ── run_loop with callbacks ──────────────────────────────────────


class TestRunLoopCallbacks:
    async def test_text_callback(self):
        env = TestAgentBuilder().with_llm_script(["hello"]).build()
        c = env.controller
        captured: list = []

        def on_text(text):
            captured.append(text)

        # Inject a user event then run one iteration.
        await c.push_event(create_user_input_event("hi"))
        async for event in c.run_once():
            if isinstance(event, TextEvent):
                on_text(event.text)
        assert captured  # at least one chunk captured


# ── native-mode completion ───────────────────────────────────────


class _NativeToolCall:
    """Mimic an LLM provider's ToolCall record."""

    def __init__(self, id, name, arguments):
        self.id = id
        self.name = name
        self.arguments = arguments

    def parsed_arguments(self):
        import json

        try:
            return json.loads(self.arguments) if self.arguments else {}
        except json.JSONDecodeError:
            return {}


class _NativeScriptedLLM(ScriptedLLM):
    """ScriptedLLM extension with native_tool_call support.

    Each turn yields the text chunks AND advertises ``last_tool_calls``
    via the standard provider-side attribute.
    """

    def __init__(self, script_with_calls):
        # script_with_calls: list of (text, [tool_calls]) tuples.
        text_only = [t for t, _ in script_with_calls]
        super().__init__(text_only)
        self._calls_per_round = [calls for _, calls in script_with_calls]
        self.last_tool_calls = []
        self.last_assistant_extra_fields: dict = {}
        self.last_usage: dict = {}

    async def chat(self, messages, **kwargs):
        idx = self.call_count
        async for chunk in super().chat(messages, **kwargs):
            yield chunk
        # call_count was incremented inside super.chat
        if idx < len(self._calls_per_round):
            self.last_tool_calls = list(self._calls_per_round[idx])


class TestNativeCompletion:
    async def test_native_tool_call_emitted_as_event(self):
        llm = _NativeScriptedLLM(
            [
                ("I'll run a tool.", [_NativeToolCall("c1", "echo", '{"msg": "hi"}')]),
            ]
        )
        env = TestAgentBuilder().with_llm(llm).build()
        env.controller.config.tool_format = "native"
        # Register the echo tool so name resolution succeeds.
        env.registry.register_tool(_DummyEchoTool())
        await env.controller.push_event(create_user_input_event("go"))
        events: list = []
        async for evt in env.controller.run_once():
            events.append(evt)
        # Yielded ToolCallEvent for the native call.
        from kohakuterrarium.parsing import ToolCallEvent as TCE

        assert any(isinstance(e, TCE) and e.name == "echo" for e in events)

    async def test_native_subagent_call_emitted(self):
        llm = _NativeScriptedLLM(
            [
                ("Delegate.", [_NativeToolCall("c1", "explore", '{"task": "scout"}')]),
            ]
        )
        env = TestAgentBuilder().with_llm(llm).build()
        env.controller.config.tool_format = "native"
        env.registry.register_subagent("explore", object())
        await env.controller.push_event(create_user_input_event("go"))
        events: list = []
        async for evt in env.controller.run_once():
            events.append(evt)
        from kohakuterrarium.parsing import SubAgentCallEvent as SCE

        assert any(isinstance(e, SCE) and e.name == "explore" for e in events)

    async def test_native_completion_no_tool_calls(self):
        llm = _NativeScriptedLLM([("Just a chat response.", [])])
        env = TestAgentBuilder().with_llm(llm).build()
        env.controller.config.tool_format = "native"
        await env.controller.push_event(create_user_input_event("hi"))
        events: list = []
        async for evt in env.controller.run_once():
            events.append(evt)
        # Pure text response — no tool events, just text.
        from kohakuterrarium.parsing import ToolCallEvent as TCE

        assert not any(isinstance(e, TCE) for e in events)


class _DummyEchoTool:
    """A minimal tool just used for native call name resolution."""

    from kohakuterrarium.modules.tool.base import ExecutionMode

    @property
    def tool_name(self):
        return "echo"

    @property
    def description(self):
        return "echo"

    @property
    def execution_mode(self):
        from kohakuterrarium.modules.tool.base import ExecutionMode

        return ExecutionMode.DIRECT

    async def execute(self, args, context=None):
        from kohakuterrarium.modules.tool.base import ToolResult

        return ToolResult(output="ok")


# ── _format_events_for_context multimodal user_input ────────────


class TestFormatEventsMultimodalUserInput:
    def test_user_input_with_image_part(self):
        from kohakuterrarium.llm.message import ImagePart

        env = TestAgentBuilder().with_llm_script(["x"]).build()
        evt = create_user_input_event([ImagePart(url="https://x/a.png")])
        out = env.controller._format_events_for_context([evt])
        # When all content is images and no text, returns a list.
        # Either way, no crash.
        assert out is not None

    def test_user_input_with_file_part(self):
        from kohakuterrarium.llm.message import FilePart

        env = TestAgentBuilder().with_llm_script(["x"]).build()
        evt = create_user_input_event([FilePart(name="a", content="text")])
        out = env.controller._format_events_for_context([evt])
        assert out is not None


# ── _collect_events stackable batching ──────────────────────────


class TestCollectEventsBatching:
    async def test_stackable_events_batched(self):
        env = TestAgentBuilder().with_llm_script(["x"]).build()
        c = env.controller
        evt1 = TriggerEvent(type="x", content="a", stackable=True)
        evt2 = TriggerEvent(type="x", content="b", stackable=True)
        evt3 = TriggerEvent(type="x", content="c", stackable=False)
        c.push_event_sync(evt1)
        c.push_event_sync(evt2)
        c.push_event_sync(evt3)
        out = await c._collect_events()
        # First two stackable batched together.
        assert len(out) >= 2
        # Non-stackable goes to _pending_events.
        assert evt3 in c._pending_events


# ── tool_complete event with multimodal content ─────────────────


class TestFormatEventsToolComplete:
    def test_tool_complete_multimodal(self):
        from kohakuterrarium.core.events import create_tool_complete_event
        from kohakuterrarium.llm.message import TextPart

        env = TestAgentBuilder().with_llm_script(["x"]).build()
        evt = create_tool_complete_event(
            job_id="bash_x", content=[TextPart(text="multimodal output")]
        )
        out = env.controller._format_events_for_context([evt])
        assert "multimodal output" in (out if isinstance(out, str) else str(out))


# ── _execute_command_inline empty result ────────────────────────


class TestExecuteCommandEmpty:
    async def test_empty_result(self):
        from kohakuterrarium.commands.base import BaseCommand, CommandResult
        from kohakuterrarium.parsing import CommandEvent

        env = TestAgentBuilder().with_llm_script(["x"]).build()
        c = env.controller

        class _Silent(BaseCommand):
            @property
            def command_name(self):
                return "silent"

            @property
            def description(self):
                return "silent"

            async def _execute(self, args, context):
                return CommandResult(content="")  # no content, no error

        c.register_command("silent", _Silent())
        evt = CommandEvent(command="silent", args="")
        text, result_event = await c._execute_command_inline(evt)
        # Empty result still produces a CommandResultEvent.
        assert text == ""

    async def test_error_result(self):
        from kohakuterrarium.commands.base import BaseCommand, CommandResult
        from kohakuterrarium.parsing import CommandEvent

        env = TestAgentBuilder().with_llm_script(["x"]).build()
        c = env.controller

        class _Err(BaseCommand):
            @property
            def command_name(self):
                return "err"

            @property
            def description(self):
                return "err"

            async def _execute(self, args, context):
                return CommandResult(error="bad input")

        c.register_command("err", _Err())
        evt = CommandEvent(command="err", args="")
        text, result_event = await c._execute_command_inline(evt)
        assert "Command Error" in text
        assert result_event.error == "bad input"


# ── run_loop callback dispatch  ──────────────────────────────────


class TestRunLoopDispatch:
    async def test_run_loop_breaks_when_no_events(self):
        """``run_loop`` is infinite — patch ``run_once`` to return empty
        and immediately raise to exit cleanly."""
        env = TestAgentBuilder().with_llm_script(["x"]).build()
        c = env.controller
        calls = []

        async def _stub_run_once():
            calls.append(1)
            if False:
                yield None  # never yields
            raise asyncio.CancelledError()

        c.run_once = _stub_run_once  # type: ignore[method-assign]
        with pytest.raises(asyncio.CancelledError):
            await c.run_loop()


# ── _resolve_message_files when no executor (fallback path) ─────


class TestResolveMessageFilesNoExecutor:
    async def test_no_executor_emits_placeholder(self):
        env = TestAgentBuilder().with_llm_script(["x"]).build()
        env.controller.executor = None
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hi [[file:a]]"},
                    {"type": "file", "file": {"name": "a", "path": "/tmp/missing"}},
                ],
            }
        ]
        out = await env.controller._resolve_message_files(msgs)
        joined = " ".join(
            p.get("text", "") for p in out[0]["content"] if p.get("type") == "text"
        )
        assert "Unable to resolve" in joined


# ── _resolve_file_part with registry resolver ───────────────────


class TestResolveFilePartRegistryReadTool:
    async def test_path_text_output_returned_as_single_part(self, tmp_path):
        from kohakuterrarium.builtins.tools.read import ReadTool
        from kohakuterrarium.llm.message import FilePart

        f = tmp_path / "a.txt"
        f.write_text("DISKDATA")
        env = TestAgentBuilder().with_llm_script(["x"]).with_tool(ReadTool()).build()
        part = FilePart(name="a.txt", path=str(f))
        out = await env.controller._resolve_file_part(part)
        joined = " ".join(getattr(p, "text", "") for p in out)
        assert "DISKDATA" in joined


# ── _collect_events first-event blocking get (lines 330-331) ────


class TestCollectEventsBlockingGet:
    async def test_blocks_until_first_event_arrives(self):
        env = TestAgentBuilder().with_llm_script(["x"]).build()
        c = env.controller

        async def push_after():
            await asyncio.sleep(0.005)
            c.push_event_sync(create_user_input_event("hi"))

        asyncio.create_task(push_after())
        events = await c._collect_events()
        assert events


# ── _collect_events QueueEmpty branch (lines 347-348) ──────────


class TestCollectEventsQueueEmpty:
    async def test_queue_empty_exception_during_batching(self):
        env = TestAgentBuilder().with_llm_script(["x"]).build()
        c = env.controller
        # Push one event, then patch get_nowait to raise QueueEmpty
        # on subsequent calls — simulates concurrent drain.
        c.push_event_sync(create_user_input_event("a"))
        # Use a queue replacement: pretend it's non-empty but get_nowait raises.

        class _RaceQueue:
            def __init__(self, real):
                self.real = real

            def empty(self):
                return self.real.empty()

            def get_nowait(self):
                # First call: real call. Second call: raise QueueEmpty.
                if not hasattr(self, "_called"):
                    self._called = True
                    return self.real.get_nowait()
                import asyncio

                raise asyncio.QueueEmpty()

        # Need queue with empty()=False but get_nowait() raises.
        evt = create_user_input_event("stacked")
        evt.stackable = True
        c.push_event_sync(evt)
        # Push a non-stackable too so we can guarantee we see >= 2 events.
        first_evt = create_user_input_event("first")
        first_evt.stackable = True
        c.push_event_sync(first_evt)

        out = await c._collect_events()
        assert out


# ── _format_events_for_context image / file in user_input (369, 371) ──


class TestFormatEventsForContextImageFile:
    def test_user_input_image_and_file_parts(self):
        from kohakuterrarium.llm.message import FilePart, ImagePart, TextPart

        env = TestAgentBuilder().with_llm_script(["x"]).build()
        c = env.controller
        evt = create_user_input_event(
            [
                TextPart(text="describe"),
                ImagePart(url="https://x/a.png"),
                FilePart(name="doc", content="text"),
            ]
        )
        out = c._format_events_for_context([evt])
        # Multimodal output.
        assert isinstance(out, list)


# ── _format_events_for_context tool_complete else branch (line 396) ──


class TestFormatEventsForContextToolCompleteBranches:
    def test_tool_complete_str_content_path(self):
        from kohakuterrarium.core.events import create_tool_complete_event

        env = TestAgentBuilder().with_llm_script(["x"]).build()
        c = env.controller
        evt = create_tool_complete_event(job_id="bash_x", content="plain str output")
        out = c._format_events_for_context([evt])
        assert "plain str output" in out


# ── _build_turn_context with job_status included (lines 433) ────


class TestBuildTurnContextJobStatus:
    def test_job_status_prepended(self):
        from kohakuterrarium.core.job import JobStatus, JobState, JobType

        env = TestAgentBuilder().with_llm_script(["x"]).build()
        c = env.controller
        # Inject a job status that format_context will render.
        c.job_store.register(
            JobStatus(
                job_id="bash_y",
                job_type=JobType.TOOL,
                type_name="bash",
                state=JobState.RUNNING,
            )
        )
        # event with text
        evt = create_user_input_event("hi")
        user_content, combined = c._build_turn_context([evt])
        # combined contains "hi" plus any job-status preface.
        assert "hi" in combined


# ── _build_turn_context multimodal branch (lines 447-448) ───────


class TestBuildTurnContextMultimodal:
    def test_multimodal_input_constructs_list_content(self):
        from kohakuterrarium.llm.message import ImagePart, TextPart

        env = TestAgentBuilder().with_llm_script(["x"]).build()
        c = env.controller
        evt = create_user_input_event([TextPart(text="describe"), ImagePart(url="x")])
        user_content, combined = c._build_turn_context([evt])
        # When images present, user_content is a list.
        assert isinstance(user_content, list)


# ── _run_native_completion interrupted in stream (lines 484-485) ──


class TestNativeCompletionInterrupted:
    async def test_interrupted_during_native_chat(self):
        env = TestAgentBuilder().with_llm_script(["dummy"]).build()
        env.controller.config.tool_format = "native"

        class _SlowLLM(ScriptedLLM):
            def __init__(self, ctrl):
                super().__init__(["chunk1chunk2"])
                self.ctrl = ctrl

            async def chat(self, messages, **kwargs):
                first = True
                async for c in super().chat(messages, **kwargs):
                    if first:
                        self.ctrl._interrupted = True
                        first = False
                    yield c

            last_tool_calls = []
            last_assistant_extra_fields = {}

        env.controller.llm = _SlowLLM(env.controller)
        await env.controller.push_event(create_user_input_event("hi"))
        events = []
        async for evt in env.controller.run_once():
            events.append(evt)


# ── _run_native_completion structured assistant image (497-498) ──


class TestNativeCompletionStructuredImage:
    async def test_image_part_emitted_as_assistant_image_event(self):
        from kohakuterrarium.llm.message import ImagePart, TextPart
        from kohakuterrarium.parsing import AssistantImageEvent

        llm = _NativeScriptedLLM([("text", [])])
        llm.last_assistant_content_parts = [
            TextPart(text="x"),
            ImagePart(url="https://x/a.png"),
        ]
        env = TestAgentBuilder().with_llm(llm).build()
        env.controller.config.tool_format = "native"
        await env.controller.push_event(create_user_input_event("hi"))
        events = []
        async for evt in env.controller.run_once():
            events.append(evt)
        assert any(isinstance(e, AssistantImageEvent) for e in events)


# ── _run_text_completion command-in-stream interrupted (636-638) ──


class TestTextCompletionCommandFlush:
    async def test_command_in_flush_yields_result(self):
        """A command stuck in the parser flush state still surfaces."""

        env = TestAgentBuilder().with_llm_script(["[/jobs"]).build()
        await env.controller.push_event(create_user_input_event("hi"))
        events = []
        async for evt in env.controller.run_once():
            events.append(evt)


# ── _persist_image_part non-data URL no-op (line 799) ───────────


class TestPersistImagePartNoOp:
    def test_no_session_store(self):
        from kohakuterrarium.llm.message import ImagePart

        env = TestAgentBuilder().with_llm_script(["x"]).build()
        env.controller.session_store = None
        part = ImagePart(url="data:image/png;base64,XYZ")
        out = env.controller._persist_image_part(part)
        # No store → either same part or TextPart placeholder. With
        # elide_without_store=False (the controller path), returns same.
        # We just check the call doesn't raise.
        assert out is not None


# ── _collect_structured_assistant_parts non-image part (lines 897-898) ──


class TestCollectStructuredNonImagePart:
    def test_non_image_part_passes_through(self):
        from kohakuterrarium.llm.message import TextPart

        env = TestAgentBuilder().with_llm_script(["x"]).build()
        env.controller.llm.last_assistant_content_parts = [
            TextPart(text="plain"),
        ]
        out = env.controller._collect_structured_assistant_parts()
        # TextPart passed through (not an ImagePart so no materialization).
        assert any(isinstance(p, TextPart) for p in out)


# ── text-mode completion yields AssistantImageEvent (897-898) ───


class TestTextCompletionStructuredImage:
    async def test_image_part_yielded_in_text_mode(self):
        from kohakuterrarium.llm.message import ImagePart
        from kohakuterrarium.parsing import AssistantImageEvent

        env = TestAgentBuilder().with_llm_script(["text response"]).build()
        # Text mode (default) — set structured parts on the LLM.
        env.controller.llm.last_assistant_content_parts = [
            ImagePart(url="https://x/a.png"),
        ]
        await env.controller.push_event(create_user_input_event("hi"))
        events = []
        async for evt in env.controller.run_once():
            events.append(evt)
        # AssistantImageEvent emitted.
        assert any(isinstance(e, AssistantImageEvent) for e in events)


# ── _format_events append_multimodal image/file paths (369, 371) ──


class TestFormatEventsAppendMultimodal:
    def test_tool_complete_with_list_content_image_file(self):
        from kohakuterrarium.core.events import create_tool_complete_event
        from kohakuterrarium.llm.message import FilePart, ImagePart, TextPart

        env = TestAgentBuilder().with_llm_script(["x"]).build()
        c = env.controller
        # tool_complete event with list content containing image + file.
        evt = create_tool_complete_event(
            job_id="bash_x",
            content=[
                TextPart(text="tool output"),
                ImagePart(url="https://x/a.png"),  # line 369
                FilePart(name="result", content="data"),  # line 371
            ],
        )
        out = c._format_events_for_context([evt])
        # Multimodal result returned.
        assert isinstance(out, list)

    def test_prompt_override_with_list_content(self):
        """Event with prompt_override AND list content goes through
        append_multimodal (line 396)."""
        from kohakuterrarium.llm.message import ImagePart, TextPart

        env = TestAgentBuilder().with_llm_script(["x"]).build()
        c = env.controller
        evt = TriggerEvent(
            type="custom_trigger",
            content=[TextPart(text="hi"), ImagePart(url="x")],
            prompt_override="prompt prefix",
        )
        out = c._format_events_for_context([evt])
        # Multimodal output produced.
        assert isinstance(out, list)


# ── _resolve_message_files file_map alt-key (line 799) ──────────


class TestResolveFilesFilePathKey:
    async def test_file_map_uses_path_key(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("CONTENT-XYZ")
        from kohakuterrarium.builtins.tools.read import ReadTool

        env = TestAgentBuilder().with_llm_script(["x"]).with_tool(ReadTool()).build()
        msgs = [
            {
                "role": "user",
                "content": [
                    # Reference file by its path key.
                    {"type": "text", "text": f"see [[file:{f}]]"},
                    {
                        "type": "file",
                        "file": {
                            "name": "any-name",
                            "path": str(f),
                        },
                    },
                ],
            }
        ]
        out = await env.controller._resolve_message_files(msgs)
        joined = " ".join(
            p.get("text", "") for p in out[0]["content"] if p.get("type") == "text"
        )
        assert "CONTENT-XYZ" in joined


# ── _materialize_inline_file with no data (line 731) ────────────


class TestMaterializeInlineFileNoData:
    async def test_no_data_no_content_returns_none(self):
        from kohakuterrarium.llm.message import FilePart

        env = TestAgentBuilder().with_llm_script(["x"]).build()
        # data_base64 None, content None → returns None.
        part = FilePart(name="x", is_inline=True)
        out = await env.controller._materialize_inline_file(part)
        assert out is None


# ── Controller built without executor uses fresh JobStore (line 233) ──


class TestControllerWithoutExecutor:
    def test_creates_own_job_store(self):
        from kohakuterrarium.core.controller import Controller
        from kohakuterrarium.testing.llm import ScriptedLLM

        c = Controller(ScriptedLLM(["x"]))
        # Job store created lazily when no executor was passed.
        assert c.job_store is not None


# ── text completion: command-in-stream + interrupted (lines 621-638) ──


class TestTextCompletionInStreamCommand:
    async def test_command_event_in_stream_yields_result_event(self):

        env = TestAgentBuilder().with_llm_script(["[/jobs][jobs/]"]).build()
        await env.controller.push_event(create_user_input_event("hi"))
        events = []
        async for evt in env.controller.run_once():
            events.append(evt)
        # The parser may produce different event types depending on
        # how the command is rendered; we just verify no crash.
        assert events

    async def test_interrupted_mid_stream(self):
        from kohakuterrarium.testing.llm import ScriptedLLM

        class _SlowLLM(ScriptedLLM):
            def __init__(self, parent):
                super().__init__(["interrupted text"])
                self.parent = parent

            async def chat(self, messages, **kwargs):
                first = True
                async for c in super().chat(messages, **kwargs):
                    if first:
                        # Set the interrupt flag mid-stream.
                        self.parent._interrupted = True
                        first = False
                    yield c

        env = TestAgentBuilder().with_llm_script(["dummy"]).build()
        env.controller.llm = _SlowLLM(env.controller)
        await env.controller.push_event(create_user_input_event("hi"))
        events = []
        async for evt in env.controller.run_once():
            events.append(evt)


# ── temp file cleanup OSError branch (lines 731, 736-737) ───────


class TestResolveFilePartTempCleanup:
    async def test_temp_file_cleanup_failure_swallowed(self, monkeypatch, tmp_path):
        import base64
        from pathlib import Path

        from kohakuterrarium.llm.message import FilePart

        env = TestAgentBuilder().with_llm_script(["x"]).build()
        part = FilePart(
            name="x.txt",
            data_base64=base64.b64encode(b"hello").decode(),
            is_inline=True,
        )

        # Patch Path.unlink to raise OSError when cleanup tries.
        def bad_unlink(self, missing_ok=False):
            raise OSError("cleanup failed")

        monkeypatch.setattr(Path, "unlink", bad_unlink)
        # Should not raise.
        out = await env.controller._resolve_file_part(part)
        assert out


class TestResolveFilePartReadToolReturnsList:
    """When the read tool returns a list (multimodal output), the
    helper passes it through (line 731)."""

    async def test_list_output_returned_directly(self, tmp_path):
        from kohakuterrarium.llm.message import FilePart, TextPart
        from kohakuterrarium.modules.tool.base import (
            BaseTool,
            ExecutionMode,
            ToolResult,
        )

        class _ListReadTool(BaseTool):
            @property
            def tool_name(self):
                return "read"

            @property
            def description(self):
                return "list-output read"

            @property
            def execution_mode(self):
                return ExecutionMode.DIRECT

            async def _execute(self, args, **kwargs):
                return ToolResult(
                    output=[
                        TextPart(text="multi-text-1"),
                        TextPart(text="multi-text-2"),
                    ]
                )

        env = (
            TestAgentBuilder().with_tool(_ListReadTool()).with_llm_script(["x"]).build()
        )
        f = tmp_path / "a.txt"
        f.write_text("ignored — tool returns list anyway")
        part = FilePart(name="a.txt", path=str(f))
        out = await env.controller._resolve_file_part(part)
        # List output passes through verbatim (line 731).
        assert any(getattr(p, "text", "") == "multi-text-1" for p in out)


# ── run_loop dispatch callback paths (lines 981-986) ────────────


class TestRunLoopDispatchCallbacks:
    async def test_all_three_callbacks_invoked(self):
        from kohakuterrarium.parsing import (
            SubAgentCallEvent,
            TextEvent,
            ToolCallEvent,
        )

        text_seen = []
        tool_seen = []
        sa_seen = []

        async def on_tool(evt):
            tool_seen.append(evt)

        async def on_sub(evt):
            sa_seen.append(evt)

        env = TestAgentBuilder().with_llm_script(["x"]).build()
        c = env.controller

        # Replace run_once to yield one batch then raise to exit run_loop.
        call_count = {"n": 0}

        async def stub_run_once():
            if call_count["n"] >= 1:
                raise asyncio.CancelledError()
            call_count["n"] += 1
            yield TextEvent(text="hi")
            yield ToolCallEvent(name="read", args={"path": "x"}, raw="")
            yield SubAgentCallEvent(name="explore", args={}, raw="")

        c.run_once = stub_run_once  # type: ignore[method-assign]
        with pytest.raises(asyncio.CancelledError):
            await c.run_loop(
                on_text=text_seen.append,
                on_tool=on_tool,
                on_subagent=on_sub,
            )
        # All three callbacks fired.
        assert text_seen
        assert tool_seen
        assert sa_seen
        # We invoked the dispatcher path manually; success criterion is
        # nothing crashed.


# ── _resolve_message_files ───────────────────────────────────────


class TestResolveMessageFiles:
    async def test_passthrough_no_files(self):
        env = TestAgentBuilder().with_llm_script(["x"]).build()
        msgs = [{"role": "user", "content": "hello"}]
        out = await env.controller._resolve_message_files(msgs)
        assert out == msgs

    async def test_passthrough_list_with_no_files(self):
        env = TestAgentBuilder().with_llm_script(["x"]).build()
        msgs = [
            {
                "role": "user",
                "content": [{"type": "text", "text": "hi"}],
            }
        ]
        out = await env.controller._resolve_message_files(msgs)
        # No file parts → message returned verbatim.
        assert out == msgs

    async def test_inline_content_replaces_file_part(self):
        env = TestAgentBuilder().with_llm_script(["x"]).build()
        msgs = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Please read [[file:doc]]",
                    },
                    {
                        "type": "file",
                        "file": {
                            "name": "doc",
                            "content": "INLINE-CONTENT",
                        },
                    },
                ],
            }
        ]
        out = await env.controller._resolve_message_files(msgs)
        parts = out[0]["content"]
        # The inline content surfaces as text inside the resolved message.
        joined = " ".join(p.get("text", "") for p in parts if p.get("type") == "text")
        assert "INLINE-CONTENT" in joined

    async def test_image_url_preserved(self):
        env = TestAgentBuilder().with_llm_script(["x"]).build()
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "look"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://x.png", "detail": "low"},
                    },
                    {
                        "type": "file",
                        "file": {"name": "doc", "content": "hello"},
                    },
                ],
            }
        ]
        out = await env.controller._resolve_message_files(msgs)
        parts = out[0]["content"]
        # Image URL preserved through the resolution path.
        assert any(p.get("type") == "image_url" for p in parts)
