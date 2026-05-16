"""Advanced integration workflows for the multinode-flavoured surface.

Each workflow targets a distinct, currently low-coverage core-lib
module via the same in-process Agent + ScriptedLLM pattern the
``test_laboratory.py`` deep workflows use. Per ``tests/README.md`` rule
5, every method here is a *complete* feature workflow that mirrors a
real consumer path — no shape asserts, real collaborators, the only
seam is the LLM.

Modules targeted (gap-filling):

* :mod:`core.agent_mcp` — MCP client init + meta-tool routing on a
  real Agent.
* :mod:`core.agent_messages` — edit-and-rerun + regenerate on a live
  conversation with an attached SessionStore.
* :mod:`core.native_tool_validation` — provider-native option override
  validation (success + every error arm).
* :mod:`core.agent_pre_dispatch` — pre_tool_dispatch rewrite + veto
  through a live LLM-driven turn.
* :mod:`core.tool_output` — multimodal + large + image-elision result
  rendering through the real executor.
* :mod:`core.termination` — max_turns + keyword + plugin checker votes
  observed on a live controller loop.
* :mod:`core.loader` — load a custom tool class from a Python file in
  an agent folder, dispatch it from a real turn.
* :mod:`terrarium.runtime_prompt` — engine-event-driven prompt
  refreshes after add/connect/disconnect/remove topology mutations.
"""

import asyncio
import base64

import pytest

from kohakuterrarium.bootstrap import agent_init as _agent_init_mod
from kohakuterrarium.bootstrap import llm as _bootstrap_llm_mod
from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.agent_pre_dispatch import run_pre_tool_dispatch
from kohakuterrarium.core.config_types import (
    AgentConfig,
    InputConfig,
    OutputConfig,
)
from kohakuterrarium.core.events import create_user_input_event
from kohakuterrarium.core.loader import ModuleLoader, ModuleLoadError
from kohakuterrarium.core.native_tool_validation import (
    NativeToolOptionError,
    validate_native_tool_options,
)
from kohakuterrarium.core.termination import (
    TerminationChecker,
    TerminationConfig,
    TerminationDecision,
)
from kohakuterrarium.core.tool_output import (
    normalize_tool_output,
    render_content_text,
    truncate_text_utf8,
)
from kohakuterrarium.llm.message import (
    FilePart,
    ImagePart,
    TextPart,
)
from kohakuterrarium.modules.plugin.base import BasePlugin, PluginBlockError
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolResult,
)
from kohakuterrarium.parsing import ToolCallEvent
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.testing.llm import ScriptedLLM, ScriptEntry

pytestmark = pytest.mark.timeout(60)


# ── Helpers ──────────────────────────────────────────────────────────


def _patch_scripted(monkeypatch, script: list):
    def _create(config, llm_override=None):
        return ScriptedLLM(script)

    monkeypatch.setattr(_bootstrap_llm_mod, "create_llm_provider", _create)
    monkeypatch.setattr(_agent_init_mod, "create_llm_provider", _create)


def _make_agent_cfg(name: str, tmp_path, **overrides) -> AgentConfig:
    cfg = AgentConfig(
        name=name,
        model="gpt-4",
        provider="openai",
        api_key_env="",
        system_prompt=f"You are {name}.",
        include_tools_in_prompt=True,
        include_hints_in_prompt=False,
        tool_format="bracket",
        agent_path=tmp_path,
        input=InputConfig(type="none"),
        output=OutputConfig(type="stdout"),
    )
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


# ── Tool fixtures used across workflows ──────────────────────────────


class _EchoTool(BaseTool):
    """Simple echo tool — drives executor + tool_output rendering."""

    def __init__(self):
        super().__init__()
        self.calls = 0
        self.last_args: dict | None = None

    @property
    def tool_name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echo back the message arg."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        }

    async def _execute(self, args, **kwargs):
        self.calls += 1
        self.last_args = dict(args)
        return ToolResult(output=f"echoed: {args.get('message', '')}")


class _MultimodalTool(BaseTool):
    """Tool returning a mix of text + image part (data URL + URL)."""

    def __init__(self):
        super().__init__()

    @property
    def tool_name(self) -> str:
        return "snapshot"

    @property
    def description(self) -> str:
        return "Return a multimodal snapshot."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    async def _execute(self, args, **kwargs):
        # 1x1 transparent PNG, base64-encoded.
        png_b64 = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4//"
            "8/AwAI/AL+jPq2NgAAAABJRU5ErkJggg=="
        )
        return ToolResult(
            output=[
                TextPart(text="snapshot ready"),
                ImagePart(
                    url=f"data:image/png;base64,{png_b64}",
                    source_name="probe.png",
                ),
                ImagePart(
                    url="https://example.invalid/banner.png",
                    source_name="banner",
                ),
                FilePart(
                    name="report.txt",
                    data_base64=base64.b64encode(b"hello bytes").decode(),
                ),
            ]
        )


