"""Unit tests for ``llm/tools.py`` — native tool schema builders.

The contract: ``build_tool_schemas`` turns a populated ``Registry`` into
OpenAI-compatible ``ToolSchema`` objects, using (1) the builtin schema
map, (2) the tool's own ``get_parameters_schema``, (3) a generic
fallback — and always injecting ``run_in_background``. Provider-native
tools are skipped here and surfaced by ``build_provider_native_tools``.

Tests use a real ``Registry`` with deterministic fake tools.
"""

from kohakuterrarium.core.registry import Registry
from kohakuterrarium.llm.tools import build_provider_native_tools, build_tool_schemas
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult

# ---------------------------------------------------------------------------
# Fake tools
# ---------------------------------------------------------------------------


class _BashTool(BaseTool):
    """A tool whose name matches an entry in _BUILTIN_SCHEMAS ('bash')."""

    @property
    def tool_name(self):
        return "bash"

    @property
    def description(self):
        return "Run a shell command"

    @property
    def execution_mode(self):
        return ExecutionMode.DIRECT

    async def _execute(self, args, **kwargs):
        return ToolResult(output="")


class _SchemaTool(BaseTool):
    """A tool not in _BUILTIN_SCHEMAS — uses its own get_parameters_schema."""

    @property
    def tool_name(self):
        return "my_custom_tool"

    @property
    def description(self):
        return "Custom"

    async def _execute(self, args, **kwargs):
        return ToolResult(output="")

    def get_parameters_schema(self):
        return {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }


class _NoSchemaTool(BaseTool):
    """Tool with no builtin schema and no get_parameters_schema -> generic."""

    @property
    def tool_name(self):
        return "bare_tool"

    @property
    def description(self):
        return "Bare"

    async def _execute(self, args, **kwargs):
        return ToolResult(output="")

    # explicitly remove the inherited attr so the hasattr() check fails
    get_parameters_schema = None


class _BadSchemaTool(BaseTool):
    """get_parameters_schema raises — builder must fall back to generic."""

    @property
    def tool_name(self):
        return "broken_tool"

    @property
    def description(self):
        return "Broken"

    async def _execute(self, args, **kwargs):
        return ToolResult(output="")

    def get_parameters_schema(self):
        raise RuntimeError("schema gen failed")


class _ProviderNativeTool(BaseTool):
    is_provider_native = True
    provider_support = frozenset({"codex"})

    @property
    def tool_name(self):
        return "image_gen"

    @property
    def description(self):
        return "Provider-native image generation"

    async def _execute(self, args, **kwargs):
        return ToolResult(output="")

    def get_parameters_schema(self):
        return {"type": "object", "properties": {"prompt": {"type": "string"}}}


class _FakeSubAgent:
    description = "Explores the codebase"


# ---------------------------------------------------------------------------
# build_tool_schemas
# ---------------------------------------------------------------------------


class TestBuildToolSchemas:
    def test_empty_registry_yields_no_schemas(self):
        assert build_tool_schemas(Registry()) == []

    def test_builtin_schema_used_for_known_tool(self):
        reg = Registry()
        reg.register_tool(_BashTool())
        schemas = build_tool_schemas(reg)
        assert len(schemas) == 1
        schema = schemas[0]
        assert schema.name == "bash"
        assert schema.description == "Run a shell command"
        # builtin 'bash' schema has a 'command' property + required list
        assert schema.parameters["properties"]["command"] == {
            "type": "string",
            "description": "Shell command to execute",
        }
        assert schema.parameters["required"] == ["command"]
        # run_in_background always injected
        assert schema.parameters["properties"]["run_in_background"] == {
            "type": "boolean",
            "description": (
                "If true, run in background. Results delivered later, "
                "not immediately."
            ),
        }

    def test_builtin_schema_dict_not_mutated(self):
        # docstring: "don't mutate builtin schemas"
        from kohakuterrarium.llm.tool_schemas import _BUILTIN_SCHEMAS

        before = set(_BUILTIN_SCHEMAS["bash"]["properties"].keys())
        reg = Registry()
        reg.register_tool(_BashTool())
        build_tool_schemas(reg)
        after = set(_BUILTIN_SCHEMAS["bash"]["properties"].keys())
        assert "run_in_background" not in before
        assert before == after

    def test_tool_own_schema_used_when_no_builtin(self):
        reg = Registry()
        reg.register_tool(_SchemaTool())
        schemas = build_tool_schemas(reg)
        params = schemas[0].parameters
        assert params["properties"]["query"] == {"type": "string"}
        assert params["required"] == ["query"]
        assert "run_in_background" in params["properties"]

    def test_generic_fallback_when_no_schema_available(self):
        reg = Registry()
        reg.register_tool(_NoSchemaTool())
        schemas = build_tool_schemas(reg)
        params = schemas[0].parameters
        # generic fallback shape: {content: string} + run_in_background
        assert params["properties"]["content"] == {
            "type": "string",
            "description": "Input content for the tool",
        }
        assert "run_in_background" in params["properties"]

    def test_schema_exception_falls_back_to_generic(self):
        reg = Registry()
        reg.register_tool(_BadSchemaTool())
        schemas = build_tool_schemas(reg)
        # builder swallows the exception and uses the generic schema
        assert schemas[0].parameters["properties"]["content"]["type"] == "string"

    def test_provider_native_tool_skipped(self):
        reg = Registry()
        reg.register_tool(_BashTool())
        reg.register_tool(_ProviderNativeTool())
        schemas = build_tool_schemas(reg)
        # only 'bash' — image_gen is provider-native and excluded
        assert [s.name for s in schemas] == ["bash"]

    def test_subagents_added_as_callable_functions(self):
        reg = Registry()
        reg.register_subagent("explore", _FakeSubAgent())
        schemas = build_tool_schemas(reg)
        assert len(schemas) == 1
        schema = schemas[0]
        assert schema.name == "explore"
        assert schema.description == "Explores the codebase"
        # sub-agent schema shape: task (required) + run_in_background
        assert schema.parameters["properties"]["task"]["type"] == "string"
        assert schema.parameters["required"] == ["task"]
        assert "run_in_background" in schema.parameters["properties"]

    def test_subagent_without_description_gets_default(self):
        reg = Registry()

        class _NoDesc:
            pass

        reg.register_subagent("worker", _NoDesc())
        schemas = build_tool_schemas(reg)
        assert schemas[0].description == "Sub-agent: worker"

    def test_tools_and_subagents_both_present(self):
        reg = Registry()
        reg.register_tool(_BashTool())
        reg.register_subagent("explore", _FakeSubAgent())
        names = {s.name for s in build_tool_schemas(reg)}
        assert names == {"bash", "explore"}


# ---------------------------------------------------------------------------
# build_provider_native_tools
# ---------------------------------------------------------------------------


class TestBuildProviderNativeTools:
    def test_empty_registry_returns_empty(self):
        assert build_provider_native_tools(Registry()) == []

    def test_returns_only_provider_native_tools(self):
        reg = Registry()
        reg.register_tool(_BashTool())
        native = _ProviderNativeTool()
        reg.register_tool(native)
        result = build_provider_native_tools(reg)
        assert result == [native]

    def test_non_native_tools_excluded(self):
        reg = Registry()
        reg.register_tool(_BashTool())
        reg.register_tool(_SchemaTool())
        assert build_provider_native_tools(reg) == []
