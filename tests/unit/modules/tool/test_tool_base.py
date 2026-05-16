"""Unit tests for :mod:`kohakuterrarium.modules.tool.base`.

Behavior-first: every assert checks the documented contract of the tool
protocol — error wrapping, provider-native gating, multimodal result
inspection, path resolution, ToolInfo derivation.
"""

from pathlib import Path

import pytest

from kohakuterrarium.llm.message import ImagePart, TextPart
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    Tool,
    ToolConfig,
    ToolContext,
    ToolInfo,
    ToolResult,
    resolve_tool_path,
)


class _OkTool(BaseTool):
    """Minimal concrete tool that returns whatever it is told."""

    def __init__(self, result=None, config=None):
        super().__init__(config)
        self._result = result if result is not None else ToolResult(output="ok")

    @property
    def tool_name(self) -> str:
        return "ok_tool"

    @property
    def description(self) -> str:
        return "always succeeds"

    async def _execute(self, args, **kwargs):
        return self._result


class _RaisingTool(BaseTool):
    @property
    def tool_name(self) -> str:
        return "boom"

    @property
    def description(self) -> str:
        return "always raises"

    async def _execute(self, args, **kwargs):
        raise RuntimeError("kaboom")


class _StrReturningTool(BaseTool):
    @property
    def tool_name(self) -> str:
        return "stringy"

    @property
    def description(self) -> str:
        return "wrongly returns str"

    async def _execute(self, args, **kwargs):
        return "raw string output"


class _ContextTool(BaseTool):
    needs_context = True

    @property
    def tool_name(self) -> str:
        return "ctx_tool"

    @property
    def description(self) -> str:
        return "needs context"

    async def _execute(self, args, **kwargs):
        ctx = kwargs.get("context")
        # Behavior: when needs_context=True the context kwarg is forwarded.
        return ToolResult(output=f"agent={ctx.agent_name}" if ctx else "no-ctx")


class _ProviderNativeTool(BaseTool):
    is_provider_native = True

    @property
    def tool_name(self) -> str:
        return "image_generation"

    @property
    def description(self) -> str:
        return "provider native"

    async def _execute(self, args, **kwargs):  # pragma: no cover - never reached
        return ToolResult(output="should not run")


class TestBaseToolExecute:
    async def test_success_result_passed_through(self):
        tool = _OkTool(ToolResult(output="hello", exit_code=0))
        result = await tool.execute({})
        assert result.output == "hello"
        assert result.success is True

    async def test_exception_becomes_error_result(self):
        # Contract: subclasses don't worry about error handling — execute()
        # converts any exception into a ToolResult with error set.
        result = await _RaisingTool().execute({})
        assert result.error == "kaboom"
        assert result.success is False

    async def test_str_return_is_wrapped_not_dropped(self):
        # Contract: _execute MUST return ToolResult, but a stray str is
        # salvaged into a ToolResult with exit_code 0 rather than crashing.
        result = await _StrReturningTool().execute({})
        assert isinstance(result, ToolResult)
        assert result.output == "raw string output"
        assert result.exit_code == 0
        assert result.success is True

    async def test_needs_context_forwards_context(self):
        ctx = ToolContext(agent_name="swe", session=None, working_dir=Path.cwd())
        result = await _ContextTool().execute({}, context=ctx)
        assert result.output == "agent=swe"

    async def test_no_context_tool_ignores_context_arg(self):
        # needs_context defaults False — context must not be forwarded to
        # _execute (would be an unexpected kwarg).
        result = await _OkTool().execute({}, context="ignored")
        assert result.success is True

    async def test_provider_native_tool_refuses_to_run(self):
        # Contract: provider-native tools must be handled by the provider.
        # If the runner ever calls execute(), it returns a loud error
        # instead of silently running _execute.
        result = await _ProviderNativeTool().execute({})
        assert result.success is False
        assert "provider-native" in result.error