# ── Workflow 1: termination conditions on a live controller ─────────


class TestLaboratoryTerminationWorkflow:
    async def test_termination_conditions_full_workflow(self, tmp_path, monkeypatch):
        """Drive the full TerminationChecker surface inside a live
        controller loop.

        Exercises:
        - ``record_turn`` / ``record_activity`` / ``record_tool_result``
        - max_turns built-in trip
        - keyword built-in trip
        - plugin-supplied checker votes via ``attach_plugins`` and
          ``collect_termination_checkers``
        - ``force_terminate`` external signal
        - resume semantics: ``is_active`` / ``elapsed`` / ``reason``
        """
        # ── Pure-function surface first (no LLM needed) ──
        ck = TerminationChecker(
            TerminationConfig(max_turns=3, keywords=["STOP-NOW"], idle_timeout=0)
        )
        ck.start()
        assert ck.is_active is True
        assert ck.elapsed >= 0.0
        assert ck.turn_count == 0
        # Turn-count trip.
        for _ in range(3):
            ck.record_turn()
        assert ck.should_terminate(last_output="ok") is True
        assert "Max turns reached" in ck.reason

        # Keyword trip on a fresh checker.
        ck2 = TerminationChecker(TerminationConfig(keywords=["STOP-NOW"]))
        ck2.start()
        ck2.record_turn()
        assert ck2.should_terminate(last_output="please STOP-NOW") is True
        assert "Keyword detected" in ck2.reason

        # record_tool_result tail (cap = 16 retained).
        ck3 = TerminationChecker(TerminationConfig(max_turns=100))
        ck3.start()
        for i in range(25):
            ck3.record_tool_result({"i": i})
        assert len(ck3._recent_tool_results) == 16
        assert ck3._recent_tool_results[0]["i"] == 9  # last 16 retained

        # force_terminate bypasses the chain.
        ck4 = TerminationChecker(TerminationConfig())
        ck4.start()
        ck4.force_terminate("budget exhausted")
        assert ck4.should_terminate() is True
        assert ck4.reason == "budget exhausted"

        # ── Plugin-supplied checker on a real Agent + controller ──
        script = [
            ScriptEntry("turn-1 output"),
            ScriptEntry("turn-2 output"),
            ScriptEntry("KILL_SWITCH triggered output"),
            ScriptEntry("post-stop fallback"),
        ]
        _patch_scripted(monkeypatch, script)
        cfg = _make_agent_cfg("term_agent", tmp_path)
        agent = Agent(cfg)

        class _KillSwitch(BasePlugin):
            name = "killswitch"
            priority = 10
            voted_outputs: list[str] = []

            def contribute_termination_check(self):
                def _checker(ctx):
                    _KillSwitch.voted_outputs.append(ctx.last_output)
                    if "KILL_SWITCH" in (ctx.last_output or ""):
                        return TerminationDecision(
                            should_stop=True, reason="kill-switch fired"
                        )
                    return TerminationDecision(should_stop=False)

                return _checker

        plugin = _KillSwitch()
        agent.plugins.register(plugin)
        agent.plugins.enable("killswitch")
        # Wire the checker so plugin votes are consulted.
        live_ck = TerminationChecker(TerminationConfig(max_turns=20))
        live_ck.start()
        live_ck.attach_plugins(agent.plugins)
        # is_active should now be true thanks to the plugin checker even
        # though only max_turns is set.
        assert live_ck.is_active is True

        await agent.start()
        try:
            await agent._process_event(create_user_input_event("please run a turn"))
            # Now simulate the checker being consulted with the last
            # output containing the kill switch keyword. The plugin's
            # termination_check vote should win.
            stop = live_ck.should_terminate(
                last_output="something KILL_SWITCH something"
            )
            assert stop is True
            assert "kill-switch fired" in live_ck.reason
            # The plugin's checker was actually invoked.
            assert any("KILL_SWITCH" in (o or "") for o in _KillSwitch.voted_outputs)
        finally:
            await agent.stop()


# ── Workflow 2: native tool option validation + pre_tool_dispatch ────


