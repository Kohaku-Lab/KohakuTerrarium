"""Integration suite for ``kohakuterrarium.core`` — the agent runtime.

Each test method here drives a *complete* feature workflow of the
``core/`` package end-to-end through a real :class:`Agent`, constructed
and hosted exactly the way the real consumers do it:

* :mod:`kohakuterrarium.bootstrap.agent_init` builds the agent from an
  ``AgentConfig`` (``Agent(config)`` runs the whole ``AgentInitMixin``
  chain — LLM, registry, executor, sub-agents, controller, I/O).
* :mod:`kohakuterrarium.terrarium.creature_host` wraps that live
  ``Agent`` in a :class:`Creature` and adds it to a real
  :class:`Terrarium` engine; ``Creature.chat`` is the canonical
  inject-input + drain-output cycle every HTTP / WS / CLI endpoint
  uses.

The ONLY seam is the LLM: both ``create_llm_provider`` import sites
(``bootstrap.llm`` and ``bootstrap.agent_init``) are monkeypatched to a
:class:`ScriptedLLM`. Every other collaborator — controller loop,
executor, sub-agent manager, output router, compact manager,
termination checker, the Terrarium engine — is the real thing.

No shape asserts: every assertion pins an exact value or an observable
side effect (conversation contents, tool output text, engine state).
"""

import asyncio
import base64
import copy
import json
import re
import time
from contextlib import closing
from pathlib import Path
from typing import Callable

import pytest
from PIL import Image

import kohakuterrarium.core.agent_message_history as branch_history
from kohakuterrarium.bootstrap import agent_init as _agent_init_mod
from kohakuterrarium.bootstrap import llm as _bootstrap_llm_mod
from kohakuterrarium.builtins.tools.canvas_image import CanvasImageTool
from kohakuterrarium.builtins.tools.read import ReadTool
from kohakuterrarium.builtins.tools.show_app import ShowAppTool
from kohakuterrarium.builtins.tools.stop_task import StopTaskTool
from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.branch_continuation import (
    live_branch_path,
    load_persisted_branch_view,
    persist_branch_view,
    selected_branch_view,
)
from kohakuterrarium.core.config_types import (
    AgentConfig,
    InputConfig,
    OutputConfig,
)
from kohakuterrarium.builtins.user_commands.clear import ClearCommand
from kohakuterrarium.builtins.user_commands.repair_images import RepairImagesCommand
from kohakuterrarium.core.conversation import Conversation
from kohakuterrarium.core.events import (
    EventType,
    TriggerEvent,
    create_tool_complete_event,
    create_user_input_event,
)
from kohakuterrarium.core.pinned import pin_message
from kohakuterrarium.core.turn import Activity, TurnCapture
from kohakuterrarium.llm.message import FilePart, ImagePart, TextPart
from kohakuterrarium.llm.media.artifacts import resolve_artifact_url
from kohakuterrarium.llm.base import NativeToolCall
from kohakuterrarium.llm.codex.format import to_responses_input
from kohakuterrarium.llm.recovery.errors import drop_last_tool_round
from kohakuterrarium.session import resume as resume_history
from kohakuterrarium.session import resume_branch
from kohakuterrarium.session.history import replay_conversation
from kohakuterrarium.session.raw_history import UserMessageSelector
from kohakuterrarium.modules.user_command.base import UserCommandContext
from kohakuterrarium.session.resume import resume_agent
from kohakuterrarium.session.resume_build import build_conversation
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.session.reader import SessionReader
from kohakuterrarium.skills.registry import Skill
from kohakuterrarium.terrarium.service import LocalTerrariumService
from kohakuterrarium.modules.plugin.base import BasePlugin, PluginBlockError
from kohakuterrarium.modules.subagent.config import SubAgentConfig
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolConfig,
    ToolResult,
)
from kohakuterrarium.modules.trigger.base import BaseTrigger
from kohakuterrarium.parsing import ToolCallEvent
from kohakuterrarium.terrarium.creature_host import Creature
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.testing.llm import ScriptedLLM, ScriptEntry
from kohakuterrarium.testing.output import OutputRecorder

from tests.helpers.stable_compact_history import exercise_compact_media_history

# ---------------------------------------------------------------------------
# Deterministic tool stubs — real BaseTool subclasses, no faked methods.
# ---------------------------------------------------------------------------


class _HistoryReplayLLM(ScriptedLLM):
    """Supply native/text tool calls and a cancellable response for history workflows."""

    def __init__(self, native, plan=None):
        super().__init__(["OK"])
        self.native = native
        self.plan = plan
        self.started = asyncio.Event()
        self.last_tool_calls = []

    async def chat(self, messages, **kwargs):
        normalized = self._normalize_messages(messages)
        self.call_log.append(normalized)
        self.call_count += 1
        for item in to_responses_input(normalized):
            if item.get("type") == "function_call":
                assert re.fullmatch(r"[a-zA-Z0-9_-]+", item["name"])
        self.last_tool_calls = []
        if self.plan is not None:
            step = self.plan[min(self.call_count - 1, len(self.plan) - 1)]
            if isinstance(step, list):
                self.last_tool_calls = step
                yield ""
            else:
                yield step
            return
        if self.call_count == 1:
            if self.native:
                self.last_tool_calls = [
                    NativeToolCall(
                        "call_history_1",
                        "scratchpad",
                        '{"action":"set","key":"history","value":"preserved"}',
                    )
                ]
                yield ""
            else:
                yield "[/scratchpad]@@action=set\n@@key=history\n@@value=preserved\n[scratchpad/]"
        elif self.call_count == 3:
            self.started.set()
            await asyncio.Event().wait()
        else:
            yield "OK"


class _AssistantMediaLLM(ScriptedLLM):
    """Generate two ordered structured responses and plain continuation replies."""

    def __init__(self):
        super().__init__(["continued"])
        self.generated = 0
        self.last_assistant_content_parts = []
        self.last_assistant_extra_fields = {}

    async def chat(self, messages, **kwargs):
        self.call_log.append(copy.deepcopy(self._normalize_messages(messages)))
        self.call_count += 1
        self.last_assistant_content_parts = []
        self.last_assistant_extra_fields = {}
        if self.call_count <= 2:
            self.generated += 1
            version = "A" if self.generated == 1 else "B"
            encoded = base64.b64encode(f"ASSISTANT_IMAGE_{version}".encode()).decode()
            self.last_assistant_content_parts = [
                TextPart(text=f"structured_{version}"),
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{encoded}",
                        "detail": "high",
                        "provider_token": version,
                    },
                    "meta": {
                        "source_name": "same.png",
                        "source_type": "generated",
                        "extension": {"version": version},
                    },
                },
                FilePart(
                    name="same.txt", content=f"ASSISTANT_FILE_{version}", is_inline=True
                ),
                TextPart(text=f"tail_{version}"),
            ]
            self.last_assistant_extra_fields = {
                "provider_extension": {"state": [version]}
            }
            yield f"streamed_{version}"
        else:
            yield "continued"


class _LegacyImageReadTool(ReadTool):
    """Emit the live image reference used by pre-snapshot read results."""

    async def execute(self, args, context=None):
        result = await super().execute(args, context)
        result.output[1].url = Path(args["path"]).as_uri()
        return result


class _EchoTool(BaseTool):
    """DIRECT tool: returns its bracket body (the ``content`` arg) back.

    The bracket tool format maps the text between ``[/echo]`` and
    ``[echo/]`` onto the ``content`` argument.
    """

    def __init__(self):
        super().__init__()
        self.calls = 0

    @property
    def tool_name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echo the call body back."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args, **kwargs):
        self.calls += 1
        return ToolResult(output=f"echoed:{args.get('content', '')}")


class _SlowBackgroundTool(BaseTool):
    """BACKGROUND tool: yields after a short sleep so the controller
    must promote it and pick the result up on a later turn."""

    def __init__(self, *, gated=False, output=None, error=None):
        super().__init__()
        self.calls = 0
        self.output = output
        self.error = error
        self.gated = gated
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    @property
    def tool_name(self) -> str:
        return "slowbg"

    @property
    def description(self) -> str:
        return "A background tool that completes after a short delay."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.BACKGROUND

    async def _execute(self, args, **kwargs):
        self.calls += 1
        self.started.set()
        if self.gated:
            await self.release.wait()
        else:
            await asyncio.sleep(0.05)
        return ToolResult(
            output=(
                "background-finished"
                if self.output is None
                else copy.deepcopy(self.output)
            ),
            error=self.error,
            exit_code=2 if self.error else 0,
        )


class _FailingTool(BaseTool):
    """DIRECT tool that always returns an error result.

    Drives the executor's error-result path and the
    ``_emit_direct_completion_activity`` / ``_format_text_results``
    error branches — distinct from a tool that *raises*.
    """

    @property
    def tool_name(self) -> str:
        return "fail"

    @property
    def description(self) -> str:
        return "Always fails with an error result."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args, **kwargs):
        return ToolResult(error="deliberate failure", exit_code=2)


class _RaisingTool(BaseTool):
    """DIRECT tool whose ``_execute`` raises — exercises the executor's
    generic-exception arm (distinct from an error *result*)."""

    @property
    def tool_name(self) -> str:
        return "boom"

    @property
    def description(self) -> str:
        return "Raises a RuntimeError when executed."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args, **kwargs):
        raise RuntimeError("kaboom")


class _MultimodalTool(BaseTool):
    """DIRECT tool returning a multimodal ToolResult (text + image).

    Drives ``tool_output.normalize_tool_output`` down its
    list-of-parts branch: the image part has no artifact store here so
    it is rendered as a safe text placeholder (raw base64 never reaches
    model context) and ``render_content_text`` joins the parts.
    """

    @property
    def tool_name(self) -> str:
        return "snap"

    @property
    def description(self) -> str:
        return "Returns a multimodal result: a caption plus an image."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args, **kwargs):
        return ToolResult(
            output=[
                TextPart(text="caption-for-the-snapshot"),
                ImagePart(url="data:image/png;base64,aGVsbG8td29ybGQ=", detail="low"),
            ]
        )


class _VersionedMediaTool(BaseTool):
    """Return ordered media blocks under one tool and source name."""

    def __init__(self):
        super().__init__()
        self.calls = 0

    @property
    def tool_name(self):
        return "versioned_media"

    @property
    def description(self):
        return "Generate a caption, image, and file for a supplied version."

    @property
    def execution_mode(self):
        return ExecutionMode.DIRECT

    async def _execute(self, args, **kwargs):
        self.calls += 1
        version = args["version"]
        encoded = base64.b64encode(f"IMAGE_{version}".encode()).decode()
        return ToolResult(
            output=[
                TextPart(text=f"caption_{version}"),
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{encoded}",
                        "detail": "high",
                        "provider_token": f"token_{version}",
                    },
                    "meta": {
                        "source_name": "same.png",
                        "source_type": "generated",
                        "vendor": {"version": version},
                    },
                    "extension": {"payload": [version]},
                },
                (
                    {
                        "type": "file",
                        "file": {
                            "name": "same.bin",
                            "mime": "application/octet-stream",
                            "data_base64": base64.b64encode(b"\x00\xffFILE_B").decode(),
                            "encoding": "base64",
                            "is_inline": True,
                            "vendor": {"version": version},
                        },
                        "extension": {"source": ["tool"]},
                    }
                    if version == "B"
                    else FilePart(
                        name="same.txt", content=f"FILE_{version}", is_inline=True
                    )
                ),
            ]
        )


class _HugeOutputTool(BaseTool):
    """DIRECT tool returning text far over its ``max_output`` byte cap.

    Drives ``tool_output.truncate_text_utf8`` — the executor reads
    ``tool.config.max_output`` and the normalised output carries the
    truncation note + ``truncated`` metadata.
    """

    @property
    def tool_name(self) -> str:
        return "huge"

    @property
    def description(self) -> str:
        return "Returns a very large text blob."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args, **kwargs):
        return ToolResult(output="A" * 5000)


class _BlockingTool(BaseTool):
    """DIRECT tool that blocks forever — used to test interrupt()."""

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()

    @property
    def tool_name(self) -> str:
        return "block"

    @property
    def description(self) -> str:
        return "Blocks until cancelled."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args, **kwargs):
        self.started.set()
        await asyncio.sleep(3600)
        return ToolResult(output="never")


class _BackgroundRaisingTool(BaseTool):
    """BACKGROUND tool whose ``_execute`` raises after a short delay.

    Drives the executor's ``_run_tool`` background-exception arm: the
    raised error is caught, recorded as an ERROR job, and the
    ``_on_complete`` callback fires a ``tool_complete`` event carrying
    the error string.
    """

    @property
    def tool_name(self) -> str:
        return "bgboom"

    @property
    def description(self) -> str:
        return "A background tool that raises after a short delay."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.BACKGROUND

    async def _execute(self, args, **kwargs):
        await asyncio.sleep(0.03)
        raise RuntimeError("background-kaboom")


class _RewriteArgsPlugin(BasePlugin):
    """Real plugin: rewrites a tool call's args via ``pre_tool_dispatch``.

    The canonical cross-cutting-concern pattern — a plugin sits between
    the parser and the executor and mutates the ``ToolCallEvent``. Here
    it appends a marker to the ``echo`` tool's ``content`` arg so the
    test can observe the rewrite landed in the executed tool's output.
    """

    name = "rewrite-args"
    priority = 10

    async def pre_tool_dispatch(self, call, context):
        if call.name != "echo":
            return None
        new_args = dict(call.args)
        new_args["content"] = f"{new_args.get('content', '')}-rewritten"
        return ToolCallEvent(name=call.name, args=new_args, raw=call.raw)


class _VetoPlugin(BasePlugin):
    """Real plugin: vetoes a named tool via ``PluginBlockError``."""

    name = "veto"
    priority = 20

    def __init__(self, blocked_tool: str) -> None:
        super().__init__()
        self._blocked = blocked_tool

    async def pre_tool_dispatch(self, call, context):
        if call.name == self._blocked:
            raise PluginBlockError(f"{call.name} is not allowed here")
        return None


class _RewriteResponsePlugin(BasePlugin):
    """Real plugin: rewrites the assistant text via ``post_llm_call``.

    Drives ``controller_plugins.run_post_llm_call_chain`` — when a
    plugin returns a changed string, the controller mutates the stored
    assistant message in place and emits an ``assistant_message_edited``
    activity. Here it appends a fixed suffix so the mutation is
    observable in the conversation.
    """

    name = "rewrite-response"
    priority = 30

    async def post_llm_call(self, messages, response_text, usage, **kwargs):
        if not response_text:
            return None
        return f"{response_text} [reviewed]"


class _SubagentGatePlugin(BasePlugin):
    """Real plugin: rewrites a sub-agent task via ``pre_subagent_run``,
    and vetoes one named sub-agent entirely.

    Exercises ``run_pre_subagent_dispatch`` — both the task-rewrite arm
    (returns a new task string) and the ``PluginBlockError`` veto arm
    that synthesises a blocked sub-agent result back to the controller.
    """

    name = "subagent-gate"
    priority = 15

    def __init__(self, blocked_name: str) -> None:
        super().__init__()
        self._blocked = blocked_name

    async def pre_subagent_run(self, task, **kwargs):
        if kwargs.get("name") == self._blocked:
            raise PluginBlockError(f"{self._blocked} is forbidden")
        return f"{task} [gated]"


class _OneShotTrigger(BaseTrigger):
    """A real BaseTrigger that fires exactly one event then idles.

    Drives the unified ``TriggerEvent`` model end-to-end through the
    real ``TriggerManager._run_loop`` — the same path ``TimerTrigger``
    and ``ChannelTrigger`` take. Exposes ``fired`` so the test can wait
    for the trigger loop to actually emit before asserting.
    """

    def __init__(self, prompt: str) -> None:
        super().__init__(prompt=prompt)
        self._gate = asyncio.Event()
        self._done = False
        self.fired = asyncio.Event()

    async def wait_for_trigger(self) -> TriggerEvent | None:
        if self._done or not self._running:
            # Block until stopped so the run loop doesn't busy-spin.
            await self._gate.wait()
            return None
        self._done = True
        self.fired.set()
        return self._create_event(
            EventType.TIMER,
            content=self.prompt,
            context={"trigger": "oneshot"},
        )

    async def _on_stop(self) -> None:
        self._gate.set()


# ---------------------------------------------------------------------------
# Fixtures — mirror bootstrap/agent_init + terrarium/creature_host.
# ---------------------------------------------------------------------------


@pytest.fixture
def scripted_llm(monkeypatch):
    """Patch BOTH ``create_llm_provider`` import sites to a ScriptedLLM.

    ``bootstrap.agent_init`` imports the symbol directly and
    ``bootstrap.llm`` defines it — patching only one leaves a real
    provider on the other path. The closure lets a test set its script
    before it builds the agent.
    """

    holder: dict[str, list] = {"script": ["OK"]}

    def _fake_create(config, llm=None):
        return ScriptedLLM(holder["script"])

    monkeypatch.setattr(_bootstrap_llm_mod, "create_llm_provider", _fake_create)
    monkeypatch.setattr(_agent_init_mod, "create_llm_provider", _fake_create)
    return holder


@pytest.fixture
def make_creature(scripted_llm, tmp_path):
    """Build a real ``Agent`` and wrap it in a ``Creature``.

    This is the ``terrarium/creature_host.build_creature`` path with an
    in-memory ``AgentConfig`` — the same construction the engine uses
    for a solo ``kt run`` creature. Returns ``(creature, recorder)``;
    the recorder is swapped onto the router's default output for
    behaviour assertions.
    """

    def _build(
        *,
        script=None,
        system_prompt="You are a test agent.",
        tools=None,
        ephemeral=False,
        max_iterations=None,
        termination=None,
        compact=None,
        creature_id="solo",
    ):
        if script is not None:
            scripted_llm["script"] = script
        cfg = AgentConfig(
            name=creature_id,
            llm_profile="openai/gpt-4-test",
            model="gpt-4",
            provider="openai",
            api_key_env="",
            system_prompt=system_prompt,
            include_tools_in_prompt=True,
            include_hints_in_prompt=False,
            tool_format="bracket",
            agent_path=tmp_path,
            input=InputConfig(type="none"),
            output=OutputConfig(type="stdout"),
            tools=tools or [],
            ephemeral=ephemeral,
            max_iterations=max_iterations,
            termination=termination,
            compact=compact,
        )
        agent = Agent(cfg)
        recorder = OutputRecorder()
        agent.output_router.default_output = recorder
        creature = Creature(creature_id=creature_id, name=creature_id, agent=agent)
        return creature, recorder

    return _build


async def _drain_chat(creature: Creature, message: str) -> str:
    """``Creature.chat`` consumed to completion — the canonical drive."""
    chunks: list[str] = []
    async for chunk in creature.chat(message):
        chunks.append(chunk)
    return "".join(chunks)