class TestToolResultInspection:
    def test_success_requires_no_error_and_zero_exit(self):
        assert ToolResult(output="x").success is True
        assert ToolResult(output="x", exit_code=0).success is True
        assert ToolResult(output="x", exit_code=3).success is False
        assert ToolResult(error="bad").success is False

    def test_get_text_output_for_plain_string(self):
        assert ToolResult(output="just text").get_text_output() == "just text"

    def test_get_text_output_concatenates_multimodal_text_parts(self):
        parts = [
            TextPart(text="line one"),
            ImagePart(url="http://x/y.png"),
            TextPart(text="line two"),
        ]
        result = ToolResult(output=parts)
        assert result.get_text_output() == "line one\nline two"

    def test_has_images_detects_image_parts(self):
        text_only = ToolResult(output=[TextPart(text="a")])
        with_image = ToolResult(output=[ImagePart(url="u")])
        assert text_only.has_images() is False
        assert with_image.has_images() is True
        assert ToolResult(output="plain").has_images() is False

    def test_is_multimodal_true_only_for_list_output(self):
        assert ToolResult(output="str").is_multimodal() is False
        assert ToolResult(output=[]).is_multimodal() is True


class TestToolContextPathResolution:
    def test_relative_path_anchored_to_working_dir(self, tmp_path):
        ctx = ToolContext(agent_name="a", session=None, working_dir=tmp_path)
        resolved = ctx.resolve_path("sub/file.txt")
        assert resolved == (tmp_path / "sub" / "file.txt").resolve()

    def test_absolute_path_left_intact(self, tmp_path):
        ctx = ToolContext(agent_name="a", session=None, working_dir=tmp_path)
        absolute = (tmp_path / "abs.txt").resolve()
        assert ctx.resolve_path(str(absolute)) == absolute

    def test_channels_and_scratchpad_proxy_session(self):
        class _Sess:
            channels = ["ch"]
            scratchpad = {"k": "v"}

        ctx = ToolContext(agent_name="a", session=_Sess(), working_dir=Path.cwd())
        assert ctx.channels == ["ch"]
        assert ctx.scratchpad == {"k": "v"}

    def test_channels_none_when_no_session(self):
        ctx = ToolContext(agent_name="a", session=None, working_dir=Path.cwd())
        assert ctx.channels is None
        assert ctx.scratchpad is None

    def test_resolve_tool_path_without_context_uses_cwd(self, tmp_path):
        # No context → resolves against process cwd, not a working_dir.
        resolved = resolve_tool_path("rel.txt", None)
        assert resolved == (Path.cwd() / "rel.txt").resolve()

    def test_resolve_tool_path_with_context_delegates(self, tmp_path):
        ctx = ToolContext(agent_name="a", session=None, working_dir=tmp_path)
        assert resolve_tool_path("rel.txt", ctx) == (tmp_path / "rel.txt").resolve()


class TestBaseToolMetadata:
    def test_default_execution_mode_is_background(self):
        assert _OkTool().execution_mode is ExecutionMode.BACKGROUND

    def test_default_config_applied_when_none(self):
        tool = _OkTool()
        assert isinstance(tool.config, ToolConfig)
        assert tool.config.timeout == 60.0

    def test_custom_config_retained(self):
        cfg = ToolConfig(timeout=5.0, max_output=100)
        assert _OkTool(config=cfg).config is cfg

    def test_prompt_contribution_defaults_none(self):
        assert _OkTool().prompt_contribution() is None

    def test_provider_native_option_schema_defaults_empty(self):
        assert BaseTool.provider_native_option_schema() == {}

    def test_full_documentation_falls_back_to_description(self):
        # Unknown tool name → no builtin doc → minimal header from description.
        doc = _OkTool().get_full_documentation()
        assert "ok_tool" in doc
        assert "always succeeds" in doc

    def test_abstract_properties_raise_without_override(self):
        # BaseTool itself can't supply tool_name / description.
        bare = BaseTool.__new__(BaseTool)
        with pytest.raises(NotImplementedError):
            _ = bare.tool_name
        with pytest.raises(NotImplementedError):
            _ = bare.description


class TestToolInfo:
    def test_from_tool_copies_identity_and_mode(self):
        info = ToolInfo.from_tool(_OkTool())
        assert info.tool_name == "ok_tool"
        assert info.description == "always succeeds"
        assert info.execution_mode is ExecutionMode.BACKGROUND
        # documentation is pulled from get_full_documentation
        assert "ok_tool" in info.documentation

    def test_to_prompt_line_format(self):
        info = ToolInfo(tool_name="bash", description="run shell")
        assert info.to_prompt_line() == "- bash: run shell"


class TestToolProtocol:
    def test_concrete_tool_satisfies_runtime_protocol(self):
        # Tool is a runtime_checkable Protocol — a fully-implemented
        # BaseTool subclass must pass isinstance.
        assert isinstance(_OkTool(), Tool)