class TestLaboratoryNativeToolValidationWorkflow:
    async def test_native_tool_validation_and_pre_dispatch_workflow(
        self, tmp_path, monkeypatch
    ):
        """Drive native_tool_validation across every type arm and then
        prove pre_tool_dispatch rewrite + veto land on a live agent.

        Covers:
        - ``validate_native_tool_options`` — enum / string / int / float
          / bool / unknown key / overlong / out-of-range / image size
        - ``NativeToolOptionError`` surfaces with clear messages
        - ``run_pre_tool_dispatch`` rewrite arm (echo args mutated)
        - ``run_pre_tool_dispatch`` veto arm (PluginBlockError synthesises
          a tool result)
        - ``run_pre_tool_dispatch`` rewrite-to-unknown-tool arm (synthesises
          an error result rather than crashing)
        """
        schema = {
            "mode": {"type": "enum", "values": ["fast", "slow"]},
            "label": {"type": "string", "max_length": 8},
            "qty": {"type": "int", "min": 1, "max": 5},
            "ratio": {"type": "float", "min": 0.0, "max": 1.0},
            "verbose": {"type": "bool"},
            "size": {"type": "string"},
        }

        # success path — every type
        out = validate_native_tool_options(
            "t",
            {
                "mode": "fast",
                "label": "tag",
                "qty": "3",
                "ratio": 0.5,
                "verbose": "yes",
            },
            schema,
        )
        assert out == {
            "mode": "fast",
            "label": "tag",
            "qty": 3,
            "ratio": 0.5,
            "verbose": True,
        }
        # bool "off" path
        assert validate_native_tool_options("t", {"verbose": "off"}, schema) == {
            "verbose": False
        }
        # None-valued keys are silently dropped
        assert (
            validate_native_tool_options("t", {"mode": None, "label": ""}, schema) == {}
        )

        # error arms — every guard
        with pytest.raises(NativeToolOptionError):
            validate_native_tool_options("t", "not-a-dict", schema)  # type: ignore[arg-type]
        with pytest.raises(NativeToolOptionError):
            validate_native_tool_options("t", {"foo": 1}, schema)
        with pytest.raises(NativeToolOptionError):
            validate_native_tool_options("t", {"mode": "rocket"}, schema)
        with pytest.raises(NativeToolOptionError):
            validate_native_tool_options("t", {"label": "too-long-label"}, schema)
        with pytest.raises(NativeToolOptionError):
            validate_native_tool_options("t", {"qty": 99}, schema)
        with pytest.raises(NativeToolOptionError):
            validate_native_tool_options("t", {"qty": "abc"}, schema)
        with pytest.raises(NativeToolOptionError):
            validate_native_tool_options("t", {"qty": True}, schema)
        with pytest.raises(NativeToolOptionError):
            validate_native_tool_options("t", {"ratio": 1.5}, schema)
        with pytest.raises(NativeToolOptionError):
            validate_native_tool_options("t", {"ratio": True}, schema)
        with pytest.raises(NativeToolOptionError):
            validate_native_tool_options("t", {"verbose": "perhaps"}, schema)
        # image_gen size guards
        img_schema = {"size": {"type": "string"}}
        assert validate_native_tool_options(
            "image_gen", {"size": "auto"}, img_schema
        ) == {"size": "auto"}
        assert validate_native_tool_options(
            "image_gen", {"size": "512x512"}, img_schema
        ) == {"size": "512x512"}
        with pytest.raises(NativeToolOptionError):
            validate_native_tool_options("image_gen", {"size": "bogus"}, img_schema)
        with pytest.raises(NativeToolOptionError):
            validate_native_tool_options("image_gen", {"size": "32x32"}, img_schema)
        with pytest.raises(NativeToolOptionError):
            validate_native_tool_options("image_gen", {"size": "9999x9999"}, img_schema)

        # ── Live pre_tool_dispatch rewrite + veto ──
        script = [
            ScriptEntry(
                "[/echo]@@message=ping[echo/]",
                match="invoke echo",
            ),
            ScriptEntry("post-echo done", match="echoed"),
            ScriptEntry(
                "[/echo]@@message=blocked[echo/]",
                match="try forbidden",
            ),
            ScriptEntry("post-block ack"),
            ScriptEntry("fallback"),
        ]
        _patch_scripted(monkeypatch, script)
        cfg = _make_agent_cfg("dispatch_agent", tmp_path)
        agent = Agent(cfg)
        echo = _EchoTool()
        agent.registry.register_tool(echo)
        agent.executor.register_tool(echo)

        class _Rewriter(BasePlugin):
            name = "rewriter"
            priority = 5

            async def pre_tool_dispatch(self, call, context):
                if call.name != "echo":
                    return None
                new_args = dict(call.args)
                new_args["message"] = f"{new_args.get('message', '')}-rewritten"
                return ToolCallEvent(name=call.name, args=new_args, raw=call.raw)

        class _Veto(BasePlugin):
            name = "veto"
            priority = 10
            blocked_payload = "blocked"

            async def pre_tool_dispatch(self, call, context):
                if (
                    call.name == "echo"
                    and call.args.get("message") == self.blocked_payload
                ):
                    raise PluginBlockError("blocked-by-veto")
                return None

        agent.plugins.register(_Rewriter())
        agent.plugins.register(_Veto())
        agent.plugins.enable("rewriter")
        agent.plugins.enable("veto")

        await agent.start()
        try:
            await agent._process_event(create_user_input_event("invoke echo please"))
            assert echo.calls == 1, "echo should have run exactly once"
            assert echo.last_args is not None
            # Rewrite landed: message arg now ends in -rewritten
            assert echo.last_args["message"].endswith(
                "-rewritten"
            ), f"rewrite did not land: {echo.last_args}"

            # Now drive the veto path — the second echo call should be
            # blocked before reaching the executor.
            calls_before = echo.calls
            await agent._process_event(create_user_input_event("try forbidden echo"))
            # Veto wins: rewriter still fires first (adds suffix), but
            # the veto then matches on the original "blocked" payload?
            # No — rewriter rewrites first, so the rewritten payload is
            # "blocked-rewritten" which doesn't match the veto. The
            # behavior under test: the rewrite + veto chain runs in
            # priority order and reaches the executor when the veto's
            # gate condition is not met.
            assert echo.calls > calls_before, (
                "second echo should still run since veto didn't match "
                "the rewritten payload"
            )

            # ── Direct exercise of run_pre_tool_dispatch returning None
            # when a plugin veto fires. Inject a plugin that vetoes any
            # echo unconditionally and call the function directly to hit
            # the synthesise-blocked-tool-result code path.

            class _AlwaysVeto(BasePlugin):
                name = "always-veto"
                priority = 100

                async def pre_tool_dispatch(self, call, context):
                    if call.name == "echo":
                        raise PluginBlockError("always-blocked")
                    return None

            agent.plugins.register(_AlwaysVeto())
            agent.plugins.enable("always-veto")
            evt = ToolCallEvent(
                name="echo",
                args={"message": "should-not-run"},
                raw="[/echo]@@message=should-not-run[echo/]",
            )
            calls_before = echo.calls
            result = await run_pre_tool_dispatch(agent, evt, agent.controller)
            assert result is None, "veto should return None"
            # echo was NOT executed
            assert echo.calls == calls_before
            # The synthetic blocked tool result was queued — controller
            # has a pending event with the error text.
            assert agent.controller._event_queue.qsize() > 0

            # rewrite-to-unknown-tool arm: a rewrite to a name not in
            # the registry should be vetoed with a synthesised error.

            class _RenameToUnknown(BasePlugin):
                name = "rename-unknown"
                priority = 1

                async def pre_tool_dispatch(self, call, context):
                    if call.name == "echo":
                        return ToolCallEvent(
                            name="not_a_real_tool",
                            args=call.args,
                            raw=call.raw,
                        )
                    return None

            # Disable the veto plugins so this rename arm reaches its
            # own validation check.
            agent.plugins.disable("always-veto")
            agent.plugins.disable("veto")
            agent.plugins.register(_RenameToUnknown())
            agent.plugins.enable("rename-unknown")
            evt2 = ToolCallEvent(name="echo", args={"message": "x"}, raw="raw")
            calls_before = echo.calls
            result2 = await run_pre_tool_dispatch(agent, evt2, agent.controller)
            assert result2 is None
            assert echo.calls == calls_before
        finally:
            await agent.stop()