async def _settle(predicate: Callable[[], bool], budget: float = 30.0) -> None:
    """Poll ``predicate`` until true; ``budget`` caps a hang, never a speed."""
    deadline = time.monotonic() + budget
    while not predicate() and time.monotonic() < deadline:
        await asyncio.sleep(0.02)


def _assistant_text(agent: Agent) -> str:
    last = agent.controller.conversation.get_last_assistant_message()
    assert last is not None, "expected an assistant message in conversation"
    return last.get_text_content()


async def _exercise_user_attachment_history(tmp_path):
    """Replay, edit, and resume user multimodal attachments across branches."""
    media_path = tmp_path / "multimodal.kohakutr"
    media_llm = ScriptedLLM(["original", "regen", "edited", "A next", "B next"])
    original_parts = [
        TextPart(text="inspect attachments"),
        ImagePart(url="data:image/png;base64,QUJD", source_name="pic.png"),
        FilePart(name="same.txt", content="FILE_A", mime="text/plain", is_inline=True),
        TextPart(text="between files"),
        FilePart(name="same.txt", content="FILE_B", mime="text/plain", is_inline=True),
        TextPart(text="after files"),
    ]
    raw_original = [part.to_dict() for part in original_parts]
    replacement = [part.to_dict() for part in original_parts]
    replacement[4]["file"]["content"] = "FILE_C"

    def assert_media_request(provider, expected_second):
        content = next(
            m["content"]
            for m in provider.last_messages
            if m["role"] == "user" and isinstance(m["content"], list)
        )
        assert [p["type"] for p in content] == [
            "text",
            "image_url",
            "text",
            "text",
            "text",
            "text",
            "text",
            "text",
        ]
        assert content[0]["text"] == "inspect attachments"
        assert content[1]["image_url"] == raw_original[1]["image_url"]
        assert "FILE_A" in content[3]["text"]
        assert content[4]["text"] == "between files"
        assert expected_second in content[6]["text"]
        assert content[7]["text"] == "after files"
        paths = [Path(content[idx]["text"].split("\nPath: ", 1)[1]) for idx in (2, 5)]
        assert paths[0] != paths[1]
        assert [path.read_text() for path in paths] == ["FILE_A", expected_second]
        return paths

    async with Terrarium() as media_engine:
        media_creature = await media_engine.add_creature(
            AgentConfig(
                name="mediahistory", agent_path=tmp_path, system_prompt="plain"
            ),
            llm=media_llm,
            io="headless",
            session=media_path,
            start=True,
        )
        media_agent = media_creature.agent
        media_agent.controller.config.include_job_status = False
        await media_agent.inject_input(original_parts)
        first_paths = assert_media_request(media_llm, "FILE_B")
        first_paths[0].write_bytes(b"CHANGED_DERIVED_FILE")
        await media_agent.regenerate_last_response(turn_index=1)
        rerun_paths = assert_media_request(media_llm, "FILE_B")
        assert rerun_paths[0] != first_paths[0]
        assert first_paths[0].read_bytes() == b"CHANGED_DERIVED_FILE"
        original_view = dict(live_branch_path(media_agent))
        await media_agent.edit_and_rerun(
            -1, replacement, turn_index=1, branch_view=original_view
        )
        edited_view = dict(live_branch_path(media_agent))
        assert edited_view != original_view
        assert_media_request(media_llm, "FILE_C")
        await media_agent.inject_input("continue original", branch_view=original_view)
        assert_media_request(media_llm, "FILE_B")
        await media_agent.inject_input("continue edited", branch_view=edited_view)
        assert_media_request(media_llm, "FILE_C")
        saved_events = media_agent.session_store.get_events("mediahistory")
        assert [
            e["content"]
            for e in saved_events
            if e["type"] == "user_message" and e["turn_index"] == 1
        ] == [
            raw_original,
            raw_original,
            replacement,
        ]
        saved_media_view = selected_branch_view(media_agent)
        saved_media_path = dict(live_branch_path(media_agent))
        await media_engine.remove_creature(media_creature)
        media_agent.session_store.close()

    cold_llm = ScriptedLLM(["cold continuation", "cold original"])
    cold_agent, cold_store = resume_agent(media_path, io_mode="plain", llm=cold_llm)
    try:
        assert selected_branch_view(cold_agent) == saved_media_view
        assert live_branch_path(cold_agent) == saved_media_path
        first_user = next(
            m
            for m in cold_agent.controller.conversation.get_messages()
            if m.role == "user"
        )
        assert (
            first_user.get_text_content()
            == "inspect attachments\nbetween files\nafter files"
        )
        assert first_user.has_images()
        assert [type(p) for p in first_user.content] == [
            TextPart,
            ImagePart,
            FilePart,
            TextPart,
            FilePart,
            TextPart,
        ]
        assert first_user.content[4].content == "FILE_C"
        await cold_agent.start()
        cold_agent.controller.config.include_job_status = False
        await cold_agent.inject_input("after cold restore")
        assert_media_request(cold_llm, "FILE_C")
        await cold_agent.inject_input("back to original", branch_view=original_view)
        assert_media_request(cold_llm, "FILE_B")
        assert (
            cold_store.get_events("mediahistory")[: len(saved_events)] == saved_events
        )
        assert [part.to_dict() for part in original_parts] == raw_original
    finally:
        await cold_agent.stop()
        cold_store.close()


async def _exercise_direct_media_history(tmp_path, monkeypatch):
    """Keep generated tool media stable across regenerate, branch, and resume."""
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "unrelated-default-sessions"))
    result_tool = _VersionedMediaTool()
    result_llm = _HistoryReplayLLM(
        True,
        plan=[
            [NativeToolCall("media_A", "versioned_media", '{"version":"A"}')],
            "answer_A",
            [NativeToolCall("media_B", "versioned_media", '{"version":"B"}')],
            "answer_B",
            "continued_B",
            "continued_A",
        ],
    )

    def assert_result_prefix(messages, version, call_id, prepared=False):
        calls = [call for message in messages for call in message.get("tool_calls", [])]
        assert [
            (call["id"], call["function"]["name"], call["function"]["arguments"])
            for call in calls
        ] == [(call_id, "versioned_media", f'{{"version":"{version}"}}')]
        results = [message for message in messages if message["role"] == "tool"]
        assert len(results) == 1
        result = results[0]
        assert result["tool_call_id"] == call_id
        assert result["name"] == "versioned_media"
        parts = result["content"]
        assert parts[0]["text"] == f"caption_{version}"
        assert parts[1]["image_url"]["detail"] == "high"
        assert parts[1]["meta"]["source_name"] == "same.png"
        assert parts[1]["meta"]["vendor"] == {"version": version}
        assert parts[1]["image_url"]["provider_token"] == f"token_{version}"
        assert parts[1]["extension"] == {"payload": [version]}
        if prepared:
            assert [part["type"] for part in parts] == (
                ["text", "image_url", "text"]
                if version == "B"
                else ["text", "image_url", "text", "text"]
            )
            assert (
                base64.b64decode(
                    resolve_artifact_url(parts[1]["image_url"]["url"]).split(",", 1)[1]
                )
                == f"IMAGE_{version}".encode()
            )
            file_markers = [
                part["text"]
                for part in parts[2:]
                if part.get("text", "").startswith("Attached file: same.txt\nPath: ")
            ]
            if version == "B":
                assert file_markers == []
                assert parts[2]["text"].startswith("[File read failed: same.bin:")
                assert "binary" in parts[2]["text"].lower()
                assert base64.b64encode(b"\x00\xffFILE_B").decode() not in str(parts)
            else:
                assert len(file_markers) == 1, parts
                path = Path(file_markers[0].split("Path: ", 1)[1])
                assert path.read_bytes() == f"FILE_{version}".encode()
        else:
            assert [part["type"] for part in parts] == ["text", "image_url", "file"]
            if version == "B":
                assert parts[2]["file"]["name"] == "same.bin"
                assert parts[2]["file"]["mime"] == "application/octet-stream"
                assert parts[2]["file"]["encoding"] == "base64"
                assert parts[2]["file"]["is_inline"] is True
                assert parts[2]["file"]["vendor"] == {"version": "B"}
                assert parts[2]["extension"] == {"source": ["tool"]}
                assert (
                    base64.b64decode(parts[2]["file"]["data_base64"])
                    == b"\x00\xffFILE_B"
                )
            else:
                assert parts[2]["file"]["content"] == f"FILE_{version}"
        return parts

    def assert_result_state(agent, llm, version, call_id, expected_parts=None):
        assert_result_prefix(llm.last_messages, version, call_id, prepared=True)
        parts = assert_result_prefix(
            agent.controller.conversation.to_messages(), version, call_id
        )
        if expected_parts is not None:
            assert parts == expected_parts
        if version == "B":
            assert any(
                Path(path).read_bytes() == b"\x00\xffFILE_B"
                for path in agent.controller._inline_file_paths
            )
        return copy.deepcopy(parts)

    async with Terrarium(session_dir=tmp_path / "result-media") as engine:
        result_creature = await engine.add_creature(
            AgentConfig(
                name="resultmedia", system_prompt="plain", tool_format="native"
            ),
            llm=result_llm,
            tools=[result_tool],
            io="headless",
            start=True,
        )
        result_agent = result_creature.agent
        result_agent.controller.config.include_job_status = False
        await result_creature.run("generate media")
        first_parts = assert_result_state(result_agent, result_llm, "A", "media_A")
        first_artifact = (
            result_agent.session_store.artifacts_dir
            / first_parts[1]["image_url"]["url"].split("/artifacts/", 1)[1]
        )
        assert first_artifact.read_bytes() == b"IMAGE_A"
        result_original = dict(live_branch_path(result_agent))
        await result_agent.regenerate_last_response(turn_index=1)
        second_parts = assert_result_state(result_agent, result_llm, "B", "media_B")
        second_artifact = (
            result_agent.session_store.artifacts_dir
            / second_parts[1]["image_url"]["url"].split("/artifacts/", 1)[1]
        )
        assert first_artifact != second_artifact
        assert first_artifact.read_bytes() == b"IMAGE_A"
        assert second_artifact.read_bytes() == b"IMAGE_B"
        result_sibling = dict(live_branch_path(result_agent))
        assert result_tool.calls == 2
        await result_agent.inject_input("continue sibling", branch_view=result_sibling)
        assert_result_state(result_agent, result_llm, "B", "media_B", second_parts)
        await result_agent.inject_input(
            "continue original", branch_view=result_original
        )
        assert_result_state(result_agent, result_llm, "A", "media_A", first_parts)
        assert result_tool.calls == 2
        result_path = result_agent.session_store.path
        saved_results = copy.deepcopy(
            result_agent.session_store.get_events("resultmedia")
        )

    resumed_llm = ScriptedLLM(["cold sibling", "cold original"])
    resumed, resumed_store = resume_agent(result_path, io_mode="plain", llm=resumed_llm)
    replay_tool = _VersionedMediaTool()
    resumed.add_tool(replay_tool)
    try:
        resumed.controller.config.include_job_status = False
        await resumed.start()
        await resumed.inject_input("cold sibling", branch_view=result_sibling)
        assert_result_state(resumed, resumed_llm, "B", "media_B", second_parts)
        await resumed.inject_input("cold original", branch_view=result_original)
        assert_result_state(resumed, resumed_llm, "A", "media_A", first_parts)
        assert replay_tool.calls == 0
        assert first_artifact.read_bytes() == b"IMAGE_A"
        assert second_artifact.read_bytes() == b"IMAGE_B"
        assert (
            resumed_store.get_events("resultmedia")[: len(saved_results)]
            == saved_results
        )
    finally:
        await resumed.stop()
        resumed_store.close()

    for persistent in (False, True):
        image_path = tmp_path / "snapshot.png"
        read_args = json.dumps({"path": str(image_path)})
        llm = _HistoryReplayLLM(
            True,
            plan=[
                [NativeToolCall("read_first", "read", read_args)],
                "first snapshot read",
                [NativeToolCall("read_updated", "read", read_args)],
                "updated snapshot read",
                "continued after deletion",
            ],
        )
        expected_urls = []
        async with Terrarium(session_dir=tmp_path / "read-media") as engine:
            creature = await engine.add_creature(
                AgentConfig(
                    name="readmedia", system_prompt="plain", tool_format="native"
                ),
                llm=llm,
                tools=[ReadTool()],
                pwd=tmp_path,
                session=persistent,
                io="headless",
                start=True,
            )
            for color in ("red", "blue"):
                Image.new("RGB", (2, 2), color).save(image_path)
                expected_urls.append(
                    "data:image/png;base64,"
                    + base64.b64encode(image_path.read_bytes()).decode()
                )
                assert (await creature.run("read the image")).ok
                urls = [
                    part["image_url"]["url"]
                    for message in llm.last_messages
                    if isinstance(message.get("content"), list)
                    for part in message["content"]
                    if part.get("type") == "image_url"
                ]
                assert urls == expected_urls, str(llm.last_messages[-2:])
            image_path.unlink()
            assert (
                await creature.run("continue after deletion")
            ).text == "continued after deletion"
            if persistent:
                saved_path = creature.agent.session_store.path

        if persistent:
            restored_llm = ScriptedLLM(["continued after resume"])
            restored, store = resume_agent(
                saved_path, io_mode="plain", llm=restored_llm
            )
            try:
                await restored.start()
                assert (
                    await restored.run("continue with saved images")
                ).text == "continued after resume"
                urls = [
                    part["image_url"]["url"]
                    for message in restored_llm.last_messages
                    if isinstance(message.get("content"), list)
                    for part in message["content"]
                    if part.get("type") == "image_url"
                ]
                assert urls == expected_urls
            finally:
                await restored.stop()
                store.close()


async def _exercise_assistant_media_history(tmp_path):
    """Replay assistant-generated media, including post-LLM rewrite, across branches."""
    assistant_dir = tmp_path / "assistant-media"
    assistant_llm = _AssistantMediaLLM()

    def assert_assistant_media(agent, llm, version):
        messages = agent.controller.conversation.to_messages()
        media = [message for message in messages if message.get("provider_extension")]
        assert len(media) == 1
        expected = media[0]
        assert expected["provider_extension"] == {"state": [version]}
        content = expected["content"]
        assert [part["type"] for part in content] == [
            "text",
            "text",
            "image_url",
            "file",
            "text",
        ]
        assert [content[index]["text"] for index in (0, 1, 4)] == [
            f"streamed_{version}",
            f"structured_{version}",
            f"tail_{version}",
        ]
        image = content[2]
        assert image["image_url"]["provider_token"] == version
        assert image["meta"]["extension"] == {"version": version}
        assert (
            base64.b64decode(
                resolve_artifact_url(
                    image["image_url"]["url"], session_path=agent.session_store.path
                ).split(",", 1)[1]
            )
            == f"ASSISTANT_IMAGE_{version}".encode()
        )
        assert content[3]["file"]["content"] == f"ASSISTANT_FILE_{version}"
        sent = next(
            message
            for message in llm.last_messages
            if message.get("provider_extension")
        )
        assert sent["provider_extension"] == expected["provider_extension"]
        assert sent["content"][0:2] == content[0:2]
        expected_image = copy.deepcopy(image)
        expected_image["image_url"]["url"] = resolve_artifact_url(
            image["image_url"]["url"], session_path=agent.session_store.path
        )
        assert sent["content"][2] == expected_image
        assert sent["content"][-1] == content[-1]
        file_marker = next(
            part["text"]
            for part in sent["content"]
            if part.get("text", "").startswith("Attached file: same.txt\nPath: ")
        )
        assert (
            Path(file_marker.split("Path: ", 1)[1]).read_bytes()
            == f"ASSISTANT_FILE_{version}".encode()
        )
        return copy.deepcopy(expected)

    async with Terrarium(session_dir=assistant_dir) as engine:
        creature = await engine.add_creature(
            AgentConfig(name="assistantmedia", system_prompt="plain"),
            llm=assistant_llm,
            io="headless",
            start=True,
        )
        assistant_agent = creature.agent
        assistant_agent.controller.config.include_job_status = False
        await creature.run("generate")
        assistant_original = dict(live_branch_path(assistant_agent))
        await assistant_agent.regenerate_last_response(turn_index=1)
        assistant_sibling = dict(live_branch_path(assistant_agent))
        await assistant_agent.inject_input(
            "continue sibling", branch_view=assistant_sibling
        )
        sibling_message = assert_assistant_media(assistant_agent, assistant_llm, "B")
        await assistant_agent.inject_input(
            "continue original", branch_view=assistant_original
        )
        original_message = assert_assistant_media(assistant_agent, assistant_llm, "A")
        assert assistant_llm.generated == 2
        assert (
            original_message["content"][2]["image_url"]["url"]
            != sibling_message["content"][2]["image_url"]["url"]
        )
        assistant_path = assistant_agent.session_store.path
        assistant_events = copy.deepcopy(
            assistant_agent.session_store.get_events("assistantmedia")
        )
        raw_original = branch_history.replay_with_settled_tools(
            assistant_events, branch_view=assistant_original
        )
        assert (
            next(
                message for message in raw_original if message.get("provider_extension")
            )
            == original_message
        )
        raw_sibling = branch_history.replay_with_settled_tools(
            assistant_events, branch_view=assistant_sibling
        )
        assert (
            next(
                message for message in raw_sibling if message.get("provider_extension")
            )
            == sibling_message
        )

    cold_llm = ScriptedLLM(["cold sibling", "cold original"])
    assistant_cold, assistant_store = resume_agent(
        assistant_path, io_mode="plain", llm=cold_llm
    )
    try:
        assistant_cold.controller.config.include_job_status = False
        await assistant_cold.start()
        await assistant_cold.inject_input("cold sibling", branch_view=assistant_sibling)
        assert assert_assistant_media(assistant_cold, cold_llm, "B") == sibling_message
        await assistant_cold.inject_input(
            "cold original", branch_view=assistant_original
        )
        assert assert_assistant_media(assistant_cold, cold_llm, "A") == original_message
        assert (
            assistant_store.get_events("assistantmedia")[: len(assistant_events)]
            == assistant_events
        )
        assert assistant_llm.generated == 2
    finally:
        await assistant_cold.stop()
        assistant_store.close()

    rewritten_path = tmp_path / "assistant-posthook.kohakutr"
    post_llm = _AssistantMediaLLM()
    async with Terrarium() as engine:
        rewritten = await engine.add_creature(
            AgentConfig(name="rewrittenmedia", system_prompt="plain"),
            llm=post_llm,
            io="headless",
            session=rewritten_path,
            start=True,
            plugins=[_RewriteResponsePlugin()],
        )
        rewritten.agent.controller.config.include_job_status = False
        await rewritten.run("generate and rewrite")
        final = (
            rewritten.agent.controller.conversation.get_last_assistant_message().to_dict()
        )
        assert final["content"] == "streamed_A\nstructured_A\ntail_A [reviewed]"
        rewritten_events = rewritten.agent.session_store.get_events("rewrittenmedia")
        assert branch_history.replay_with_settled_tools(rewritten_events)[-1] == final
        canonical = next(
            event for event in rewritten_events if event["type"] == "assistant_message"
        )
        assert canonical["message"] == final
    post_cold_llm = ScriptedLLM(["confirmed"])
    post_cold, post_store = resume_agent(
        rewritten_path, io_mode="plain", llm=post_cold_llm
    )
    try:
        await post_cold.start()
        await post_cold.inject_input("confirm rewritten history")
        assert (
            next(
                message
                for message in post_cold_llm.last_messages
                if message.get("provider_extension")
            )
            == final
        )
    finally:
        await post_cold.stop()
        post_store.close()