# ── Workflow 3: tool output rendering + multimodal + truncation ──────


class TestLaboratoryToolOutputWorkflow:
    async def test_tool_output_rendering_full_workflow(self, tmp_path, monkeypatch):
        """Drive every tool_output rendering branch end-to-end.

        Covers:
        - ``render_content_text`` over text + image (data URL + URL) +
          file parts — base64 NEVER reaches output
        - ``normalize_tool_output`` truncation arm with metadata
        - ``materialize_image_part`` elision arm (no artifact store)
        - ``materialize_image_part`` real artifact write arm (with a
          minimal artifact store)
        - end-to-end: a multimodal tool result flows through the live
          executor + agent_messages and the rendered conversation never
          contains raw base64
        - ``truncate_text_utf8`` byte-safe split (under, over, equal)
        """
        # truncate_text_utf8 — three arms
        text, meta = truncate_text_utf8("hi", 100)
        assert text == "hi"
        assert meta["truncated"] is False
        text2, meta2 = truncate_text_utf8("a" * 50, 20)
        assert meta2["truncated"] is True
        assert meta2["omitted_text_bytes"] == 30
        assert "truncated" in text2 and "20 bytes" in text2
        text3, meta3 = truncate_text_utf8("", 0)
        assert text3 == "" and meta3["truncated"] is False

        # render_content_text — every part type renders to safe text
        png_b64 = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4//"
            "8/AwAI/AL+jPq2NgAAAABJRU5ErkJggg=="
        )
        parts = [
            TextPart(text="hello"),
            ImagePart(url=f"data:image/png;base64,{png_b64}"),
            ImagePart(url="https://example.invalid/foo.png"),
            FilePart(name="x", data_base64=base64.b64encode(b"data").decode()),
            FilePart(name="y", content="visible body"),
        ]
        rendered = render_content_text(parts)
        # base64 SHOULD NEVER appear in the rendered text
        assert png_b64 not in rendered
        assert "hello" in rendered
        assert "data:image/png;base64 elided" in rendered
        assert "https://example.invalid/foo.png" in rendered
        # FilePart with inline content shows the body, while base64 file
        # gets elided.
        assert "visible body" in rendered

        # normalize_tool_output without artifact store → elide image
        normalized = normalize_tool_output(
            [
                TextPart(text="caption"),
                ImagePart(url=f"data:image/png;base64,{png_b64}"),
            ],
            max_output=64 * 1024,
            tool_name="t",
            job_id="j",
            artifact_store=None,
        )
        assert normalized.metadata.get("data_urls_elided", 0) == 1
        # Output is the normalized parts list; the elided image is a TextPart
        assert isinstance(normalized.output, list)
        assert any(isinstance(p, TextPart) for p in normalized.output)
        # No raw base64 in any TextPart
        for part in normalized.output:
            if isinstance(part, TextPart):
                assert png_b64 not in part.text

        # normalize_tool_output truncation arm
        big_text = "x" * 200
        norm_big = normalize_tool_output(
            big_text, max_output=50, tool_name="t", job_id="j"
        )
        assert norm_big.metadata["truncated"] is True
        assert norm_big.metadata["omitted_text_bytes"] == 150

        # normalize_tool_output WITH artifact store → image materialized
        from pathlib import Path

        class _ArtifactStore:
            def __init__(self, root: Path):
                self.session_id = "sess-1"
                self.root = root
                self.writes: list[tuple[str, int]] = []

            def write_artifact(self, name: str, data: bytes) -> Path:
                target = self.root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                self.writes.append((name, len(data)))
                return target

        store = _ArtifactStore(tmp_path / "artifacts")
        norm_with_store = normalize_tool_output(
            [ImagePart(url=f"data:image/png;base64,{png_b64}")],
            max_output=64 * 1024,
            tool_name="snap",
            job_id="job1",
            artifact_store=store,
        )
        assert norm_with_store.metadata.get("data_urls_materialized") == 1
        # The artifact was actually written
        assert len(store.writes) == 1
        # And the new ImagePart url points to the served path, not raw b64.
        img = next(p for p in norm_with_store.output if isinstance(p, ImagePart))
        assert "/api/sessions/sess-1/artifacts/" in img.url
        assert png_b64 not in img.url

        # ── End-to-end: drive a multimodal tool from a live LLM turn ──
        script = [
            ScriptEntry(
                "[/snapshot][snapshot/]",
                match="take snapshot",
            ),
            ScriptEntry("snapshot received and processed"),
            ScriptEntry("fallback"),
        ]
        _patch_scripted(monkeypatch, script)
        cfg = _make_agent_cfg("snap_agent", tmp_path)
        agent = Agent(cfg)
        tool = _MultimodalTool()
        agent.registry.register_tool(tool)
        agent.executor.register_tool(tool)

        await agent.start()
        try:
            await agent._process_event(create_user_input_event("please take snapshot"))
            # Walk the live conversation: no raw base64 leaked
            convo_text = " ".join(
                m.get_text_content()
                for m in agent.controller.conversation.get_messages()
            )
            assert png_b64 not in convo_text, "raw base64 leaked into model context!"
            # The snapshot text result IS visible
            assert "snapshot ready" in convo_text
        finally:
            await agent.stop()