async def _exercise_settled_media_history(tmp_path):
    """Retain settled background-tool media in later-turn mutation prefixes."""
    for notify in (True, False):
        settled_dir = tmp_path / f"settled-media-{notify}"
        background_payload = [
            {
                "type": "text",
                "text": "settled caption",
                "extension": {"tag": "caption"},
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/png;base64,U0VUVExFRF9JTUFHRQ==",
                    "detail": "high",
                    "provider_token": "settled",
                },
                "meta": {
                    "source_name": "same.png",
                    "source_type": "generated",
                    "vendor": {"id": "settled"},
                },
            },
            {
                "type": "file",
                "file": {
                    "name": "same.txt",
                    "content": "SETTLED_FILE",
                    "is_inline": True,
                },
                "extension": {"tag": "file"},
            },
            {"type": "text", "text": "settled tail"},
        ]
        error = "diagnostic failure" if notify else None
        background = _SlowBackgroundTool(
            gated=True, output=background_payload, error=error
        )
        background.config.notify_controller_on_background_complete = notify
        media_llm = _HistoryReplayLLM(
            True,
            plan=[
                [NativeToolCall("bg_media", "slowbg", "{}")],
                "launched",
                "later",
                *(["completion feedback"] if notify else []),
                "sibling",
                "original continued",
                "sibling continued",
                "excluded call",
                "excluded continued",
            ],
        )

        def assert_settled_media(agent, llm, expected, excluded=False):
            calls = [
                call
                for message in llm.last_messages
                for call in message.get("tool_calls") or []
                if call["id"] == "bg_media"
            ]
            prepared = [
                message
                for message in llm.last_messages
                if message.get("tool_call_id") == "bg_media"
            ]
            stored = [
                message
                for message in agent.controller.conversation.to_messages()
                if message.get("tool_call_id") == "bg_media"
            ]
            if excluded:
                assert calls == prepared == stored == []
                assert "SETTLED_FILE" not in str(llm.last_messages)
                assert "settled caption" not in str(llm.last_messages)
                return
            assert [
                (
                    call["id"],
                    call["function"]["name"],
                    call["function"]["arguments"],
                )
                for call in calls
            ] == [("bg_media", "slowbg", "{}")]
            assert len(stored) == len(prepared) == 1
            assert stored[0]["name"] == prepared[0]["name"] == "slowbg"
            assert stored[0]["content"] == expected
            parts = prepared[0]["content"]
            assert [part["type"] for part in parts] == [
                "text",
                "image_url",
                "text",
                "text",
                "text",
            ]
            assert parts[0] == expected[0]
            expected_image = copy.deepcopy(expected[1])
            expected_image["image_url"]["url"] = resolve_artifact_url(
                expected[1]["image_url"]["url"], session_path=agent.session_store.path
            )
            assert parts[1] == expected_image
            assert parts[-1] == expected[-1]
            assert (
                base64.b64decode(
                    resolve_artifact_url(parts[1]["image_url"]["url"]).split(",", 1)[1]
                )
                == b"SETTLED_IMAGE"
            )
            marker = next(
                part["text"]
                for part in parts
                if part.get("text", "").startswith("Attached file: same.txt\nPath: ")
            )
            assert Path(marker.split("Path: ", 1)[1]).read_bytes() == b"SETTLED_FILE"

        async with Terrarium(session_dir=settled_dir) as engine:
            node = await engine.add_creature(
                AgentConfig(
                    name="settledmedia", system_prompt="plain", tool_format="native"
                ),
                llm=media_llm,
                tools=[background],
                io="headless",
                start=True,
            )
            media_agent = node.agent
            media_agent.controller.config.include_job_status = False
            await node.run("launch media", timeout=8)
            await asyncio.wait_for(background.started.wait(), 2)
            job = media_agent.executor.get_running_jobs()[0]
            task = media_agent.executor._tasks[job.job_id]
            if notify:
                await node.run("later turn", timeout=8)
                async with node.attach() as stream:
                    background.release.set()
                    while True:
                        activity = await asyncio.wait_for(anext(stream), 8)
                        if (
                            isinstance(activity, Activity)
                            and activity.kind == "processing_end"
                        ):
                            break
                completion = media_agent._turn_completion
                if completion is not None:
                    await asyncio.wait_for(asyncio.shield(completion), 8)
            else:
                background.release.set()
                await asyncio.wait_for(task, 8)
                await node.run("later turn", timeout=8)
            await asyncio.wait_for(task, 8)
            await media_agent._session_output.drain()
            assert media_agent.executor.get_running_jobs() == []
            assert not media_agent.is_processing
            assert background.calls == 1
            raw_results = media_agent.session_store.get_events("settledmedia")
            result_event = next(
                event
                for event in raw_results
                if event["type"] == "tool_result" and event.get("call_id") == job.job_id
            )
            assert result_event["exit_code"] == (2 if error else 0)
            if error:
                assert result_event["error"] == error
                assert result_event["final_state"] == "error"
                assert result_event["interrupted"] is False
                assert result_event["cancelled"] is False
            saved_parts = copy.deepcopy(result_event["output"])
            assert [part["type"] for part in saved_parts] == [
                "text",
                "image_url",
                "file",
                "text",
            ]
            assert saved_parts[0] == background_payload[0]
            assert saved_parts[2:] == background_payload[2:]
            assert saved_parts[1]["meta"]["vendor"] == {"id": "settled"}
            assert saved_parts[1]["image_url"]["provider_token"] == "settled"
            original_view = dict(live_branch_path(media_agent))
            if notify:
                await media_agent.regenerate_last_response(turn_index=2)
            else:
                await media_agent.regenerate_last_response()
            sibling_view = dict(live_branch_path(media_agent))
            assert_settled_media(media_agent, media_llm, saved_parts)
            await media_agent.inject_input(
                "original continuation", branch_view=original_view
            )
            assert_settled_media(media_agent, media_llm, saved_parts)
            await media_agent.inject_input(
                "sibling continuation", branch_view=sibling_view
            )
            assert_settled_media(media_agent, media_llm, saved_parts)
            await media_agent.regenerate_last_response(turn_index=1)
            excluded_view = dict(live_branch_path(media_agent))
            await media_agent.inject_input(
                "exclude continuation", branch_view=excluded_view
            )
            assert_settled_media(media_agent, media_llm, saved_parts, excluded=True)
            assert background.calls == 1
            background_path = media_agent.session_store.path
            settled_events = copy.deepcopy(
                media_agent.session_store.get_events("settledmedia")
            )

        cold_bg_llm = ScriptedLLM(["cold original", "cold sibling", "cold excluded"])
        restored_bg, bg_store = resume_agent(
            background_path, io_mode="plain", llm=cold_bg_llm
        )
        never_rerun = _SlowBackgroundTool(output=background_payload)
        restored_bg.add_tool(never_rerun)
        try:
            restored_bg.controller.config.include_job_status = False
            await restored_bg.start()
            for label, view, excluded in [
                ("original", original_view, False),
                ("sibling", sibling_view, False),
                ("excluded", excluded_view, True),
            ]:
                await restored_bg.inject_input(f"cold {label}", branch_view=view)
                assert_settled_media(
                    restored_bg, cold_bg_llm, saved_parts, excluded=excluded
                )
            assert never_rerun.calls == 0
            assert background.calls == 1
            assert (
                bg_store.get_events("settledmedia")[: len(settled_events)]
                == settled_events
            )
        finally:
            await restored_bg.stop()
            bg_store.close()