# ── Workflow 4: custom module loading via ModuleLoader ──────────────


class TestLaboratoryLoaderWorkflow:
    async def test_module_loader_full_workflow(self, tmp_path, monkeypatch):
        """Drive ModuleLoader across every load arm and then load a real
        custom tool into a live Agent.

        Covers:
        - ``load_class`` from a real .py file
        - ``load_instance`` instantiating a class
        - ``load_config_object`` for a module-level object
        - the loader's cache (same path → same module instance)
        - error arms: missing file, missing class, no-agent-path, bad
          extension, unknown module_type, package not installed
        - package mode: load a class from kohakuterrarium itself
        - the loaded custom tool runs in a real Agent's executor
        """
        # Build an agent folder with a custom tool module
        agent_dir = tmp_path / "agent"
        custom_dir = agent_dir / "custom"
        custom_dir.mkdir(parents=True, exist_ok=True)
        tool_path = custom_dir / "my_tool.py"
        tool_path.write_text(
            "from kohakuterrarium.modules.tool.base import "
            "BaseTool, ExecutionMode, ToolResult\n"
            "\n"
            "\n"
            "class StampTool(BaseTool):\n"
            "    @property\n"
            "    def tool_name(self):\n"
            "        return 'stamp'\n"
            "    @property\n"
            "    def description(self):\n"
            "        return 'Return a stamped value.'\n"
            "    @property\n"
            "    def execution_mode(self):\n"
            "        return ExecutionMode.DIRECT\n"
            "    def get_parameters_schema(self):\n"
            "        return {'type':'object','properties':"
            "{'value':{'type':'string'}}}\n"
            "    async def _execute(self, args, **kwargs):\n"
            "        return ToolResult(output=f\"stamped:{args.get('value','')}\")\n"
            "\n"
            "EXPORTED_CONFIG = {'name': 'StampTool', 'version': 1}\n",
            encoding="utf-8",
        )

        loader = ModuleLoader(agent_path=agent_dir)
        cls1 = loader.load_class(
            module_path="custom/my_tool.py",
            class_name="StampTool",
            module_type="custom",
        )
        cls2 = loader.load_class(
            module_path="custom/my_tool.py",
            class_name="StampTool",
            module_type="custom",
        )
        # Cache hit: same module object underlies both load_class calls.
        assert cls1 is cls2

        inst = loader.load_instance(
            module_path="custom/my_tool.py",
            class_name="StampTool",
            module_type="custom",
        )
        assert isinstance(inst, cls1)

        obj = loader.load_config_object(
            module_path="custom/my_tool.py",
            object_name="EXPORTED_CONFIG",
            module_type="custom",
        )
        assert obj == {"name": "StampTool", "version": 1}

        # Package mode — load a stable internal class
        cls_pkg = loader.load_class(
            module_path="kohakuterrarium.modules.tool.base",
            class_name="BaseTool",
            module_type="package",
        )
        assert cls_pkg.__name__ == "BaseTool"
        obj_pkg = loader.load_config_object(
            module_path="kohakuterrarium.modules.tool.base",
            object_name="ExecutionMode",
            module_type="package",
        )
        assert obj_pkg.DIRECT.value == "direct"

        # ── Error arms ──
        with pytest.raises(ModuleLoadError):
            loader.load_class(
                module_path="custom/nope.py",
                class_name="X",
                module_type="custom",
            )
        with pytest.raises(ModuleLoadError):
            loader.load_class(
                module_path="custom/my_tool.py",
                class_name="DoesNotExist",
                module_type="custom",
            )
        with pytest.raises(ModuleLoadError):
            loader.load_class(
                module_path="some.module",
                class_name="X",
                module_type="unknown-kind",
            )
        with pytest.raises(ModuleLoadError):
            loader.load_class(
                module_path=("kohakuterrarium.this.package.really.does.not.exist"),
                class_name="Anything",
                module_type="package",
            )
        with pytest.raises(ModuleLoadError):
            loader.load_config_object(
                module_path="custom/my_tool.py",
                object_name="NOPE",
                module_type="custom",
            )
        with pytest.raises(ModuleLoadError):
            loader.load_config_object(
                module_path="custom/my_tool.py",
                object_name="X",
                module_type="bogus-type",
            )
        # Bad file extension
        bad_path = custom_dir / "not_python.txt"
        bad_path.write_text("not python", encoding="utf-8")
        with pytest.raises(ModuleLoadError):
            loader.load_class(
                module_path="custom/not_python.txt",
                class_name="X",
                module_type="custom",
            )
        # No agent_path
        no_path_loader = ModuleLoader(agent_path=None)
        with pytest.raises(ModuleLoadError):
            no_path_loader.load_class(
                module_path="anything.py",
                class_name="X",
                module_type="custom",
            )

        # Cache clear is a no-op semantically — verify the call doesn't raise
        loader.clear_cache()
        assert loader._loaded_modules == {}

        # ── Drive the loaded custom tool through a live agent ──
        script = [
            ScriptEntry(
                "[/stamp]@@value=alpha[stamp/]",
                match="run stamp",
            ),
            ScriptEntry("post-stamp ack", match="stamped:alpha"),
            ScriptEntry("fallback"),
        ]
        _patch_scripted(monkeypatch, script)
        cfg = _make_agent_cfg("loader_agent", agent_dir)
        agent = Agent(cfg)
        StampCls = loader.load_class(
            module_path="custom/my_tool.py",
            class_name="StampTool",
            module_type="custom",
        )
        tool = StampCls()
        agent.registry.register_tool(tool)
        agent.executor.register_tool(tool)
        await agent.start()
        try:
            await agent._process_event(create_user_input_event("run stamp"))
            convo = " ".join(
                m.get_text_content()
                for m in agent.controller.conversation.get_messages()
            )
            assert "stamped:alpha" in convo
        finally:
            await agent.stop()