class TestCoreIntegration:
    """Each method runs one complete ``core/`` feature workflow."""

    async def test_full_turn_cycle_with_direct_and_background_tools(
        self, make_creature, tmp_path
    ):
        """Engine-hosted creature: input -> controller loop -> DIRECT tool
        dispatch + result feedback -> a second turn dispatches a
        BACKGROUND tool that completes and routes its result back via
        the unified TriggerEvent model -> output streamed to chat.

        Mirrors: ``creature_host.Creature.chat`` driving a real engine
        creature, the way every HTTP/WS chat endpoint does.
        """
        creature, recorder = make_creature(
            script=[
                # Turn 1, round 1: call the direct echo tool.
                ScriptEntry("[/echo]ping[echo/]", match="hello"),
                # Turn 1, round 2: tool result fed back, wrap up.
                ScriptEntry("direct done", match="echoed:ping"),
                # Turn 2: dispatch the BACKGROUND tool. A background tool
                # is promoted immediately (not awaited) so the turn ends
                # right after this single round; the surrounding text
                # streams out as the turn's reply.
                ScriptEntry("kicked off bg work [/slowbg][slowbg/]", match="second"),
                # The background completion arrives later as its own
                # tool_complete TriggerEvent, driving a fresh turn.
                ScriptEntry("background acknowledged", match="background-finished"),
            ]
        )
        agent = creature.agent
        (tmp_path / "example.py").write_text("pass\n")
        agent.executor._working_dir = tmp_path
        agent.skills.add(
            Skill("inspect", "Inspect Python", "Instructions", paths=["*.py"])
        )
        echo, slowbg = _EchoTool(), _SlowBackgroundTool()
        fail_tool, boom_tool = _FailingTool(), _RaisingTool()
        snap_tool = _MultimodalTool()
        # A tiny 100-byte cap so the 5000-char blob is truncated.
        huge_tool = _HugeOutputTool(ToolConfig(max_output=100))
        for tool in (echo, slowbg, fail_tool, boom_tool, snap_tool, huge_tool):
            agent.registry.register_tool(tool)
            agent.executor.register_tool(tool)
        # Register a real plugin whose ``pre_tool_dispatch`` rewrites the
        # echo call's args — the cross-cutting-concern extension point.
        agent.plugins.register(_RewriteArgsPlugin())

        async with Terrarium() as engine:
            added = await engine.add_creature(creature)
            # The engine placed the creature in a fresh singleton graph
            # and started it.
            assert engine.get_creature("solo") is creature
            assert added.graph_id != ""
            assert creature.is_running is True

            # --- Turn 1: direct tool round-trip -------------------------
            out1 = await _drain_chat(creature, "hello, run echo")
            # The final assistant text of turn 1 streamed to the chat pipe.
            assert "direct done" in out1
            # The echo tool actually ran: its output is in the conversation.
            convo_text = " ".join(
                m.get_text_content()
                for m in agent.controller.conversation.get_messages()
            )
            assert "echoed:ping" in convo_text
            # The plugin's pre_tool_dispatch rewrite landed: the executed
            # tool saw the appended marker, so its output carries it.
            assert "echoed:ping-rewritten" in convo_text
            # The controller looped exactly twice for turn 1 (call, wrap-up).
            assert agent.llm.call_count == 2
            hint = agent.skill_path_scanner.format_hint([agent.skills.get("inspect")])
            assert [
                [m["content"] for m in call if m.get("content") == hint]
                for call in agent.llm.call_log
            ] == [[hint], [hint]]

            # --- Turn 2 + 3: background tool promote + completion -------
            # A BACKGROUND tool is promoted immediately, so turn 2 is a
            # single controller round: the surrounding text streams out
            # and the turn ends without awaiting the tool.
            out2 = await _drain_chat(creature, "second request please")
            assert "kicked off bg work" in out2

            # The background job completes on its own; the executor's
            # completion callback injects a ``tool_complete`` TriggerEvent
            # which drives exactly one more controller turn whose final
            # assistant text acknowledges the real background output.
            await _settle(lambda: "background acknowledged" in _assistant_text(agent))
            assert "background acknowledged" in _assistant_text(agent)
            assert agent.executor._event_queue.qsize() == 0
            assert all(
                sum(m.get("content") == hint for m in call) == 1
                for call in agent.llm.call_log[:4]
            )
            agent.skills.disable("inspect")
            bg_convo = " ".join(
                m.get_text_content()
                for m in agent.controller.conversation.get_messages()
            )
            # The real background output ("background-finished") reached
            # the conversation as a tool-result message.
            assert "background-finished" in bg_convo

            # Regression guard for B-fat2-core-1 (FIXED): a tool declared
            # ``ExecutionMode.BACKGROUND`` is submitted to the executor as
            # background, so the executor's own ``_on_complete`` callback
            # delivers the completion. The ``backgroundify`` handle must
            # NOT also fire ``_on_backgroundify_complete`` — that double
            # completion ran the controller one EXTRA turn. The fix passes
            # ``on_bg_complete=None`` to the handle whenever the executor
            # already delivers the completion. Expected: exactly 4
            # controller calls (turn1 ×2 + turn2 ×1 + one bg follow-up).
            await _settle(lambda: agent.llm.call_count >= 4)
            # Settle: on the unfixed (double-fire) code a 5th controller
            # call would be scheduled right here by the second completion.
            await asyncio.sleep(0.3)
            assert agent.llm.call_count == 4

            # Re-bind a fresh scripted LLM for the remaining turns so the
            # ``match``-keyed entries below resolve order-independently.
            rest_llm = ScriptedLLM(
                [
                    # Turn 4: a veto plugin blocks the echo tool.
                    ScriptEntry("[/echo]blocked attempt[echo/]", match="echo again"),
                    # Turn 4b: the synthesised block result rides the next
                    # input — the controller sees it and acknowledges.
                    ScriptEntry("noticed the block", match="is not allowed here"),
                    # Turn 5: TWO direct tools in one round — parallel exec.
                    ScriptEntry(
                        "[/echo]left[echo/] and [/echo]right[echo/]",
                        match="run two echoes",
                    ),
                    ScriptEntry("both echoes done", match="echoed:left"),
                    # Turn 6: an ERROR-result tool and a RAISING tool.
                    ScriptEntry(
                        "[/fail][fail/] then [/boom][boom/]",
                        match="trigger failures",
                    ),
                    ScriptEntry("saw the failures", match="deliberate failure"),
                    # Turn 7: a multimodal-output tool + a huge-output
                    # tool — exercises tool_output normalization.
                    ScriptEntry(
                        "[/snap][snap/] and [/huge][huge/]",
                        match="snapshot and blob",
                    ),
                    ScriptEntry("processed media", match="caption-for-the-snapshot"),
                    # Turn 8: framework commands — ##jobs## lists jobs,
                    # ##info## fetches a tool's docs, ##read_job## on a
                    # ghost id errors, ##info## on an unknown tool errors.
                    # All resolve inline in text mode via the controller's
                    # command handler; output is spliced into the
                    # assistant message within the SAME round.
                    ScriptEntry(
                        "checking [/jobs][jobs/] and [/info]echo[info/] and "
                        "[/read_job]ghost_job_id[read_job/] and "
                        "[/info]no_such_tool[info/]",
                        match="run the commands",
                    ),
                    # Turn 9: a custom output-handler check.
                    ScriptEntry("ephemeral reply", match="ephemeral check"),
                ]
            )
            agent.llm = rest_llm
            agent.controller.llm = rest_llm

            # --- Programmatic introspection API -------------------------
            # ``get_state`` is the monitoring snapshot the TUI/API poll.
            state = agent.get_state()
            assert state["name"] == "solo"
            assert state["running"] is True
            assert "echo" in state["tools"]
            assert "slowbg" in state["tools"]
            # message_count matches the live conversation length.
            assert state["message_count"] == len(
                agent.controller.conversation.get_messages()
            )
            # ``tools`` / ``conversation_history`` properties agree with
            # the registry and the controller's conversation. The engine
            # also registers its own channel tools, so the two we added
            # must be a subset.
            assert {"echo", "slowbg"}.issubset(set(agent.tools))
            assert agent.conversation_history == (
                agent.controller.conversation.to_messages()
            )

            # --- System-prompt hot-edit ---------------------------------
            base_prompt = agent.get_system_prompt()
            assert "You are a test agent." in base_prompt
            agent.update_system_prompt("Extra runtime guideline.")
            assert "Extra runtime guideline." in agent.get_system_prompt()
            # The original text survives an append.
            assert "You are a test agent." in agent.get_system_prompt()
            agent.update_system_prompt("Replaced entirely.", replace=True)
            assert agent.get_system_prompt() == "Replaced entirely."

            # --- session_info snapshot ----------------------------------
            # No session store attached → the ``own`` view is an empty
            # token dict, the ``all_loops`` view an empty list.
            info = agent.session_info()
            assert info["agent"] == "solo"
            assert info["tokens"] == {}
            assert agent.session_info(tokens_view="all_loops")["tokens"] == []

            # --- Turn 4: a veto plugin blocks the echo tool -------------
            # Register a second plugin whose pre_tool_dispatch raises
            # PluginBlockError for ``echo``. The call never reaches the
            # executor; the block message is synthesised as a queued
            # ``tool_complete`` event so the controller sees it on its
            # next round. Turn 4's own round produces no plain text (it
            # was a pure tool call), so ``out4`` is empty — the veto
            # short-circuits before any feedback loop continues.
            agent.plugins.register(_VetoPlugin("echo"))
            out4 = await _drain_chat(creature, "try echo again")
            assert out4 == ""  # pure tool-call round, then vetoed
            # The echo tool was NEVER executed for turn 4: the veto fired
            # before the executor, so no new echo job appears.
            echo_jobs = [
                j
                for j in agent.executor.job_store.get_all_statuses()
                if "echo" in j.job_id
            ]
            assert len(echo_jobs) == 1  # only turn 1's echo ever ran
            # The synthesised block result was queued on the controller.
            assert agent.controller.has_pending_events() is True
            # Turn 4b: the next input batches with the queued block
            # result; the controller acknowledges the veto message.
            out4b = await _drain_chat(creature, "is not allowed here?")
            assert "noticed the block" in out4b
            # rest_llm: turn-4 round (1) + turn-4b round (1) = 2.
            assert rest_llm.call_count == 2

            # --- Turn 5: two DIRECT tools in one round, run in parallel -
            # Disable the veto plugin so this turn's echoes actually run;
            # ``disable`` drops it from the active set without unregister.
            assert agent.plugins.disable("veto") is True
            assert agent.plugins.is_enabled("veto") is False
            calls_before_parallel = agent.llm.call_count
            out5 = await _drain_chat(creature, "run two echoes")
            assert "both echoes done" in out5
            convo5 = " ".join(
                m.get_text_content()
                for m in agent.controller.conversation.get_messages()
            )
            # Both parallel echoes executed — the rewrite plugin still
            # appended its marker to each.
            assert "echoed:left-rewritten" in convo5
            assert "echoed:right-rewritten" in convo5
            # Two echo jobs ran this turn; turn 1's was the only prior one.
            echo_jobs_after = [
                j
                for j in agent.executor.job_store.get_all_statuses()
                if "echo" in j.job_id
            ]
            assert len(echo_jobs_after) == 3
            assert agent.llm.call_count == calls_before_parallel + 2

            # --- Turn 6: an error-RESULT tool and a RAISING tool --------
            # The executor records the error result with its exit code,
            # and converts the raised RuntimeError into an error job too.
            out6 = await _drain_chat(creature, "trigger failures now")
            assert "saw the failures" in out6
            # Both failure modes surfaced as tool_error activities.
            errs = recorder.activities_of_type("tool_error")
            assert len(errs) >= 2
            convo6 = " ".join(
                m.get_text_content()
                for m in agent.controller.conversation.get_messages()
            )
            # The error-result tool's message and the raised exception
            # text both reached the conversation as feedback.
            assert "deliberate failure" in convo6
            assert "kaboom" in convo6
            # The executor's job_store recorded the failing tool's exact
            # exit code and an ERROR state for the raising tool.
            fail_status = next(
                j
                for j in agent.executor.job_store.get_all_statuses()
                if j.job_id.startswith("fail_")
            )
            assert fail_status.state.value == "error"
            boom_status = next(
                j
                for j in agent.executor.job_store.get_all_statuses()
                if j.job_id.startswith("boom_")
            )
            assert boom_status.state.value == "error"

            # --- Turn 7: multimodal + huge tool output normalization ----
            # ``snap`` returns a text+image ToolResult; with no artifact
            # store the image is rendered as a safe placeholder (no raw
            # base64 in context). ``huge`` returns 5000 chars but its
            # ToolConfig caps output at 100 bytes → truncated.
            out7 = await _drain_chat(creature, "snapshot and blob now")
            assert "processed media" in out7
            convo7 = " ".join(
                m.get_text_content()
                for m in agent.controller.conversation.get_messages()
            )
            # The multimodal tool's caption reached context as text.
            assert "caption-for-the-snapshot" in convo7
            # The raw base64 image data was NOT carried into context —
            # only a safe data-URL placeholder.
            assert "aGVsbG8td29ybGQ=" not in convo7
            assert "elided" in convo7
            # The huge tool's output was byte-capped — the truncation
            # note landed and the full 5000-char blob did not.
            assert "truncated to 100 bytes" in convo7
            assert "A" * 5000 not in convo7
            # The executor's job_store recorded the truncation metadata.
            huge_status = next(
                j
                for j in agent.executor.job_store.get_all_statuses()
                if j.job_id.startswith("huge_")
            )
            assert huge_status.state.value == "done"

            # --- Turn 8: framework commands resolve inline --------------
            # ``[/jobs]`` / ``[/info]`` / ``[/read_job]`` are framework
            # commands, not tools — the controller's
            # ``_execute_command_inline`` runs them inside the SAME round
            # and splices the result (or error) into the stored
            # assistant message. ``info echo`` returns docs; ``read_job``
            # on a ghost id and ``info no_such_tool`` both error.
            out8 = await _drain_chat(creature, "run the commands please")
            # The surrounding scripted text streamed.
            assert "checking" in out8
            convo8 = " ".join(
                m.get_text_content()
                for m in agent.controller.conversation.get_messages()
            )
            # The ##info echo## command spliced echo's description into
            # the stored assistant message verbatim.
            assert "Echo the call body back." in convo8
            # The erroring commands spliced a "[Command Error: ...]"
            # marker into the assistant message.
            assert "Command Error" in convo8
            # A command_done activity was surfaced for the good commands
            # and a command_error for the failing ones.
            cmd_done = recorder.activities_of_type("command_done")
            assert len(cmd_done) >= 2
            cmd_err = recorder.activities_of_type("command_error")
            assert len(cmd_err) >= 2

            # --- set_output_handler: a custom secondary sink -----------
            # ``set_output_handler`` wraps a plain callback into an
            # OutputModule and adds it as a secondary output. The next
            # turn's streamed text must reach the callback verbatim.
            captured: list[str] = []
            agent.set_output_handler(captured.append)
            out_cb = await _drain_chat(creature, "ephemeral check please")
            assert "ephemeral reply" in out_cb
            assert "ephemeral reply" in "".join(captured)

            # Canvas previews must survive direct-to-background promotion,
            # reach live output, and remain available after a session reload.
            session_path = tmp_path / "canvas.kohakutr"
            await engine.attach_session(creature.graph_id, session_path)
            agent.workspace.set(tmp_path)
            canvas_tool = CanvasImageTool()
            agent.add_tool(canvas_tool)
            canvas_events = TurnCapture()
            agent.output_router.add_secondary(canvas_events)
            expected_previews = []
            for mode in ("direct", "background"):
                image_path = tmp_path / f"{mode}.png"
                Image.new("RGB", (2, 2), color="blue").save(image_path)
                bg_arg = "@@run_in_background=true\n" if mode == "background" else ""
                canvas_llm = ScriptedLLM(
                    [
                        f"[/canvas_image]@@path={image_path}\n{bg_arg}[canvas_image/]",
                        f"{mode} canvas acknowledged",
                    ]
                )
                agent.llm = agent.controller.llm = canvas_llm
                await _drain_chat(creature, f"{mode} canvas")
                await _settle(
                    lambda: _assistant_text(agent) == f"{mode} canvas acknowledged"
                )
                assert _assistant_text(agent) == f"{mode} canvas acknowledged"
                completions = [
                    event
                    for event in canvas_events.activities
                    if event.kind == "tool_done"
                    and event.metadata.get("job_id", "").startswith("canvas_image_")
                ]
                assert (
                    len(completions) == len(expected_previews) + 1
                ), canvas_events.activities
                completion = completions[-1].metadata
                image_part = next(
                    part for part in completion["result"] if part["type"] == "image_url"
                )
                url = image_part["image_url"]["url"]
                expected = {
                    "kind": "image",
                    "file_path": str(image_path.resolve()),
                    "lang": "png",
                    "content": url,
                    "bytes": image_path.stat().st_size,
                    "truncated": False,
                }
                assert completion["canvas_preview"] == expected
                expected_previews.append(expected)
                artifact = (
                    agent.session_store.artifacts_dir / url.split("/artifacts/", 1)[1]
                )
                assert artifact.read_bytes() == image_path.read_bytes()
                start = next(
                    event
                    for event in canvas_events.activities
                    if event.kind == "tool_start"
                    and event.metadata.get("job_id") == completion["job_id"]
                )
                assert start.metadata["background"] is (mode == "background")

        # Engine __aexit__ stopped the creature.
        assert creature.is_running is False
        with SessionReader(session_path) as reader:
            canvas_results = [
                event
                for event in reader.events("solo")
                if event["type"] == "tool_result" and event["name"] == "canvas_image"
            ]
            assert [
                event["canvas_preview"] for event in canvas_results
            ] == expected_previews

        # --- A separate EPHEMERAL creature: conversation resets per turn.
        # ``ephemeral=True`` flips ``ControllerConfig.ephemeral`` so the
        # controller flushes everything but the system prompt after each
        # interaction (the group-chat-bot path).
        eph_creature, _ = make_creature(
            script=[
                ScriptEntry("first ephemeral answer", match="alpha"),
                ScriptEntry("second ephemeral answer", match="beta"),
            ],
            ephemeral=True,
            creature_id="eph",
        )
        eph_agent = eph_creature.agent
        assert eph_agent.controller.is_ephemeral is True
        async with Terrarium() as engine2:
            await engine2.add_creature(eph_creature)
            eph_out1 = await _drain_chat(eph_creature, "alpha question")
            assert "first ephemeral answer" in eph_out1
            # After an ephemeral turn only the system prompt survives.
            roles_after_1 = [
                m.role for m in eph_agent.controller.conversation.get_messages()
            ]
            assert roles_after_1 == ["system"]
            eph_out2 = await _drain_chat(eph_creature, "beta question")
            # Second turn ran fresh — its reply keyed on "beta" (the
            # match would have failed if stale "alpha" context leaked).
            assert "second ephemeral answer" in eph_out2
            roles_after_2 = [
                m.role for m in eph_agent.controller.conversation.get_messages()
            ]
            assert roles_after_2 == ["system"]
        assert eph_creature.is_running is False

    async def test_subagent_dispatch_and_result_routing(self, make_creature):
        """Controller dispatches a registered sub-agent; the sub-agent
        runs its own LLM loop; its result routes back into the parent
        conversation and drives a follow-up turn.

        Mirrors: a creature with a registered sub-agent (the VERTICAL
        composition level) — ``bootstrap.subagents`` wires these, and
        the controller loop dispatches them via ``_dispatch_subagent_event``.
        """
        creature, recorder = make_creature(
            script=[
                # Parent round 1: delegate to the explore sub-agent.
                ScriptEntry(
                    "[/explore]survey the repo[explore/]",
                    match="investigate",
                ),
                # Parent round 2: sub-agent result fed back, summarise.
                ScriptEntry("summary: exploration complete", match="explored:done"),
                # Turn 2: delegate to a SECOND sub-agent (multi-turn).
                ScriptEntry("[/planner]draft a plan[planner/]", match="now plan"),
                ScriptEntry("summary: plan received", match="planned:ready"),
                # Turn 3: a gate plugin rewrites the explore task — the
                # spawned sub-agent's task carries the "[gated]" suffix.
                ScriptEntry("[/explore]check again[explore/]", match="gated rerun"),
                ScriptEntry("summary: gated run complete", match="explored:gated"),
                # Turn 4: dispatch the FORBIDDEN sub-agent — the gate
                # plugin vetoes it; the synthesised block result is
                # queued and rides the next input.
                ScriptEntry("[/planner]nope[planner/]", match="forbidden delegate"),
                ScriptEntry("noticed the veto", match="is forbidden"),
            ]
        )
        agent = creature.agent
        # Register a sub-agent the same way ``bootstrap.subagents.init_subagents``
        # does: into the SubAgentManager (so it can be spawned) AND into
        # the Registry (so the controller's stream parser recognises the
        # ``[/explore]`` tag).
        sa_cfg = SubAgentConfig(
            name="explore",
            description="Survey a codebase.",
            tools=[],
            system_prompt="You are an explorer.",
            max_turns=1,
        )
        agent.subagent_manager.register(sa_cfg)
        agent.registry.register_subagent("explore", sa_cfg)
        # A second sub-agent — its own multi-turn config — proving the
        # VERTICAL composition level scales past one delegate.
        plan_cfg = SubAgentConfig(
            name="planner",
            description="Draft an execution plan.",
            tools=[],
            system_prompt="You are a planner.",
            max_turns=2,
        )
        agent.subagent_manager.register(plan_cfg)
        agent.registry.register_subagent("planner", plan_cfg)
        # The sub-agents share one deterministic LLM; each spawn consumes
        # the next scripted reply in order. The gate plugin appends
        # " [gated]" to every task — the explore sub-agent's third spawn
        # receives a task ending in "[gated]", so its scripted reply is
        # keyed on that exact substring.
        agent.subagent_manager.llm = ScriptedLLM(
            [
                "explored:done",
                "planned:ready",
                ScriptEntry("explored:gated ok", match="[gated]"),
            ]
        )

        async with Terrarium() as engine:
            await engine.add_creature(creature)
            assert set(agent.subagents) == {"fork", "explore", "planner"}

            # A background sub-agent's result drives a follow-up turn that can
            # land after chat() returns, so the summary is read once settled.
            await _drain_chat(creature, "investigate the project")
            await _settle(lambda: agent.llm.call_count == 2 and not agent.is_processing)

            # The parent's final turn summarised the sub-agent's work.
            assert "summary: exploration complete" in _assistant_text(agent)
            # The parent looped twice: dispatch, then summarise.
            assert agent.llm.call_count == 2
            # The sub-agent's result text reached the parent conversation
            # as a tool-result message.
            convo_text = " ".join(
                m.get_text_content()
                for m in agent.controller.conversation.get_messages()
            )
            assert "explored:done" in convo_text
            # A subagent_done activity was emitted to the output router.
            done = recorder.activities_of_type("subagent_done")
            assert len(done) == 1

            # --- Turn 2: dispatch the SECOND sub-agent ------------------
            await _drain_chat(creature, "now plan the work")
            await _settle(lambda: agent.llm.call_count == 4 and not agent.is_processing)
            assert "summary: plan received" in _assistant_text(agent)
            # The planner sub-agent's own LLM output routed back.
            convo_text2 = " ".join(
                m.get_text_content()
                for m in agent.controller.conversation.get_messages()
            )
            assert "planned:ready" in convo_text2
            # Two distinct sub-agent runs → two subagent_done activities.
            assert len(recorder.activities_of_type("subagent_done")) == 2
            # Parent looped twice more (dispatch + summarise).
            assert agent.llm.call_count == 4

            # --- Turn 3: pre_subagent_run rewrites the task -------------
            # The gate plugin appends "[gated]" to the dispatched task.
            # The explore sub-agent's scripted reply is keyed on that
            # exact suffix, so a green turn here PROVES the rewrite
            # reached ``spawn_from_event``.
            agent.plugins.register(_SubagentGatePlugin("planner"))
            await _drain_chat(creature, "gated rerun please")
            await _settle(lambda: agent.llm.call_count == 6 and not agent.is_processing)
            assert "summary: gated run complete" in _assistant_text(agent)
            convo_text3 = " ".join(
                m.get_text_content()
                for m in agent.controller.conversation.get_messages()
            )
            assert "explored:gated ok" in convo_text3
            assert agent.llm.call_count == 6

            # --- Turn 4: pre_subagent_run VETOES a forbidden delegate --
            # The gate plugin raises PluginBlockError for "planner";
            # ``run_pre_subagent_dispatch`` synthesises a blocked result
            # ("planner is forbidden") queued on the controller. Turn 4's
            # own round is a pure sub-agent call → no plain text out.
            calls_before_veto = agent.llm.call_count
            out4 = await _drain_chat(creature, "forbidden delegate now")
            assert out4 == ""
            # The synthesised block result was queued.
            assert agent.controller.has_pending_events() is True
            # Turn 4b: the next input batches with the queued veto
            # result; the controller acknowledges it.
            out4b = await _drain_chat(creature, "is forbidden, ok?")
            assert "noticed the veto" in out4b
            convo_text4 = " ".join(
                m.get_text_content()
                for m in agent.controller.conversation.get_messages()
            )
            assert "planner is forbidden" in convo_text4
            # Turn 4 round (1) + turn 4b round (1) = 2 more parent calls.
            assert agent.llm.call_count == calls_before_veto + 2

    async def test_compaction_and_termination(self, make_creature):
        """One workflow exercising context compaction AND a termination
        condition: a turn pushes token usage past the compact threshold,
        the compact manager splices the conversation, and on the next
        turn a termination keyword stops the agent.

        Mirrors: long-running creature behaviour — ``Agent.start`` builds
        the compact manager and termination checker; the controller loop
        fires ``_maybe_trigger_compact`` and ``_check_termination`` every
        iteration.
        """
        creature, recorder = make_creature(
            script=[
                ScriptEntry("answer one", match="one"),
                ScriptEntry("answer two", match="two"),
                ScriptEntry("answer three", match="three"),
                ScriptEntry("answer four", match="four"),
                ScriptEntry("ALL DONE now", match="five"),
            ],
            # Tiny budget so a few hundred tokens trips the threshold.
            compact={
                "max_tokens": 100,
                "threshold": 0.5,
                "target": 0.2,
                "keep_recent_turns": 1,
                "cooldown_seconds": 0.0,
            },
            termination={"keywords": ["ALL DONE"], "max_turns": 10},
        )
        agent = creature.agent

        async with Terrarium(session_dir=agent.config.agent_path / "runs") as engine:
            await engine.add_creature(creature)
            # Compact manager + termination checker were built by start().
            assert agent.compact_manager.config.max_tokens == 100
            assert agent.compact_manager.config.threshold == 0.5
            assert agent.compact_manager.config.target == 0.2
            assert agent._termination_checker.is_active is True
            pin_message(agent, "persona", "user", "PINNED-PERSONA")
            summarizer_inputs: list[str] = []
            real_summarize = agent.compact_manager._summarize

            async def spy_summarize(text):
                summarizer_inputs.append(text)
                return await real_summarize(text)

            agent.compact_manager._summarize = spy_summarize

            # Run several turns to build a conversation long enough for
            # the compact manager to have something to summarize.
            for word in ("one", "two", "three", "four"):
                await _drain_chat(creature, f"turn {word}")
            sent = agent.controller.llm.call_log[-1]
            assert sent[1]["content"] == "PINNED-PERSONA"
            agent._reload_conversation_under_branch_view({1: 1})
            conversation = agent.controller.conversation
            before_drop = conversation.snapshot_messages()
            assert any(msg.get("metadata", {}).get("turn_index") for msg in before_drop)
            conversation.append(
                "assistant",
                "",
                tool_calls=[
                    {
                        "id": "overflow_call",
                        "type": "function",
                        "function": {"name": "read", "arguments": "{}"},
                    }
                ],
                metadata={"turn_index": 4, "branch_id": 1},
            )
            conversation.append(
                "tool",
                "oversized result",
                tool_call_id="overflow_call",
                metadata={"turn_index": 4, "branch_id": 1},
            )
            _, recovered = drop_last_tool_round(conversation.to_messages())
            agent._on_provider_emergency_drop(recovered)
            assert recovered[-1]["content"].startswith("[tool-result truncated]")
            assert conversation.to_messages() == recovered
            assert conversation.snapshot_messages()[:-1] == before_drop
            assert conversation.get_messages()[-1].metadata == {}
            snapshot_path = agent.config.agent_path / "emergency.kohakutr"
            with closing(SessionStore(snapshot_path)) as store:
                store.save_conversation(
                    agent.config.name, conversation.snapshot_messages()
                )
            with closing(SessionStore(snapshot_path)) as store:
                resumed = build_conversation(store.load_conversation(agent.config.name))
            assert resumed.snapshot_messages() == conversation.snapshot_messages()
            assert all("metadata" not in msg for msg in resumed.to_messages())
            msgs_before = len(agent.controller.conversation.get_messages())
            assert msgs_before >= 8
            assert agent.is_running is True
            assert "answer four" in _assistant_text(agent)

            # Force the controller's recorded usage above the 50-token
            # compact threshold, then fire the turn-end hook.
            agent.controller._last_usage = {"prompt_tokens": 5000}
            compacts_before = agent.compact_manager._compact_count
            agent._maybe_trigger_compact(agent.controller)
            # The compact job runs as a background task — await it.
            await agent.compact_manager.wait_for_current()
            assert agent.compact_manager._compact_count == compacts_before + 1
            # Compaction spliced the conversation: a summary message
            # replaced the older turns, so the message count dropped.
            msgs_after = len(agent.controller.conversation.get_messages())
            assert msgs_after < msgs_before
            # The pin stayed out of the summary and out of the history.
            assert summarizer_inputs
            assert all("PINNED-PERSONA" not in text for text in summarizer_inputs)
            assert [key for key, _ in agent.controller.pinned.pinned()] == ["persona"]
            assert "PINNED-PERSONA" not in str(
                agent.controller.conversation.snapshot_messages()
            )

            # A second trigger right after a successful compaction with
            # usage still high fires another compaction (cooldown is 0s),
            # proving the compact path is re-entrant within a session.
            agent.controller._last_usage = {"prompt_tokens": 5000}
            count_after_first = agent.compact_manager._compact_count
            agent._maybe_trigger_compact(agent.controller)
            await agent.compact_manager.wait_for_current()
            assert agent.compact_manager._compact_count == count_after_first + 1
            assert agent.compact_rounds == agent.compact_manager._compact_count

            # A tool's compact_arguments hook shortens its old calls that
            # survive the splice; the newest call round keeps its arguments.
            agent.registry.register_tool(ShowAppTool())
            conversation = agent.controller.conversation
            for call_id, html in (("app_old", "<p>" + "x" * 4000), ("app_new", "<b>")):
                conversation.append(
                    "assistant",
                    "",
                    tool_calls=[
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": "show_app",
                                "arguments": json.dumps({"title": "t", "html": html}),
                            },
                        }
                    ],
                )
                conversation.append("tool", "app w_1 shown", tool_call_id=call_id)
            agent.controller._last_usage = {"prompt_tokens": 5000}
            agent._maybe_trigger_compact(agent.controller)
            await agent.compact_manager.wait_for_current()
            app_args = {
                call["id"]: json.loads(call["function"]["arguments"])
                for msg in agent.controller.conversation.get_messages()
                for call in getattr(msg, "tool_calls", None) or []
                if call["function"]["name"] == "show_app"
            }
            assert app_args["app_new"]["html"] == "<b>"
            assert app_args["app_old"]["html"].startswith("[app document")

            # With usage BELOW the threshold, the turn-end hook is a
            # no-op — the compact count stays put.
            agent.controller._last_usage = {"prompt_tokens": 10}
            steady = agent.compact_manager._compact_count
            agent._maybe_trigger_compact(agent.controller)
            await asyncio.sleep(0.05)
            assert agent.compact_manager._compact_count == steady

            # Final turn: assistant emits the termination keyword -> the
            # checker stops the agent inside the controller loop.
            await _drain_chat(creature, "turn five")
            assert "ALL DONE" in _assistant_text(agent)
            # After compaction the request is: system, pin, summary, live turns.
            sent = agent.controller.llm.call_log[-1]
            assert sent[0]["role"] == "system"
            assert sent[1]["content"] == "PINNED-PERSONA"
            assert sent[2]["content"].startswith("[Previous context summary")
            assert str(sent).count("PINNED-PERSONA") == 1
            assert agent.is_running is False
            assert "ALL DONE" in (agent._termination_checker.reason or "")
            # After termination the engine reports the creature stopped.
            assert creature.is_running is False

        # --- A second creature: max_turns termination -------------------
        # ``max_turns`` is the simplest termination condition — the
        # checker counts controller turns and stops the agent once the
        # cap is hit. Each ``_drain_chat`` is one user turn; with a cap
        # of 2 the agent is still running after turn 1 and stopped after
        # turn 2.
        mt_creature, _ = make_creature(
            script=[
                ScriptEntry("turn-one reply", match="first"),
                ScriptEntry("turn-two reply", match="second"),
            ],
            termination={"max_turns": 2},
            creature_id="maxturns",
        )
        mt_agent = mt_creature.agent
        async with Terrarium() as engine_mt:
            await engine_mt.add_creature(mt_creature)
            assert mt_agent._termination_checker.config.max_turns == 2
            await _drain_chat(mt_creature, "first request")
            # One turn recorded — below the cap, still alive.
            assert mt_agent._termination_checker.turn_count == 1
            assert mt_agent.is_running is True
            await _drain_chat(mt_creature, "second request")
            # Second turn hit the cap — the checker terminated the agent.
            assert mt_agent.is_running is False
            assert "Max turns" in (mt_agent._termination_checker.reason or "")
        assert mt_creature.is_running is False

        # --- A third creature: max_iterations → IterationBudget ---------
        # ``max_iterations`` builds an ``IterationBudget`` consumed once
        # per controller turn. When it drains, ``_check_termination``
        # translates ``BudgetExhausted`` into a clean stop with the
        # "Iteration budget exhausted" reason.
        budget_creature, _ = make_creature(
            script=[
                ScriptEntry("budget turn one", match="alpha"),
                ScriptEntry("budget turn two", match="beta"),
            ],
            max_iterations=1,
            creature_id="budgeted",
        )
        b_agent = budget_creature.agent
        async with Terrarium() as engine_b:
            await engine_b.add_creature(budget_creature)
            assert b_agent.iteration_budget is not None
            assert b_agent.iteration_budget.total == 1
            # Turn 1 consumes the single budgeted iteration.
            await _drain_chat(budget_creature, "alpha request")
            assert b_agent.iteration_budget.exhausted is True
            # Turn 2's _check_termination over-consumes the drained
            # budget → BudgetExhausted → the run loop exits and the
            # agent is stopped.
            await _drain_chat(budget_creature, "beta request")
            assert b_agent.is_running is False
        assert budget_creature.is_running is False

    async def test_interrupt_mid_turn(self, make_creature):
        """A turn dispatches a blocking DIRECT tool; ``interrupt()`` is
        called while the controller is waiting on it; the processing
        task is cancelled cleanly and the agent stays alive for the
        next input.

        Mirrors: the TUI Escape key / web stop button — both call
        ``Agent.interrupt`` on a live engine creature.
        """
        creature, recorder = make_creature(
            script=[
                ScriptEntry("[/block][block/]", match="start blocking"),
                # After the interrupt, a fresh input runs normally.
                ScriptEntry("recovered fine", match="are you ok"),
                # A turn that dispatches the blocking tool again, this
                # time so the test can cancel that one job by id while
                # the rest of the turn keeps going.
                ScriptEntry("[/block][block/]", match="block for cancel"),
                ScriptEntry("job was cancelled", match="User manually interrupted"),
                # A turn calling stop_task on a non-existent job id.
                ScriptEntry(
                    "[/stop_task]@@job_id=ghost_999\n[stop_task/]",
                    match="cancel a ghost",
                ),
                ScriptEntry("ghost not found", match="Task not found"),
                # A turn dispatching a BACKGROUND tool that raises.
                ScriptEntry(
                    "kicked off failing bg [/bgboom][bgboom/]", match="fail bg"
                ),
                ScriptEntry("noticed bg failure", match="background-kaboom"),
            ]
        )
        agent = creature.agent
        block = _BlockingTool()
        stop_task = StopTaskTool()
        bgboom = _BackgroundRaisingTool()
        for tool in (block, stop_task, bgboom):
            agent.registry.register_tool(tool)
            agent.executor.register_tool(tool)

        async with Terrarium() as engine:
            await engine.add_creature(creature)

            # Drive a turn that will hang on the blocking tool.
            inject_task = asyncio.create_task(
                agent.inject_input("start blocking", source="chat")
            )
            # Wait until the blocking tool is actually executing.
            await asyncio.wait_for(block.started.wait(), timeout=5.0)

            # Interrupt: cancels the processing task + the direct job.
            agent.interrupt()
            # inject_input awaits the processing cycle; the cancellation
            # is caught inside ``_process_event_with_controller`` so
            # inject_input itself returns without raising.
            await asyncio.wait_for(inject_task, timeout=5.0)

            # The agent survived the interrupt and is still running.
            assert agent.is_running is True
            assert agent._processing_task is None
            # An interrupt activity was surfaced to the output router.
            assert len(recorder.activities_of_type("interrupt")) >= 1
            # Only one LLM call happened — the blocked turn never looped.
            assert agent.llm.call_count == 1

            # The creature accepts a fresh turn after the interrupt.
            out = await _drain_chat(creature, "are you ok")
            assert "recovered fine" in out
            assert agent.llm.call_count == 2

            # --- _cancel_job: cancel one running job by id -------------
            # ``_cancel_job`` is the TUI running-panel click handler. It
            # cancels a tracked direct job and emits a ``job_cancelled``
            # activity. Drive a turn that hangs on the blocking tool,
            # then cancel that exact job by id mid-flight.
            block.started.clear()
            cancel_task = asyncio.create_task(
                agent.inject_input("block for cancel now", source="chat")
            )
            await asyncio.wait_for(block.started.wait(), timeout=5.0)
            # Exactly one direct job is tracked — the blocking tool's.
            tracked = list(agent._active_handles.keys())
            assert len(tracked) == 1
            job_id = tracked[0]
            agent._cancel_job(job_id, "block")
            # Cancelling the only job lets the turn's feedback loop see
            # an interrupted result and drive one follow-up turn whose
            # script entry is keyed on the interruption message.
            await asyncio.wait_for(cancel_task, timeout=5.0)
            assert "job was cancelled" in _assistant_text(agent)
            # A job_cancelled activity was surfaced.
            assert len(recorder.activities_of_type("job_cancelled")) >= 1
            assert agent.is_running is True

            # --- stop_task builtin on a non-existent job ---------------
            # The ``stop_task`` builtin tool routes through the agent's
            # executor / sub-agent manager. A ghost id resolves to
            # nothing → an error result that the controller sees.
            out_ghost = await _drain_chat(creature, "cancel a ghost job")
            assert "ghost not found" in out_ghost
            ghost_convo = " ".join(
                m.get_text_content()
                for m in agent.controller.conversation.get_messages()
            )
            assert "Task not found" in ghost_convo

            # --- a BACKGROUND tool that raises -------------------------
            # ``bgboom`` is promoted on dispatch; its ``_execute`` raises
            # after a short delay. The executor's ``_run_tool`` catches
            # the exception, records an ERROR job, and the completion
            # event carries the error string — which drives a follow-up
            # turn keyed on it.
            out_bg = await _drain_chat(creature, "fail bg now")
            assert "kicked off failing bg" in out_bg
            await _settle(lambda: "noticed bg failure" in _assistant_text(agent))
            assert "noticed bg failure" in _assistant_text(agent)
            # The executor recorded an ERROR job for the raising bg tool.
            bgboom_status = next(
                j
                for j in agent.executor.job_store.get_all_statuses()
                if j.job_id.startswith("bgboom_")
            )
            assert bgboom_status.state.value == "error"
            bg_provider_call = agent.llm.call_log[-1]
            bg_user_message = [
                message for message in bg_provider_call if message.get("role") == "user"
            ][-1]
            assert bg_user_message["content"].endswith(
                f"[Tool {bgboom_status.job_id} failed]\n" "Error: background-kaboom"
            )
            assert (
                f"[Tool {bgboom_status.job_id} completed]"
                not in bg_user_message["content"]
            )
            # ``executor.wait_all`` drains every tracked task and returns
            # the completed JobResults — the bg job's result carries its
            # error string.
            all_results = await agent.executor.wait_all(timeout=5.0)
            bgboom_result = next(
                r for jid, r in all_results.items() if jid.startswith("bgboom_")
            )
            assert "background-kaboom" in (bgboom_result.error or "")

            # Awaited requests retain ownership while a real DIRECT job runs.
            owned_llm = ScriptedLLM(
                [
                    ScriptEntry("[/block][block/]", match="owned blocking"),
                    ScriptEntry("must not execute", match="withdrawn request"),
                    ScriptEntry("owned answer one", match="owned input one"),
                    ScriptEntry("owned answer two", match="owned input two"),
                ]
            )
            agent.llm = owned_llm
            agent.controller.llm = owned_llm
            block.started.clear()
            owner = asyncio.create_task(agent.run("owned blocking"))
            try:
                await asyncio.wait_for(block.started.wait(), 5)
                withdrawn = await agent.run(
                    "withdrawn request", timeout=0.05, raise_on_error=False
                )
                assert withdrawn.status == "timeout"
                assert not owner.done()
                assert owned_llm.call_count == 1
                owner.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await owner
                assert not agent.is_processing
                one, two = await asyncio.gather(
                    agent.run("owned input one"), agent.run("owned input two")
                )
                assert (one.text, two.text) == ("owned answer one", "owned answer two")
                assert owned_llm.call_count == 3
                assert all(
                    "withdrawn request" not in str(message.get("content", ""))
                    for call in owned_llm.call_log
                    for message in call
                )
            finally:
                if not owner.done():
                    owner.cancel()
                await asyncio.gather(owner, return_exceptions=True)

    @pytest.mark.timeout(120)
    async def test_history_ops(self, make_creature, tmp_path, monkeypatch):
        """One workflow over the three history operations: run a turn,
        ``regenerate_last_response`` opens a new branch, ``edit_and_rerun``
        replaces the user message and re-runs, ``rewind_to`` drops the
        tail. Backed by a real ``SessionStore`` so branch bookkeeping
        (``turn_index`` / ``branch_id``) is exercised.

        Mirrors: the frontend retry / edit / rewind buttons and the CLI
        ``/regen`` command — all three call these same ``Agent`` methods,
        and ``Agent.attach_session_store`` is how the engine wires
        persistence onto a creature.
        """
        legacy_image = tmp_path / "legacy-image.png"
        Image.new("RGB", (2, 2), "red").save(legacy_image)
        repair_command = RepairImagesCommand()
        async with Terrarium(session_dir=tmp_path / "repair-media") as engine:
            legacy = await engine.add_creature(
                AgentConfig(
                    name="legacy_media", system_prompt="plain", tool_format="native"
                ),
                llm=_HistoryReplayLLM(
                    True,
                    plan=[
                        [
                            NativeToolCall(
                                "read_old",
                                "read",
                                json.dumps({"path": str(legacy_image)}),
                            )
                        ],
                        "image seen",
                        "continued",
                    ],
                ),
                tools=[_LegacyImageReadTool()],
                pwd=tmp_path,
                io="headless",
                start=True,
            )
            assert (await legacy.run("read the image")).ok
            legacy_image.unlink()
            failed = await legacy.run("continue", raise_on_error=False)
            assert not failed.ok and "Required local media is unavailable" in str(
                failed.error
            )
            context = UserCommandContext(agent=legacy.agent)
            preview = await repair_command.execute("", context)
            assert preview.success and legacy_image.as_uri() in preview.output
            assert "message" in preview.output and "turn 1" in preview.output
            raw_events = legacy.agent.session_store.get_events("legacy_media")
            repaired = await repair_command.execute(
                preview.data["action_args"], context
            )
            assert repaired.success and "Replaced 1" in repaired.output
            assert legacy.agent.session_store.get_events("legacy_media") == raw_events
            assert "No repairable" in (await repair_command.execute("", context)).output
            repaired_path = legacy.agent.session_store.path

        restored, repaired_store = resume_agent(
            repaired_path, io_mode="plain", llm=ScriptedLLM(["continued after repair"])
        )
        try:
            await restored.start()
            for prompt in ("continue after reopen", "continue again"):
                assert (await restored.run(prompt)).text == "continued after repair"
            assert "Historical image unavailable" in str(restored.llm.last_messages)
            assert not any(
                part.get("type") == "image_url"
                for message in restored.llm.last_messages
                if isinstance(message.get("content"), list)
                for part in message["content"]
            )
        finally:
            await restored.stop()
            repaired_store.close()

        creature, recorder = make_creature(
            script=[
                ScriptEntry("original reply", match="first question"),
                ScriptEntry("regenerated reply", match="first question"),
                ScriptEntry("regenerated again", match="first question"),
                ScriptEntry("edited reply", match="changed question"),
                # A second turn, then a turn-targeted regenerate of it.
                ScriptEntry("second turn reply", match="second question"),
                ScriptEntry("turn2 regen reply", match="second question"),
                ScriptEntry("continued original", match="continue original"),
                ScriptEntry("channel turn reply", match="queued behind channel"),
                ScriptEntry("after channel reply", match="after channel turn"),
            ]
        )
        agent = creature.agent

        async with Terrarium() as engine:
            await engine.add_creature(creature)
            # Attach a real session store — this is the engine's
            # persistence-wiring path.
            store = SessionStore(str(creature.agent.config.agent_path / "s.kohakutr"))
            agent.attach_session_store(store)
            # A pin never shifts history indices and survives every history op.
            pin_message(agent, "persona", "user", "PINNED-PERSONA")

            # --- Run the initial turn ----------------------------------
            await _drain_chat(creature, "first question")
            assert "original reply" in _assistant_text(agent)
            assert agent._turn_index == 1
            assert agent._branch_id == 1

            # Persist the native-tool event shape produced by a background
            # completion. The event log records the call and result, while
            # the assistant announcement is reconstructed for replay.
            replay_call_id = "bash_history_replay_1"
            store.append_event(
                agent.config.name,
                "tool_call",
                {"name": "bash", "call_id": replay_call_id, "args": {"cmd": "pwd"}},
                turn_index=1,
                branch_id=1,
                parent_branch_path=[],
            )
            store.append_event(
                agent.config.name,
                "tool_result",
                {"name": "bash", "call_id": replay_call_id, "output": "/work"},
                turn_index=1,
                branch_id=1,
                parent_branch_path=[],
            )

            # --- regenerate: same user msg, new branch -----------------
            before_rerun = store.get_events(agent.config.name)
            previous_id = next(
                event["event_id"]
                for event in before_rerun
                if event["type"] == "user_message"
            )
            for branch, reply in ((2, "regenerated reply"), (3, "regenerated again")):
                await agent.regenerate_last_response()
                assert reply in _assistant_text(agent)
                assert (agent._turn_index, agent._branch_id) == (1, branch)
                assert agent._max_branch_id_for_turn(1) == branch
                live_user = next(
                    message
                    for message in agent.controller.conversation.get_messages()
                    if message.role == "user"
                )
                canonical = [
                    event
                    for event in store.get_events(agent.config.name)
                    if event["type"] == "user_message"
                ][-1]
                assert canonical["event_id"] > previous_id
                assert live_user.metadata["event_id"] == canonical["event_id"]
                assert (canonical["turn_index"], canonical["branch_id"]) == (1, branch)
                assert canonical["content"] == live_user.content == "first question"
                request = [
                    message
                    for message in agent.llm.call_log[-1]
                    if message["role"] != "system"
                ]
                assert request == [
                    {"role": "user", "content": "PINNED-PERSONA"},
                    {"role": "user", "content": "first question"},
                ]
                await agent._session_output.drain()
                saved_user = next(
                    message
                    for message in store.load_branch_snapshot(
                        agent.config.name, {1: branch}
                    )["messages"]
                    if message["role"] == "user"
                )
                assert saved_user["metadata"]["event_id"] == canonical["event_id"]
                previous_id = canonical["event_id"]
            assert (
                store.get_events(agent.config.name)[: len(before_rerun)] == before_rerun
            )

            # --- edit_and_rerun: replace the user message, re-run ------
            # System message is index 0, the user message is index 1.
            ok = await agent.edit_and_rerun(
                message_idx=1, new_content="changed question"
            )
            assert ok is True
            assert "edited reply" in _assistant_text(agent)
            # The edited content is the live user message in conversation.
            user_msgs = [
                m
                for m in agent.controller.conversation.get_messages()
                if m.role == "user"
            ]
            assert user_msgs[-1].get_text_content() == "changed question"
            # Editing turn 1 opened yet another branch on it.
            assert agent._turn_index == 1
            assert agent._branch_id == 4

            # --- a fresh second turn -----------------------------------
            # A new user input after an edit advances to turn 2; the
            # parent_branch_path now records (turn 1, branch 4) so a
            # later branch switch on turn 1 can hide turn 2's events.
            await _drain_chat(creature, "second question")
            assert "second turn reply" in _assistant_text(agent)
            assert agent._turn_index == 2
            assert agent._branch_id == 1

            # --- regenerate_last_response(turn_index=2): retry an
            #     explicit turn ---------------------------------------
            # Passing ``turn_index`` routes through ``edit_and_rerun``
            # with the turn's recorded user content — the "click retry
            # on a specific assistant message" path. It opens a new
            # branch on turn 2 and exercises the branch-resolution
            # helpers (_user_message_content_for_turn,
            # _user_position_for_turn_index, _live_user_turns).
            await agent.regenerate_last_response(turn_index=2)
            assert "turn2 regen reply" in _assistant_text(agent)
            assert agent._turn_index == 2
            # Turn 2 now has two branches recorded.
            assert agent._max_branch_id_for_turn(2) == 2
            # The live user turns the helper sees are turns 1 and 2.
            assert agent._live_user_turns() == [1, 2]
            # The recorded user_message content for turn 2 is resolvable.
            assert agent._user_message_content_for_turn(2) == "second question"

            # --- rewind_to: drop the tail, no re-run -------------------
            await agent.rewind_to(1)
            roles = [m.role for m in agent.controller.conversation.get_messages()]
            # Only the system prompt survives a rewind to index 1.
            assert roles == ["system"]
            # rewind does not invoke the LLM.
            assert agent.llm.call_count == 6
            assert [key for key, _ in agent.controller.pinned.pinned()] == ["persona"]
            assert all(
                log[1]["content"] == "PINNED-PERSONA" for log in agent.llm.call_log
            )
            assert not any(
                m.metadata.get("pinned")
                for m in agent.controller.conversation.get_messages()
            )

            # --- workspace: runtime working-directory switch -----------
            # ``agent.workspace`` is wired by ``init_agent_helpers``; it
            # reflects the executor's current working dir.
            agent_path = agent.config.agent_path
            assert agent.workspace.get() == str(
                Path(agent.executor._working_dir).resolve()
            )
            new_dir = agent_path / "subtree"
            new_dir.mkdir()
            resolved = agent.workspace.set(new_dir)
            assert resolved == str(new_dir.resolve())
            # The switch took on the executor — every fresh ToolContext
            # builds from this.
            assert str(agent.executor._working_dir) == str(new_dir.resolve())
            assert agent.workspace.get() == str(new_dir.resolve())
            # The session store's meta picked up the new pwd so resume
            # restores the latest cwd.
            assert store.meta["pwd"] == str(new_dir.resolve())
            # A non-existent path is rejected before any state changes.
            with pytest.raises(ValueError):
                agent.workspace.set(agent_path / "does-not-exist")
            assert str(agent.executor._working_dir) == str(new_dir.resolve())

            # --- plugin_options: per-session plugin override + persist -
            # ``budget`` is a catalog plugin; override its turn_budget and
            # read the merged options back.
            applied = agent.plugin_options.set(
                "budget", {"turn_budget": {"soft": 2, "hard": 5}}
            )
            assert applied["turn_budget"] == {"soft": 2, "hard": 5}
            # The override is tracked on the helper and reflected live on
            # the plugin instance.
            assert agent.plugin_options.get("budget")["turn_budget"] == {
                "soft": 2,
                "hard": 5,
            }
            budget_plugin = agent.plugins.get_plugin("budget")
            assert budget_plugin.options["turn_budget"] == {"soft": 2, "hard": 5}
            for field in ("soft", "hard"):
                for non_finite in (float("nan"), float("inf"), float("-inf")):
                    invalid = {"soft": 2, "hard": 5, field: non_finite}
                    with pytest.raises(ValueError, match="finite"):
                        agent.plugin_options.set("budget", {"turn_budget": invalid})
                    assert budget_plugin.options["turn_budget"] == {
                        "soft": 2,
                        "hard": 5,
                    }
                    assert budget_plugin.budgets.turn.soft == 2
                    assert budget_plugin.budgets.turn.hard == 5
            # The override was persisted to private session state.
            saved = store.state.get("solo:plugin_options")
            assert saved["budget"]["turn_budget"] == {"soft": 2, "hard": 5}
            # An unknown plugin name is a clean KeyError.
            with pytest.raises(KeyError):
                agent.plugin_options.set("no-such-plugin", {})

            # --- conversation serialization round-trip -----------------
            # The conversation rebuilt from the rewind still has the
            # system prompt; run one more turn so there is real content
            # to round-trip through JSON.
            await _drain_chat(creature, "first question")
            conv = agent.controller.conversation
            chars_before = conv.get_context_length()
            assert chars_before > 0
            assert len(conv) == len(conv.get_messages())
            assert bool(conv) is True
            json_blob = conv.to_json()
            restored = Conversation.from_json(json_blob)
            # The restored conversation carries the exact same messages
            # in the same order with the same text payloads.
            assert [m.role for m in restored.get_messages()] == [
                m.role for m in conv.get_messages()
            ]
            assert restored.get_context_length() == chars_before
            assert (
                restored.get_last_assistant_message().get_text_content()
                == conv.get_last_assistant_message().get_text_content()
            )
            # ``find_last_user_index`` points at the live user message.
            last_user_idx = conv.find_last_user_index()
            assert conv.get_messages()[last_user_idx].role == "user"

            # --- edit_and_rerun rejects an out-of-range index ----------
            # A message_idx past the end resolves to None → returns False
            # and runs no LLM call.
            calls_pre = agent.llm.call_count
            context_before = agent.controller.conversation.to_json()
            path_before = live_branch_path(agent)
            events_before = store.get_events(agent.config.name)
            bad = await agent.edit_and_rerun(
                message_idx=999, new_content="nope", branch_view={1: 1}
            )
            assert bad is False
            assert agent.llm.call_count == calls_pre
            assert agent.controller.conversation.to_json() == context_before
            assert live_branch_path(agent) == path_before
            assert store.get_events(agent.config.name) == events_before

            # --- native_tool_options on a non-native tool is rejected --
            # ``echo``-style tools are NOT provider-native, so setting
            # native options for any unknown native tool raises.
            with pytest.raises(ValueError):
                agent.native_tool_options.set("definitely-not-native", {"k": "v"})
            # The override map stays empty after the rejected set.
            assert agent.native_tool_options.list() == {}

            # Live path is the latest subtree. inject_input must reseat
            # onto the original branch without a prior private reload.
            assert live_branch_path(agent).get(1) != 1
            previous_branches = {
                event["branch_id"]
                for event in store.get_events(agent.config.name)
                if event.get("turn_index") == 2
            }
            assert agent._max_branch_id_for_turn(2) == max(previous_branches)
            published_maxima = []

            def observe_publication(key, event):
                if event.get("type") == "user_message" and event.get("turn_index") == 2:
                    published_maxima.append(
                        (event["branch_id"], agent._max_branch_id_for_turn(2))
                    )

            store.subscribe(observe_publication)
            try:
                await agent.inject_input("continue original", branch_view={1: 1})
            finally:
                store.unsubscribe(observe_publication)
            assert published_maxima == [
                (max(previous_branches) + 1, max(previous_branches) + 1)
            ]
            continuation = next(
                event
                for event in store.get_events(agent.config.name)
                if event["type"] == "user_message"
                and event.get("content") == "continue original"
            )
            assert continuation["turn_index"] == 2
            assert continuation["branch_id"] == max(previous_branches) + 1
            assert continuation["parent_branch_path"] == [[1, 1]]
            assert agent.llm.call_log[-1][1]["content"] == "PINNED-PERSONA"
            continued = [
                (message.get("role"), message.get("content"))
                for message in agent.llm.call_log[-1][2:]
            ]
            assert continued[0] == ("user", "first question")
            assert ("assistant", "original reply") in continued
            assert continued[-1] == ("user", "continue original")
            assert ("assistant", "regenerated reply") not in continued
            assert ("assistant", "edited reply") not in continued
            assert ("user", "second question") not in continued
            assert "continued original" in _assistant_text(agent)
            persisted = load_persisted_branch_view(store, agent.config.name)
            assert persisted[1] == 1
            assert selected_branch_view(agent)[1] == 1
            assert agent._max_branch_id_for_turn(1) >= 2

            # --- a user message folded behind a channel message ---------
            # It opens the turn, so a later input with an older view continues it.
            stale_view = selected_branch_view(agent)
            turn_before = agent._turn_index
            agent.pause()
            channel = TriggerEvent(
                type=EventType.CHANNEL_MESSAGE,
                content="[channel team] bob: team done",
                context={"channel": "team", "sender": "bob"},
                stackable=True,
            )
            delivered = asyncio.create_task(agent._process_event(channel))
            await asyncio.sleep(0)
            folded = await agent.inject_input(
                "queued behind channel", source="web", branch_view=stale_view
            )
            assert folded is False
            agent.resume()
            await delivered
            await _settle(
                lambda: agent._turn_index == turn_before + 1 and not agent.is_processing
            )
            assert "channel turn reply" in _assistant_text(agent)
            opened = [
                (event["type"], event.get("content"), event["turn_index"])
                for event in store.get_events(agent.config.name)
                if event["type"] in ("user_input", "user_message")
            ][-2:]
            assert opened == [
                ("user_input", "queued behind channel", turn_before + 1),
                ("user_message", "queued behind channel", turn_before + 1),
            ]
            await agent.inject_input(
                "after channel turn", source="web", branch_view=stale_view
            )
            assert "after channel reply" in _assistant_text(agent)
            assert agent._turn_index == turn_before + 2
            kept = [
                message.get_text_content()
                for message in agent.controller.conversation.get_messages()
                if message.role == "assistant"
            ]
            assert kept[-2:] == ["channel turn reply", "after channel reply"]

        for mode in ("native", "bracket"):
            folder = tmp_path / mode
            folder.mkdir()
            config = folder / "config.yaml"
            config.write_text(
                f"name: history_probe\nsystem_prompt: offline\ntool_format: {mode}\n"
                "input: {type: none}\noutput: {type: stdout}\n"
                "tools:\n  - {name: scratchpad, type: builtin}\n"
            )
            llm = _HistoryReplayLLM(mode == "native")
            async with Terrarium(session_dir=folder / "sessions") as engine:
                creature = await engine.add_creature(
                    str(config), llm=llm, io="headless", pwd=folder, start=True
                )
                service = LocalTerrariumService(engine)
                agent = creature.agent
                assert (await creature.run("seed", timeout=5)).ok
                assert agent.scratchpad.get("history") == "preserved"
                pending = asyncio.create_task(
                    creature.run("original", timeout=5, raise_on_error=False)
                )
                await asyncio.wait_for(llm.started.wait(), 2)
                await service.interrupt(creature.creature_id)
                await pending
                assert not agent.is_processing
                assert not (await service.chat_history(creature.creature_id))[
                    "is_processing"
                ]
                events = agent.session_store.get_events(agent.config.name)
                target = [e for e in events if e["type"] == "user_message"][-1]
                recorded_call = next(e for e in events if e["type"] == "tool_call")
                assert recorded_call["name"] == "scratchpad"
                expected_id = (
                    "call_history_1" if llm.native else recorded_call["call_id"]
                )
                selector = UserMessageSelector(
                    target["event_id"], target["turn_index"], target["branch_id"]
                )
                result = await service.edit_message(
                    creature.creature_id, 0, "edited", target=selector
                )
                assert result["branch_id"] == 2
                calls = [
                    call
                    for message in llm.last_messages
                    for call in message.get("tool_calls") or []
                ]
                assert [(call["id"], call["function"]["name"]) for call in calls] == [
                    (expected_id, "scratchpad")
                ]
                assert any(
                    m.get("tool_call_id") == expected_id for m in llm.last_messages
                )
                assert (await creature.run("followup", timeout=5)).text == "OK"
                session_path = agent.session_store.path
            resumed = await Terrarium.resume(
                str(session_path), llm=ScriptedLLM(["resumed OK"])
            )
            async with resumed:
                restored = resumed.list_creatures()[0]
                assert (
                    await restored.run("after resume", timeout=5)
                ).text == "resumed OK"
                messages = restored.agent.controller.conversation.to_messages()
                calls = [
                    call
                    for message in messages
                    for call in message.get("tool_calls") or []
                ]
                assert [(call["id"], call["function"]["name"]) for call in calls] == [
                    (expected_id, "scratchpad")
                ]

        def prefix(messages):
            rows = []
            for message in messages:
                role = message["role"]
                if role == "system":
                    continue
                if message.get("tool_calls"):
                    rows.append(
                        (
                            "calls",
                            [
                                (
                                    c["id"],
                                    c["function"]["name"],
                                    c["function"]["arguments"],
                                )
                                for c in message["tool_calls"]
                            ],
                        )
                    )
                elif role == "tool":
                    rows.append(
                        (
                            role,
                            message["tool_call_id"],
                            message.get("name"),
                            message["content"],
                        )
                    )
                else:
                    rows.append((role, message["content"]))
            return rows

        for notify in (True, False):
            echo, bg = _EchoTool(), _SlowBackgroundTool(gated=True)
            bg.config.notify_controller_on_background_complete = notify
            plan = [
                [
                    NativeToolCall("c1", "echo", '{"content":"a"}'),
                    NativeToolCall("c2", "echo", '{"content":"b"}'),
                ],
                "direct done",
                [NativeToolCall("b1", "slowbg", "{}")],
                "later done",
                "completion done" if notify else "snapshot done",
                "later sibling",
                "orig cont",
                "sib cont",
                "resume one",
                "resume two",
            ]
            llm = _HistoryReplayLLM(True, plan=plan)
            async with Terrarium(session_dir=tmp_path / f"settled-{notify}") as engine:
                creature = await engine.add_creature(
                    AgentConfig(
                        name="settled", system_prompt="plain", tool_format="native"
                    ),
                    llm=llm,
                    tools=[echo, bg],
                    io="headless",
                    start=True,
                )
                agent = creature.agent
                agent.controller.config.include_job_status = False
                cold_store = None
                try:
                    await creature.run("do two", timeout=8)
                    direct = [
                        ("user", "do two"),
                        (
                            "calls",
                            [
                                ("c1", "echo", '{"content":"a"}'),
                                ("c2", "echo", '{"content":"b"}'),
                            ],
                        ),
                        ("tool", "c1", "echo", "echoed:a"),
                        ("tool", "c2", "echo", "echoed:b"),
                    ]
                    assert prefix(llm.call_log[1]) == direct
                    await creature.run("launch", timeout=8)
                    await asyncio.wait_for(bg.started.wait(), 2)
                    job = agent.executor.get_running_jobs()[0]
                    job_id = job.job_id
                    task = agent.executor._tasks[job_id]
                    ack = next(
                        m
                        for m in agent.controller.conversation.get_messages()
                        if m.role == "tool" and m.tool_call_id == "b1"
                    )
                    assert ack.metadata == {
                        "job_id": job_id,
                        "tool_result_phase": "pending",
                    }
                    await creature.run("later", timeout=8)
                    if notify:
                        async with creature.attach() as stream:
                            bg.release.set()
                            while True:
                                event = await asyncio.wait_for(anext(stream), 8)
                                if (
                                    isinstance(event, Activity)
                                    and event.kind == "processing_end"
                                ):
                                    break
                        completion = agent._turn_completion
                        if completion is not None:
                            await asyncio.wait_for(asyncio.shield(completion), 8)
                        assert (
                            agent.controller.conversation.get_last_assistant_message().content
                            == "completion done"
                        )
                        await task
                        feedback = next(
                            m
                            for m in agent.controller.conversation.get_messages()
                            if m.metadata.get("kind") == "tool_results"
                        )
                        assert feedback.metadata == {
                            "kind": "tool_results",
                            "job_ids": [job_id],
                            "tool_result_phase": "final",
                            "turn_index": 3,
                            "branch_id": 1,
                            "origin_known": True,
                        }
                    else:
                        bg.release.set()
                        await asyncio.wait_for(task, 8)
                        assert len(agent._event_inbox) == 0
                        await creature.run("save ack snapshot", timeout=8)
                        snapshot = agent.session_store.load_conversation("settled")
                        assert (
                            next(m for m in snapshot if m.get("tool_call_id") == "b1")[
                                "metadata"
                            ]["tool_result_phase"]
                            == "pending"
                        )
                        path = agent.session_store.path
                        await engine.remove_creature(creature)
                        agent.session_store.close()
                        agent, cold_store = resume_agent(path, io_mode="plain", llm=llm)
                        restored_bg = [
                            m
                            for m in agent.controller.conversation.get_messages()
                            if m.role == "tool" and m.tool_call_id == "b1"
                        ]
                        assert [(m.name, m.content) for m in restored_bg] == [
                            ("slowbg", "background-finished")
                        ]
                        assert [
                            (m.tool_call_id, m.name, m.content)
                            for m in agent.controller.conversation.get_messages()
                            if m.role == "tool"
                        ] == [
                            ("c1", "echo", "echoed:a"),
                            ("c2", "echo", "echoed:b"),
                            ("b1", "slowbg", "background-finished"),
                        ]
                        assert restored_bg[0].metadata == {
                            "job_id": job_id,
                            "tool_result_phase": "final",
                            "result_event_id": next(
                                e["event_id"]
                                for e in cold_store.get_events("settled")
                                if e["type"] == "tool_result"
                                and e.get("call_id") == job_id
                            ),
                        }
                        assert (agent._turn_index, agent._branch_id) == (4, 1)
                        assert agent._parent_branch_path == [(1, 1), (2, 1), (3, 1)]
                        assert agent._context_cutoff_event_id == 0
                        pending = cold_store.get_resumable_events("settled")
                        assert agent._pending_resume_events == pending
                        repaired_messages = copy.deepcopy(
                            [
                                dict(m.to_dict(), metadata=m.metadata)
                                for m in agent.controller.conversation.get_messages()
                            ]
                        )
                        cold_store.close(update_status=False)
                        agent, cold_store = resume_agent(path, io_mode="plain", llm=llm)
                        assert [
                            dict(m.to_dict(), metadata=m.metadata)
                            for m in agent.controller.conversation.get_messages()
                        ] == repaired_messages
                        assert agent._pending_resume_events == pending
                        assert agent._context_cutoff_event_id == 0
                        assert (agent._turn_index, agent._branch_id) == (4, 1)
                        assert agent._parent_branch_path == [(1, 1), (2, 1), (3, 1)]
                        await agent.start()
                    raw_settled = agent.session_store.get_events("settled")
                    call_event = next(
                        e
                        for e in raw_settled
                        if e["type"] == "tool_call" and e.get("call_id") == job_id
                    )
                    result_event = next(
                        e
                        for e in raw_settled
                        if e["type"] == "tool_result" and e.get("call_id") == job_id
                    )
                    assert result_event["turn_index"] > call_event["turn_index"]
                    original = dict(live_branch_path(agent))
                    await agent.regenerate_last_response(turn_index=agent._turn_index)
                    sibling = dict(live_branch_path(agent))
                    assert sibling != original
                    expected = direct + [
                        ("assistant", "direct done"),
                        ("user", "launch"),
                        ("calls", [("b1", "slowbg", "{}")]),
                        ("tool", "b1", "slowbg", "background-finished"),
                        ("user", "later"),
                    ]
                    if not notify:
                        expected += [
                            ("assistant", "later done"),
                            ("user", "save ack snapshot"),
                        ]
                    original_tail = (
                        [("assistant", "later done"), ("assistant", "completion done")]
                        if notify
                        else [("assistant", "snapshot done")]
                    )
                    await agent.inject_input("continue original", branch_view=original)
                    assert prefix(llm.last_messages) == expected + original_tail + [
                        ("user", "continue original")
                    ]
                    await agent.inject_input("continue sibling", branch_view=sibling)
                    selected = expected + [
                        ("assistant", "later sibling"),
                        ("user", "continue sibling"),
                    ]
                    assert prefix(llm.last_messages) == selected
                    selected += [("assistant", "sib cont")]
                    await agent._session_output.drain()
                    manager = agent.compact_manager
                    manager.config.keep_recent_turns = 1
                    summary_llm = ScriptedLLM(["summary SIBLING"])
                    manager._llm = summary_llm
                    assert manager.trigger_compact()
                    await asyncio.wait_for(manager.wait_for_current(), 8)
                    await agent._session_output.drain()
                    assert "background-finished" in str(summary_llm.last_messages)
                    messages = agent.controller.conversation.to_messages()
                    assert not any(
                        m.get("tool_calls") or m["role"] == "tool" for m in messages
                    )
                    raw_compacted = agent.session_store.get_events("settled")
                    bg_result = next(
                        e
                        for e in raw_compacted
                        if e["type"] == "tool_result" and e.get("call_id") == job_id
                    )
                    compact_event = next(
                        e
                        for e in reversed(raw_compacted)
                        if e["type"] == "compact_complete"
                    )
                    assert (
                        bg_result["event_id"]
                        in compact_event["compact_result_event_ids"]
                    )
                    if notify:
                        assert [3, 1] not in compact_event["compact_source_path"]
                    selected = prefix(messages)
                    assert selected[-3:] == [
                        (
                            "assistant",
                            "[Previous context summary (compact round 1)]\n\nsummary SIBLING",
                        ),
                        ("user", "continue sibling"),
                        ("assistant", "sib cont"),
                    ]
                    saved_view = selected_branch_view(agent)
                    full_selected = copy.deepcopy(
                        [
                            dict(m.to_dict(), metadata=m.metadata)
                            for m in agent.controller.conversation.get_messages()
                        ]
                    )
                    saved_cutoff = agent._context_cutoff_event_id
                    path = agent.session_store.path
                finally:
                    bg.release.set()
                    if cold_store is not None:
                        await agent.stop()
                        cold_store.close()

            for prompt, response in (
                ("after resume", "resume one"),
                ("after second resume", "resume two"),
            ):
                restored, store = resume_agent(path, io_mode="plain", llm=llm)
                try:
                    assert selected_branch_view(restored) == saved_view
                    assert (
                        prefix(restored.controller.conversation.to_messages())
                        == selected
                    )
                    assert [
                        dict(m.to_dict(), metadata=m.metadata)
                        for m in restored.controller.conversation.get_messages()
                    ] == full_selected
                    assert restored._context_cutoff_event_id == saved_cutoff
                    assert (
                        restored._pending_resume_events
                        == store.get_resumable_events("settled")
                    )
                    await restored.start()
                    await restored.inject_input(prompt)
                    selected += [("user", prompt)]
                    assert prefix(llm.last_messages) == selected
                    selected += [("assistant", response)]
                    assert (
                        prefix(restored.controller.conversation.to_messages())
                        == selected
                    )
                    await restored._session_output.drain()
                    full_selected = copy.deepcopy(
                        [
                            dict(m.to_dict(), metadata=m.metadata)
                            for m in restored.controller.conversation.get_messages()
                        ]
                    )
                    saved_cutoff = restored._context_cutoff_event_id
                    saved_view = selected_branch_view(restored)
                    path = store.path
                finally:
                    await restored.stop()
                    store.close()

            sibling_view = dict(live_branch_path(restored))
            switch_llm = ScriptedLLM(["before empty"])
            switch_agent, store = resume_agent(path, io_mode="plain", llm=switch_llm)
            try:
                switch_agent.controller.config.include_job_status = False
                await switch_agent.start()
                await switch_agent.inject_input("before clear", branch_view=original)
                assert "background-finished" in str(switch_llm.last_messages)
                assert "summary SIBLING" not in str(switch_llm.last_messages)
                raw_before = list(store.get_events("settled"))
                sibling_checkpoint = store.load_branch_snapshot("settled", sibling_view)
                if notify:
                    await ClearCommand()._execute(
                        "--force", UserCommandContext(agent=switch_agent)
                    )
                else:
                    await switch_agent.rewind_to(0)
                assert prefix(switch_agent.controller.conversation.to_messages()) == []
                path = store.path
            finally:
                await switch_agent.stop()
                store.close()

            for cold_step, (cold_input, expected_provider) in enumerate(
                [
                    ("new input 1", [("user", "new input 1")]),
                    (
                        "new input 2",
                        [
                            ("user", "new input 1"),
                            ("assistant", "fresh answer 1"),
                            ("user", "new input 2"),
                        ],
                    ),
                ],
                start=1,
            ):
                fresh_llm = ScriptedLLM(
                    [
                        f"fresh answer {cold_step}",
                        "first revised",
                        "old history revised",
                    ]
                )
                fresh_agent, fresh_store = resume_agent(
                    path, io_mode="plain", llm=fresh_llm
                )
                try:
                    fresh_agent.controller.config.include_job_status = False
                    await fresh_agent.start()
                    await fresh_agent.inject_input(cold_input)
                    assert prefix(fresh_llm.last_messages) == expected_provider
                    assert fresh_agent.compact_manager._compact_count == 0
                    assert (
                        fresh_store.load_branch_snapshot("settled", sibling_view)[
                            "messages"
                        ]
                        == sibling_checkpoint["messages"]
                    )
                    assert (
                        fresh_store.get_events("settled")[: len(raw_before)]
                        == raw_before
                    )
                    if cold_step == 2:
                        first_new = next(
                            event
                            for event in fresh_store.get_events("settled")
                            if event["type"] == "user_message"
                            and event["content"] == "new input 1"
                        )
                        selector = UserMessageSelector(
                            first_new["event_id"],
                            first_new["turn_index"],
                            first_new["branch_id"],
                        )
                        boundary = fresh_agent._context_cutoff_event_id
                        if notify:
                            assert await fresh_agent.edit_and_rerun(
                                -1, "first edited", target=selector
                            )
                            expected_first = "first edited"
                        else:
                            await fresh_agent.regenerate_last_response(target=selector)
                            expected_first = "new input 1"
                        assert prefix(fresh_llm.last_messages) == [
                            ("user", expected_first)
                        ]
                        assert fresh_agent._context_cutoff_event_id == boundary
                        old_user = next(
                            event
                            for event in raw_before
                            if event["type"] == "user_message"
                        )
                        assert await fresh_agent.edit_and_rerun(
                            -1,
                            "before clear edited",
                            target=UserMessageSelector(
                                old_user["event_id"],
                                old_user["turn_index"],
                                old_user["branch_id"],
                            ),
                        )
                        assert prefix(fresh_llm.last_messages) == [
                            ("user", "before clear edited")
                        ]
                        assert fresh_agent._context_cutoff_event_id == 0
                    path = fresh_store.path
                finally:
                    await fresh_agent.stop()
                    fresh_store.close()
            assert echo.calls == 2
            assert bg.calls == 1

        for compact_origin in ("known", "null-edit", "null-rerun", "null-internal"):
            variant_path = tmp_path / compact_origin
            variant_path.mkdir()
            with monkeypatch.context() as variant_patch:
                await self._exercise_compact_tail(
                    variant_path, variant_patch, compact_origin, prefix
                )

        await exercise_compact_media_history(tmp_path, monkeypatch)
        await _exercise_user_attachment_history(tmp_path)
        await _exercise_direct_media_history(tmp_path, monkeypatch)
        await _exercise_assistant_media_history(tmp_path)
        await _exercise_settled_media_history(tmp_path)

    async def _exercise_compact_tail(
        self, tmp_path, monkeypatch, compact_origin, prefix
    ):
        """Exercise one isolated compact-tail mutation and its cold restoration."""
        compact_bg = _SlowBackgroundTool(gated=True)
        compact_bg.config.notify_controller_on_background_complete = True
        compact_llm = _HistoryReplayLLM(
            True,
            plan=[
                [NativeToolCall("b1", "slowbg", "{}")],
                "launched",
                "completion done",
                "third done",
                *(["feedback accepted"] if compact_origin != "known" else []),
                "fourth done",
            ],
        )
        async with Terrarium(session_dir=tmp_path / "compact-cold") as engine:
            creature = await engine.add_creature(
                AgentConfig(
                    name="compactcold", system_prompt="plain", tool_format="native"
                ),
                llm=compact_llm,
                tools=[compact_bg],
                io="headless",
                pwd=tmp_path,
                start=True,
            )
            agent = creature.agent
            agent.controller.config.include_job_status = False
            try:
                await creature.run("launch", timeout=8)
                await asyncio.wait_for(compact_bg.started.wait(), 2)
                await creature.run("second", timeout=8)
                async with creature.attach() as stream:
                    compact_bg.release.set()
                    while True:
                        event = await asyncio.wait_for(anext(stream), 8)
                        if (
                            isinstance(event, Activity)
                            and event.kind == "processing_end"
                        ):
                            break
                completion = agent._turn_completion
                if completion is not None:
                    await asyncio.wait_for(asyncio.shield(completion), 8)
                assert (
                    agent.controller.conversation.get_last_assistant_message().content
                    == "completion done"
                )
                await creature.run("third", timeout=8)
                if compact_origin != "known":
                    # A legal injected feedback event, not edited storage or a
                    # fabricated compact: the real producer marks origin unknown.
                    await agent.inject_event(
                        TriggerEvent(
                            type=EventType.SUBAGENT_OUTPUT,
                            content="subagent feedback before compact",
                        )
                    )
                    feedback = next(
                        message
                        for message in agent.controller.conversation.get_messages()
                        if "subagent feedback before compact" in str(message.content)
                    )
                    assert feedback.metadata["origin_known"] is False
                    assert (
                        agent.controller.conversation.get_last_assistant_message().content
                        == "feedback accepted"
                    )
                await creature.run("fourth", timeout=8)
                await agent._session_output.drain()

                manager = agent.compact_manager
                manager.config.keep_recent_turns = 1
                summary_llm = ScriptedLLM(["summary OLD_LAUNCH"])
                manager._llm = summary_llm
                assert manager.trigger_compact()
                await asyncio.wait_for(manager.wait_for_current(), 8)
                await agent._session_output.drain()
                assert manager._compact_count == 1
                compacted = prefix(agent.controller.conversation.to_messages())
                assert compacted == [
                    (
                        "assistant",
                        "[Previous context summary (compact round 1)]\n\nsummary OLD_LAUNCH",
                    ),
                    ("user", "fourth"),
                    ("assistant", "fourth done"),
                ]

                raw = agent.session_store.get_events("compactcold")
                compact_event = [
                    event for event in raw if event["type"] == "compact_complete"
                ][-1]
                assert compact_event["summary"] == "summary OLD_LAUNCH"
                expected_source = (
                    [[t, 1] for t in range(1, 4)] if compact_origin == "known" else None
                )
                assert compact_event["compact_source_path"] == expected_source
                tool_events = [
                    event
                    for event in raw
                    if event["type"] in ("tool_call", "tool_result")
                ]
                assert [event["type"] for event in tool_events] == [
                    "tool_call",
                    "tool_result",
                ]
                for event in tool_events:
                    assert (
                        compact_event["replaced_from_event_id"]
                        <= event["event_id"]
                        <= compact_event["replaced_to_event_id"]
                    ), event
                assert compact_event["replaced_to_event_id"] < compact_event["event_id"]
                assert (
                    load_persisted_branch_view(agent.session_store, "compactcold")
                    is None
                )
                compact_path = agent.session_store.path
                raw_before_resume = list(raw)
                capture_checkpoint = agent.session_store.load_branch_snapshot(
                    "compactcold", dict(live_branch_path(agent))
                )
                checkpoint_summary = next(
                    message
                    for message in capture_checkpoint["messages"]
                    if (message.get("metadata") or {}).get("compact_summary")
                )
                assert (
                    checkpoint_summary["metadata"]["compact_source_path"]
                    == expected_source
                )
                assert compact_event["event_id"] <= capture_checkpoint["watermark"]
            finally:
                compact_bg.release.set()

        resumed_llm = ScriptedLLM(
            [
                "after compact",
                "edited tail",
                "rerun tail",
                "edited fourth",
                "rerun fourth",
                "edited launch",
                "switch A reply",
            ]
        )
        replayed_ids = []
        original_replay = branch_history.replay_with_settled_tools

        def observe_replay(events, **kwargs):
            replayed_ids.extend(evt["event_id"] for evt in events if "event_id" in evt)
            return original_replay(events, **kwargs)

        # With only the captured checkpoint, cold restore must not replay its prefix.
        with monkeypatch.context() as observation:
            observation.setattr(
                branch_history, "replay_with_settled_tools", observe_replay
            )
            observation.setattr(
                resume_history, "replay_with_settled_tools", observe_replay
            )
            observation.setattr(
                resume_branch, "replay_with_settled_tools", observe_replay
            )
            restored, restored_store = resume_agent(
                compact_path, io_mode="plain", llm=resumed_llm
            )
        try:
            assert all(
                event_id > capture_checkpoint["watermark"] for event_id in replayed_ids
            )
            await restored.start()
            restored.controller.config.include_job_status = False
            await restored.inject_input("after reopen")
            assert prefix(resumed_llm.last_messages) == compacted + [
                ("user", "after reopen")
            ]
            assert prefix(restored.controller.conversation.to_messages()) == (
                compacted + [("user", "after reopen"), ("assistant", "after compact")]
            )
            assert compact_bg.calls == 1
            raw_after = restored_store.get_events("compactcold")
            assert raw_after[: len(raw_before_resume)] == raw_before_resume
            appended = raw_after[len(raw_before_resume) :]
            assert not any(
                event["type"] in ("tool_call", "tool_result", "compact_complete")
                for event in appended
            ), appended
            assert [
                event["type"] for event in appended if event["type"] == "user_message"
            ] == ["user_message"]
            original_view = dict(live_branch_path(restored))
            # ScriptedLLM observes message dictionaries before provider adaptation;
            # this is not a wire-byte, provider-kwargs, or cache-hit assertion.
            model_prefix = copy.deepcopy(resumed_llm.last_messages[:-1])
            tail = next(
                evt
                for evt in appended
                if evt["type"] == "user_message" and evt["content"] == "after reopen"
            )
            assert tail["event_id"] > capture_checkpoint["watermark"]
            assert compact_event["turn_index"] < tail["turn_index"]
            assert tail["event_id"] not in {
                event["event_id"] for event in raw_before_resume
            }
            tail_selector = UserMessageSelector(
                tail["event_id"], tail["turn_index"], tail["branch_id"]
            )
            if compact_origin != "known":
                # Each variant starts from its own unmodified persisted branch.
                if compact_origin == "null-internal":
                    launch = next(
                        event
                        for event in raw_before_resume
                        if event["type"] == "user_message"
                        and event["content"] == "launch"
                    )
                    assert await restored.edit_and_rerun(
                        -1,
                        "launch edited",
                        target=UserMessageSelector(
                            launch["event_id"],
                            launch["turn_index"],
                            launch["branch_id"],
                        ),
                        branch_view=original_view,
                    )
                    assert prefix(resumed_llm.last_messages) == [
                        ("user", "launch edited")
                    ]
                    assert "OLD_LAUNCH" not in str(resumed_llm.last_messages)
                    assert restored.compact_manager._compact_count == 0
                    expected_request = resumed_llm.last_messages
                else:
                    if compact_origin == "null-edit":
                        assert await restored.edit_and_rerun(
                            -1,
                            "after reopen edited",
                            target=tail_selector,
                            branch_view=original_view,
                        )
                        target_content = "after reopen edited"
                    else:
                        await restored.regenerate_last_response(
                            target=tail_selector, branch_view=original_view
                        )
                        target_content = "after reopen"
                    expected_request = model_prefix + [
                        {"role": "user", "content": target_content}
                    ]
                assert summary_llm.call_count == 1
                evidence = {
                    "operation": compact_origin,
                    "compact_source_path": compact_event["compact_source_path"],
                    "compact_event_id": compact_event["event_id"],
                    "compact_turn": compact_event["turn_index"],
                    "checkpoint_watermark": capture_checkpoint["watermark"],
                    "target_event_id": tail["event_id"],
                    "target_turn": tail["turn_index"],
                    "expected_request": expected_request,
                    "actual_request": resumed_llm.last_messages,
                    "restored_compact_count": restored.compact_manager._compact_count,
                }
                print(json.dumps(evidence, indent=2))
                assert resumed_llm.last_messages == expected_request, evidence
                if compact_origin == "null-rerun":
                    # Replayed users legitimately carry IDs retained by implicit regen.
                    event = next(
                        event
                        for event in reversed(restored_store.get_events("compactcold"))
                        if event["type"] == "user_message"
                    )
                    conversation = restored.controller.conversation
                    conversation.get_messages()[
                        conversation.find_last_user_index()
                    ].metadata.update(
                        replay_conversation([event], include_metadata=True)[0][
                            "metadata"
                        ]
                    )
                    for _ in range(2):
                        await restored.regenerate_last_response()
                        assert resumed_llm.last_messages == expected_request
                if compact_origin != "null-internal":
                    mutated_view = dict(live_branch_path(restored))
                    await restored.inject_input(
                        "visit original", branch_view=original_view
                    )
                    assert (
                        resumed_llm.last_messages[: len(model_prefix)] == model_prefix
                    )
                    assert [
                        message["content"]
                        for message in resumed_llm.last_messages[len(model_prefix) :]
                        if message["role"] == "user"
                    ] == ["after reopen", "visit original"]
                    await restored.inject_input(
                        "back to mutation", branch_view=mutated_view
                    )
                    assert (
                        resumed_llm.last_messages[: len(model_prefix)] == model_prefix
                    )
                    assert [
                        message["content"]
                        for message in resumed_llm.last_messages[len(model_prefix) :]
                        if message["role"] == "user"
                    ] == [target_content, "back to mutation"]
                    selected_view = dict(live_branch_path(restored))
                    await restored.stop()
                    checkpoint = restored_store.load_branch_snapshot(
                        "compactcold", selected_view
                    )
                    assert any(
                        message["role"] == "user"
                        and message["content"] == target_content
                        and message.get("metadata", {}).get("branch_id")
                        == mutated_view[tail["turn_index"]]
                        for message in checkpoint["messages"]
                    )
                    restored_store.close()
                    resumed_llm = ScriptedLLM(["continued after normal stop"])
                    restored, restored_store = resume_agent(
                        compact_path, io_mode="plain", llm=resumed_llm
                    )
                    await restored.start()
                    restored.controller.config.include_job_status = False
                    assert dict(live_branch_path(restored)) == selected_view
                    await restored.inject_input("after final reopen")
                    assert (
                        resumed_llm.last_messages[: len(model_prefix)] == model_prefix
                    )
                    assert [
                        message["content"]
                        for message in resumed_llm.last_messages[len(model_prefix) :]
                        if message["role"] == "user"
                    ] == [target_content, "back to mutation", "after final reopen"]
                    assert summary_llm.call_count == 1
                    assert compact_bg.calls == 1
                return
            assert await restored.edit_and_rerun(
                -1,
                "after reopen edited",
                target=tail_selector,
                branch_view=original_view,
            )
            assert resumed_llm.last_messages == model_prefix + [
                {"role": "user", "content": "after reopen edited"}
            ]
            await restored.regenerate_last_response(
                target=tail_selector, branch_view=original_view
            )
            assert resumed_llm.last_messages == model_prefix + [
                {"role": "user", "content": "after reopen"}
            ]
            assert summary_llm.call_count == 1
            # Retain the pre-capture target contract as well as the new tail case.
            fourth = next(
                evt
                for evt in raw_before_resume
                if evt["type"] == "user_message" and evt["content"] == "fourth"
            )
            assert await restored.edit_and_rerun(
                -1,
                "fourth edited",
                target=UserMessageSelector(
                    fourth["event_id"], fourth["turn_index"], fourth["branch_id"]
                ),
                branch_view=original_view,
            )
            assert prefix(resumed_llm.last_messages) == [
                compacted[0],
                ("user", "fourth edited"),
            ]
            edited_view = dict(live_branch_path(restored))
            edited_fourth = next(
                evt
                for evt in reversed(restored_store.get_events("compactcold"))
                if evt["type"] == "user_message" and evt["content"] == "fourth edited"
            )
            await restored.regenerate_last_response(
                target=UserMessageSelector(
                    edited_fourth["event_id"],
                    edited_fourth["turn_index"],
                    edited_fourth["branch_id"],
                ),
                branch_view=edited_view,
            )
            assert prefix(resumed_llm.last_messages) == [
                compacted[0],
                ("user", "fourth edited"),
            ]
            launch = next(
                evt
                for evt in raw_before_resume
                if evt["type"] == "user_message" and evt["content"] == "launch"
            )
            await restored.edit_and_rerun(
                -1,
                "launch edited",
                target=UserMessageSelector(
                    launch["event_id"], launch["turn_index"], launch["branch_id"]
                ),
                branch_view=original_view,
            )
            assert prefix(resumed_llm.last_messages) == [("user", "launch edited")]
            assert "OLD_LAUNCH" not in str(resumed_llm.last_messages)
            assert restored.compact_manager._compact_count == 0
            await restored.stop()
            await restored.start()
            assert restored.compact_manager._compact_count == 0
            checkpoint = restored_store.load_branch_snapshot(
                "compactcold", original_view
            )
            prepared_snapshots = []
            original_prepare = branch_history.prepare_snapshot

            def observe_prepare(*args, **kwargs):
                prepared = original_prepare(*args, **kwargs)
                prepared_snapshots.append(copy.deepcopy(prepared))
                return prepared

            with monkeypatch.context() as observation:
                observation.setattr(branch_history, "prepare_snapshot", observe_prepare)
                await restored.inject_input("switch A", branch_view=original_view)
            # Preparation may replay tails for non-winning sibling candidates.
            # Assert the adopted checkpoint, not the aggregate speculative work.
            assert len(prepared_snapshots) == 1
            assert prepared_snapshots[0]["messages"] == checkpoint["messages"]
            assert prepared_snapshots[0]["compact_state"] == checkpoint["compact_state"]
            assert prepared_snapshots[0]["selected"] == original_view
            assert resumed_llm.last_messages[: len(model_prefix)] == model_prefix
            assert prefix(resumed_llm.last_messages) == compacted + [
                ("user", "after reopen"),
                ("assistant", "after compact"),
                ("user", "switch A"),
            ]
            assert restored.compact_manager._compact_count == 1
            counter_reads = []
            original_events = restored_store.get_events

            def observe_counter_reads(*args, **kwargs):
                counter_reads.append(args)
                return original_events(*args, **kwargs)

            with monkeypatch.context() as observation:
                observation.setattr(restored_store, "get_events", observe_counter_reads)
                restored.attach_session_store(restored_store)
            assert counter_reads == []
            assert restored.compact_manager._compact_count == 1
            assert (
                restored_store.get_events("compactcold")[: len(raw_before_resume)]
                == raw_before_resume
            )
            assert summary_llm.call_count == 1
            assert compact_bg.calls == 1
            persist_branch_view(
                restored_store, "compactcold", {turn: 1 for turn in range(1, 7)}
            )
        finally:
            await restored.stop()
            restored_store.close()
        retained_llm = ScriptedLLM(["retained A"])
        retained, retained_store = resume_agent(
            compact_path, io_mode="plain", llm=retained_llm
        )
        try:
            await retained.start()
            retained.controller.config.include_job_status = False
            assert retained.compact_manager._compact_count == 1
            await retained.inject_input("from retained A")
            assert retained_llm.last_messages[: len(model_prefix)] == model_prefix
            assert prefix(retained_llm.last_messages) == compacted + [
                ("user", "after reopen"),
                ("assistant", "after compact"),
                ("user", "switch A"),
                ("assistant", "switch A reply"),
                ("user", "from retained A"),
            ]
            await retained.rewind_to(
                retained.controller.conversation.find_last_user_index() + 1
            )
            assert [
                m["content"]
                for m in retained_store.load_branch_snapshot("compactcold", {1: 2})[
                    "messages"
                ]
                if m["role"] == "user"
            ] == ["launch edited"]

        finally:
            await retained.stop()
            retained_store.close()

    async def test_inject_event_and_unified_trigger_model(self, make_creature):
        """The unified ``TriggerEvent`` model: a non-user-input event
        (a synthetic ``tool_complete``) injected straight into the
        agent drives a full controller turn, exactly like a real
        background-tool completion would.

        Mirrors: ``Agent._on_bg_complete`` / channel-trigger fan-out —
        both reach the controller loop through ``_process_event`` with a
        non-``user_input`` ``TriggerEvent``, not through ``inject_input``.
        """
        creature, recorder = make_creature(
            script=[
                ScriptEntry("handled the completion", match="tool-output-xyz"),
                ScriptEntry("acknowledged", match="now talk"),
                # The hot-plugged trigger fires this prompt as its event
                # content; the controller answers it as a fresh turn.
                ScriptEntry("trigger handled", match="wake up please"),
            ]
        )
        agent = creature.agent

        async with Terrarium() as engine:
            await engine.add_creature(creature)

            # A tool_complete event — the same shape the executor's
            # completion callback builds for a finished background job.
            evt = create_tool_complete_event(
                job_id="bash_42", content="tool-output-xyz", exit_code=0
            )
            assert evt.type == "tool_complete"
            await agent.inject_event(evt)

            # The controller ran one turn off the injected event.
            assert agent.llm.call_count == 1
            assert "handled the completion" in _assistant_text(agent)
            # tool_complete is NOT user input: turn_index stays at 0
            # (only ``user_input`` events bump it in ``_process_event``).
            assert agent._turn_index == 0

            # And a plain user_input event through the same _process_event
            # path DOES advance the turn — proving the branch logic keys
            # on event.type, not on the entry point.
            await agent.inject_event(create_user_input_event("now talk"))
            assert agent._turn_index == 1
            assert agent.llm.call_count == 2

            # --- Hot-plug a real trigger on the running creature --------
            # ``Agent.add_trigger`` goes through the real TriggerManager:
            # it starts the trigger and spawns its ``_run_loop`` task.
            # When the trigger emits, the run loop drives ``_process_event``
            # exactly like a TimerTrigger would — the unified event model.
            trig = _OneShotTrigger("wake up please")
            trigger_id = await agent.add_trigger(trig)
            assert trigger_id.startswith("trigger_")
            # The manager reports it as a live trigger.
            info = agent.trigger_manager.get(trigger_id)
            assert info is not None
            assert info.running is True
            assert any(t.trigger_id == trigger_id for t in agent.trigger_manager.list())

            # Wait for the trigger to fire and its event to drive a turn.
            await asyncio.wait_for(trig.fired.wait(), timeout=5.0)
            await _settle(lambda: "trigger handled" in _assistant_text(agent))
            assert "trigger handled" in _assistant_text(agent)
            assert agent.llm.call_count == 3
            # The trigger-fired turn is a non-user-input event: turn_index
            # is still 1 (only ``user_input`` bumps it).
            assert agent._turn_index == 1

            # --- Remove the trigger on the running creature -------------
            removed = await agent.remove_trigger(trigger_id)
            assert removed is True
            assert agent.trigger_manager.get(trigger_id) is None
            # Removing a non-existent trigger is a clean False.
            assert await agent.remove_trigger("trigger_nonexistent") is False

            # --- post_llm_call plugin rewrites the assistant text ------
            # Register a plugin whose ``post_llm_call`` appends a suffix.
            # The controller's post-LLM chain mutates the stored
            # assistant message in place and emits an
            # ``assistant_message_edited`` activity.
            agent.plugins.register(_RewriteResponsePlugin())
            post_llm = ScriptedLLM(
                [ScriptEntry("base assistant answer", match="rewrite this")]
            )
            agent.llm = post_llm
            agent.controller.llm = post_llm
            await agent.inject_input("rewrite this please", source="chat")
            # The stored assistant message carries the plugin's suffix —
            # the rewrite landed in the conversation, not just the stream.
            assert _assistant_text(agent) == "base assistant answer [reviewed]"
            # The router surfaced the edit-marker activity.
            assert len(recorder.activities_of_type("assistant_message_edited")) >= 1

            # --- batched stackable events through one controller turn --
            # The controller batches simultaneously-queued stackable
            # events into ONE LLM round (the "multiple triggers fired at
            # once" path). Bind a fresh scripted LLM, push two
            # tool_complete events straight onto the controller queue,
            # then run a single turn: both event bodies must appear in
            # the user message the LLM saw.
            batch_llm = ScriptedLLM([ScriptEntry("batched both", match="alpha-evt")])
            agent.llm = batch_llm
            agent.controller.llm = batch_llm
            agent.controller.push_event_sync(
                create_tool_complete_event(
                    job_id="bash_a", content="alpha-evt", exit_code=0
                )
            )
            agent.controller.push_event_sync(
                create_tool_complete_event(
                    job_id="bash_b", content="beta-evt", exit_code=0
                )
            )
            async for _ in agent.controller.run_once():
                pass
            # ONE LLM round consumed BOTH queued events.
            assert batch_llm.call_count == 1
            batch_user_msgs = [
                m
                for m in agent.controller.conversation.get_messages()
                if m.role == "user"
            ]
            last_batch_user = batch_user_msgs[-1].get_text_content()
            # Both event bodies were folded into the single user message.
            assert "alpha-evt" in last_batch_user
            assert "beta-evt" in last_batch_user

            # --- multimodal user input through inject_input ------------
            # ``inject_input`` accepts a list of ContentParts; the
            # controller's ``_format_events_for_context`` keeps the text
            # part and threads image parts through as multimodal content.
            mm_llm = ScriptedLLM(
                [ScriptEntry("saw the picture", match="describe-this")]
            )
            agent.llm = mm_llm
            agent.controller.llm = mm_llm
            await agent.inject_input(
                [
                    TextPart(text="describe-this image please"),
                    ImagePart(url="data:image/png;base64,iVBORw0KGgo=", detail="low"),
                ],
                source="chat",
            )
            assert mm_llm.call_count == 1
            assert "saw the picture" in _assistant_text(agent)
            # The conversation recorded a multimodal user message — the
            # image part survived into the stored message content.
            mm_user = [
                m
                for m in agent.controller.conversation.get_messages()
                if m.role == "user" and isinstance(m.content, list)
            ]
            assert mm_user, "no multimodal user message recorded"
            assert any(isinstance(p, ImagePart) for p in mm_user[-1].content)
            # ``get_image_count`` sees exactly the one image we injected.
            assert agent.controller.conversation.get_image_count() == 1

            image_only_llm = ScriptedLLM(["received the attachment without text"])
            agent.llm = image_only_llm
            agent.controller.llm = image_only_llm
            original_format = agent.controller.config.tool_format
            agent.controller.config.tool_format = "native"
            image_only = ImagePart(url="https://example.invalid/image-only.png")
            try:
                await agent.inject_input([image_only], source="chat")
            finally:
                agent.controller.config.tool_format = original_format
            assert image_only_llm.call_log[-1][-1]["role"] == "user"
            assert (
                image_only_llm.call_log[-1][-1]["content"][-1] == image_only.to_dict()
            )
            assert "received the attachment without text" in _assistant_text(agent)

            # --- inline-file user input through inject_input -----------
            # Browser-style text uploads are materialized once for the live
            # session. The provider sees both their content and the stable
            # path that later read/write calls can use.
            file_llm = ScriptedLLM(
                [ScriptEntry("read the attached file", match="inline-file-marker")]
            )
            agent.llm = file_llm
            agent.controller.llm = file_llm
            await agent.inject_input(
                [
                    TextPart(text="please read this"),
                    FilePart(
                        name="notes.txt",
                        content="inline-file-marker: hello from the file",
                        is_inline=True,
                    ),
                ],
                source="chat",
            )
            assert file_llm.call_count == 1
            assert "read the attached file" in _assistant_text(agent)
            # The inline file content was resolved into the message the
            # provider actually saw — the ScriptedLLM matched on it.
            file_call_msgs = file_llm.call_log[-1]
            joined_file_call = " ".join(
                str(m.get("content", "")) for m in file_call_msgs
            )
            assert "inline-file-marker" in joined_file_call
            assert "Attached file: notes.txt" in joined_file_call
            text_path = next(
                part["text"].split("Path: ", 1)[1]
                for message in file_call_msgs
                if isinstance(message.get("content"), list)
                for part in message["content"]
                if isinstance(part, dict)
                and part.get("type") == "text"
                and "Attached file: notes.txt\nPath: " in part.get("text", "")
            )
            assert Path(text_path).is_file()
            assert agent._path_guard.check(text_path) is None

            # A browser-style inline image attachment is controller-owned:
            # it is materialized outside the workspace, retained for the live
            # session, and delivered through the normal ReadTool validation path.
            png_b64 = (
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8"
                "AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
            )
            image_file_llm = ScriptedLLM(
                [ScriptEntry("saw inline png", match="inspect-inline-png")]
            )
            agent.llm = image_file_llm
            agent.controller.llm = image_file_llm
            await agent.inject_input(
                [
                    TextPart(text="inspect-inline-png"),
                    FilePart(
                        name="pixel.png",
                        mime="image/png",
                        data_base64=png_b64,
                        is_inline=True,
                    ),
                ],
                source="chat",
            )
            assert image_file_llm.call_count == 1
            assert "saw inline png" in _assistant_text(agent)
            image_user_message = [
                message
                for message in image_file_llm.call_log[-1]
                if message.get("role") == "user"
            ][-1]
            image_call_parts = [
                part for part in image_user_message["content"] if isinstance(part, dict)
            ]
            # ReadTool captures the validated bytes before the next model call.
            image_ref = next(
                (
                    part["image_url"]["url"]
                    for part in image_call_parts
                    if part.get("type") == "image_url"
                ),
                None,
            )
            assert image_ref is not None, image_call_parts
            assert image_ref == "data:image/png;base64," + png_b64
            assert resolve_artifact_url(image_ref) == (
                "data:image/png;base64," + png_b64
            )
            assert not any(
                "File read failed" in str(part.get("text", ""))
                for part in image_call_parts
            )
            image_path = next(
                part["text"].split("Path: ", 1)[1]
                for part in image_call_parts
                if part.get("type") == "text"
                and "Attached file: pixel.png\nPath: " in part.get("text", "")
            )
            assert Path(image_path).is_file()
            resolved_again = await agent.controller._resolve_message_files(
                agent.controller.conversation.to_messages()
            )
            assert any(
                part.get("type") == "text"
                and part.get("text") == f"Attached file: pixel.png\nPath: {image_path}"
                for message in resolved_again
                if isinstance(message.get("content"), list)
                for part in message["content"]
                if isinstance(part, dict)
            )

            # --- run_event: correlated custom-event ingress (Phase E) ---
            # ``Agent.run_event`` drives a pre-built TriggerEvent (a Drive
            # delivery shape) and returns a correlated TurnResult whose
            # ``correlation_id`` echoes the event's ``delivery_id`` through
            # context -> finalization -> result. It is NOT user input, so
            # ``turn_index`` is left untouched — the public ingress the Drive
            # dispatcher delivers over.
            drive_llm = ScriptedLLM(
                [ScriptEntry("drive turn settled", match="correlated-ingress")]
            )
            agent.llm = drive_llm
            agent.controller.llm = drive_llm
            turn_before = agent._turn_index
            drive_event = TriggerEvent(
                type="drive_ready",
                content="correlated-ingress marker",
                context={"delivery_id": "corr-77"},
                stackable=False,
            )
            result = await agent.run_event(drive_event)
            assert result.status == "ok"
            assert result.correlation_id == "corr-77"
            assert "drive turn settled" in result.text
            assert drive_llm.call_count == 1
            assert agent._turn_index == turn_before