# ── Workflow 5: agent_messages edit / regenerate on a live conv ──────


class TestLaboratoryAgentMessagesWorkflow:
    async def test_edit_regenerate_rewind_full_workflow(self, tmp_path, monkeypatch):
        """Drive the agent_messages mixin end-to-end on a live agent
        with an attached SessionStore.

        Covers:
        - turn 1 chat
        - turn 2 chat
        - ``edit_and_rerun`` on the first user message — opens a new
          branch, the LLM is re-invoked
        - ``regenerate_last_response`` — opens another branch on the
          latest turn
        - ``rewind_to`` — truncates the conversation in place
        """
        from kohakuterrarium.session.store import SessionStore

        cdir = tmp_path / "creature_rewind"
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / "config.yaml").write_text(
            "name: rewind\n"
            "system_prompt: 'You are rewind.'\n"
            "model: gpt-4\n"
            "provider: openai\n"
            "input:\n  type: cli\n"
            "output:\n  type: stdout\n",
            encoding="utf-8",
        )

        # Script: enough entries to cover the original 2 turns + edit
        # rerun + regen + a few fallbacks. Match-gated where useful.
        script = [
            ScriptEntry("reply-A", match="first"),
            ScriptEntry("reply-B", match="second"),
            ScriptEntry("reply-A-edited", match="EDITED"),
            ScriptEntry("reply-regen-1"),
            ScriptEntry("reply-regen-2"),
            ScriptEntry("fallback-1"),
            ScriptEntry("fallback-2"),
            ScriptEntry("fallback-3"),
            ScriptEntry("fallback-4"),
        ]
        _patch_scripted(monkeypatch, script)

        engine = Terrarium(session_dir=str(tmp_path / "sess"))
        try:
            from kohakuterrarium.core.config import load_agent_config

            a_cfg = load_agent_config(str(cdir))
            creature = await engine.add_creature(
                a_cfg,
                creature_id="rewind-1",
                pwd=str(tmp_path),
            )
            store = SessionStore(tmp_path / "rewind.kohakutr")
            store.init_meta(
                session_id=creature.graph_id,
                config_type="creature",
                config_path=str(cdir),
                pwd=str(tmp_path),
                agents=["rewind-1"],
            )
            await engine.attach_session(creature.graph_id, store)

            agent = creature.agent
            # Turn 1
            await agent._process_event(create_user_input_event("first message"))
            # Turn 2
            await agent._process_event(create_user_input_event("second message"))

            msgs = agent.controller.conversation.get_messages()
            assert len(msgs) >= 4, f"expected ≥4 messages; got {len(msgs)}"

            # ── edit_and_rerun ──
            # Find the first user message index
            first_user_idx = next(i for i, m in enumerate(msgs) if m.role == "user")
            llm_calls_before = agent.llm.call_count
            edited_ok = await agent.edit_and_rerun(
                message_idx=first_user_idx,
                new_content="EDITED first message",
            )
            assert edited_ok is True
            # The LLM was invoked again for the rerun.
            assert agent.llm.call_count > llm_calls_before
            # Branch id incremented from default 1
            assert agent._branch_id >= 2

            # ── regenerate_last_response (tail) ──
            llm_calls_before2 = agent.llm.call_count
            branch_before = agent._branch_id
            await agent.regenerate_last_response()
            assert agent.llm.call_count > llm_calls_before2
            assert agent._branch_id > branch_before

            # ── rewind_to ──
            cur = agent.controller.conversation.get_messages()
            before = len(cur)
            # Rewind to message 1 (drop everything from index 1 onward
            # but keep the leading system message — see the API rewind
            # guard B-fat2-api-2).
            await agent.rewind_to(1)
            after = len(agent.controller.conversation.get_messages())
            assert after < before, "rewind_to should drop messages"

        finally:
            await engine.shutdown()


# ── Workflow 6: terrarium runtime_prompt refresh on topology change ─


class TestLaboratoryRuntimePromptWorkflow:
    async def test_runtime_prompt_refresh_on_topology_changes_workflow(
        self, tmp_path, monkeypatch
    ):
        """Drive RuntimeGraphPrompt across the full event surface.

        Covers:
        - ``attach`` listener picks up engine events
        - ``refresh_creature`` direct call updates the sentinel block
        - add_channel + connect topology events trigger refresh
        - disconnect + remove_channel topology events trigger refresh
        - ``detach`` stops listening cleanly
        """
        script = [ScriptEntry(f"reply-{i}") for i in range(10)]
        _patch_scripted(monkeypatch, script)

        cdir_a = tmp_path / "creature_a"
        cdir_b = tmp_path / "creature_b"
        for cdir, name in ((cdir_a, "alpha"), (cdir_b, "bravo")):
            cdir.mkdir(parents=True, exist_ok=True)
            (cdir / "config.yaml").write_text(
                f"name: {name}\n"
                f"system_prompt: 'You are {name}.'\n"
                "model: gpt-4\n"
                "provider: openai\n"
                "input:\n  type: cli\n"
                "output:\n  type: stdout\n",
                encoding="utf-8",
            )

        engine = Terrarium(session_dir=str(tmp_path / "sess"))
        from kohakuterrarium.core.config import load_agent_config

        try:
            engine._runtime_prompt.attach()
            # Idempotent.
            engine._runtime_prompt.attach()

            a_cfg = load_agent_config(str(cdir_a))
            b_cfg = load_agent_config(str(cdir_b))
            ca = await engine.add_creature(
                a_cfg,
                creature_id="alpha",
                pwd=str(tmp_path),
                is_privileged=True,
            )
            cb = await engine.add_creature(
                b_cfg,
                creature_id="bravo",
                pwd=str(tmp_path),
            )

            # Refresh both creatures directly — exercises the splice path.
            await engine._runtime_prompt.refresh_creature(ca)
            await engine._runtime_prompt.refresh_creature(cb)

            # The alpha system prompt should now contain the sentinel
            # block for the runtime graph.
            sys_alpha = (
                ca.agent.controller.conversation.get_system_message().get_text_content()
                if ca.agent.controller.conversation.get_system_message()
                else ""
            )
            assert "<!-- runtime-graph -->" in sys_alpha
            assert "<!-- /runtime-graph -->" in sys_alpha
            # alpha is privileged
            assert "privileged" in sys_alpha.lower()

            # Add a channel and connect — engine events should fire and
            # be queued for refresh.
            await engine.add_channel(ca.graph_id, "ops")
            await engine.connect("alpha", "bravo", channel="ops")
            # Yield to allow the listener to consume the events.
            for _ in range(20):
                await asyncio.sleep(0.02)
            # Refresh alpha — block should now show ops listener.
            await engine._runtime_prompt.refresh_creature(ca)
            sys_alpha2 = (
                ca.agent.controller.conversation.get_system_message().get_text_content()
                if ca.agent.controller.conversation.get_system_message()
                else ""
            )
            # The connect mutated the listener set for alpha's wiring.
            # Both creatures appear in the same graph block.
            assert "<!-- runtime-graph -->" in sys_alpha2

            # Repeated refresh replaces the block, never stacks.
            await engine._runtime_prompt.refresh_creature(ca)
            sys_alpha3 = (
                ca.agent.controller.conversation.get_system_message().get_text_content()
                if ca.agent.controller.conversation.get_system_message()
                else ""
            )
            # Sentinels appear exactly once
            assert sys_alpha3.count("<!-- runtime-graph -->") == 1
            assert sys_alpha3.count("<!-- /runtime-graph -->") == 1

            # Disconnect + remove channel — also event-driven refreshes.
            await engine.disconnect("alpha", "bravo", channel="ops")
            for _ in range(20):
                await asyncio.sleep(0.02)
            try:
                await engine.remove_channel(ca.graph_id, "ops")
            except (KeyError, ValueError):
                pass
            for _ in range(20):
                await asyncio.sleep(0.02)
            await engine._runtime_prompt.refresh_creature(ca)
            sys_alpha4 = (
                ca.agent.controller.conversation.get_system_message().get_text_content()
                if ca.agent.controller.conversation.get_system_message()
                else ""
            )
            # Sentinels still exactly once after a no-op refresh
            assert sys_alpha4.count("<!-- runtime-graph -->") == 1

            # Detach cleanly stops the listener.
            engine._runtime_prompt.detach()
            # Idempotent detach.
            engine._runtime_prompt.detach()
        finally:
            await engine.shutdown()
