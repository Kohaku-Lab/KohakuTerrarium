"""
Unit tests for native tool calling support.

Tests for:
- ToolSchema creation and API format conversion
- NativeToolCall argument parsing
- build_tool_schemas helper
- OpenAIProvider initialization and configuration
"""

from pathlib import Path

import pytest

from kohakuterrarium.builtins.tools.bash import (
    _resolve_shell_executable,
    _shell_override_env,
    _windows_git_bash_candidates,
)
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.llm.base import NativeToolCall, ToolSchema
from kohakuterrarium.llm.tools import build_tool_schemas
from kohakuterrarium.modules.tool.base import BaseTool, ToolConfig, ToolResult

# =============================================================================
# ToolSchema Tests
# =============================================================================


class TestToolSchema:
    """Tests for ToolSchema dataclass."""

    def test_default_parameters(self):
        """Test that default parameters create empty object schema."""
        schema = ToolSchema(name="test", description="A test tool")
        assert schema.parameters == {"type": "object", "properties": {}}

    def test_to_api_format_basic(self):
        """Test conversion to OpenAI API tools format."""
        schema = ToolSchema(
            name="bash",
            description="Run shell commands",
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute",
                    }
                },
                "required": ["command"],
            },
        )
        api_format = schema.to_api_format()

        assert api_format["type"] == "function"
        assert api_format["function"]["name"] == "bash"
        assert api_format["function"]["description"] == "Run shell commands"
        assert api_format["function"]["parameters"]["type"] == "object"
        assert "command" in api_format["function"]["parameters"]["properties"]
        assert api_format["function"]["parameters"]["required"] == ["command"]

    def test_to_api_format_minimal(self):
        """Test API format with minimal schema."""
        schema = ToolSchema(name="ping", description="Ping a host")
        api_format = schema.to_api_format()

        assert api_format == {
            "type": "function",
            "function": {
                "name": "ping",
                "description": "Ping a host",
                "parameters": {"type": "object", "properties": {}},
            },
        }

    def test_to_api_format_complex_parameters(self):
        """Test API format with nested parameter schemas."""
        schema = ToolSchema(
            name="search",
            description="Search files",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "default": 10},
                    "options": {
                        "type": "object",
                        "properties": {
                            "case_sensitive": {"type": "boolean"},
                        },
                    },
                },
                "required": ["query"],
            },
        )
        api_format = schema.to_api_format()
        params = api_format["function"]["parameters"]

        assert params["properties"]["query"]["type"] == "string"
        assert params["properties"]["max_results"]["default"] == 10
        assert params["properties"]["options"]["type"] == "object"


# =============================================================================
# NativeToolCall Tests
# =============================================================================


class TestNativeToolCall:
    """Tests for NativeToolCall dataclass."""

    def test_parsed_arguments_valid_json(self):
        """Test parsing valid JSON arguments."""
        tc = NativeToolCall(
            id="call_123",
            name="bash",
            arguments='{"command": "ls -la"}',
        )
        args = tc.parsed_arguments()
        assert args == {"command": "ls -la"}

    def test_parsed_arguments_empty_object(self):
        """Test parsing empty JSON object."""
        tc = NativeToolCall(id="call_456", name="ping", arguments="{}")
        args = tc.parsed_arguments()
        assert args == {}

    def test_parsed_arguments_invalid_json(self):
        """Test parsing invalid JSON falls back to _raw."""
        tc = NativeToolCall(
            id="call_789",
            name="broken",
            arguments="not valid json {",
        )
        args = tc.parsed_arguments()
        assert args == {"_raw": "not valid json {"}

    def test_parsed_arguments_complex(self):
        """Test parsing complex nested JSON arguments."""
        tc = NativeToolCall(
            id="call_abc",
            name="write_file",
            arguments='{"path": "/tmp/test.txt", "content": "hello\\nworld", "overwrite": true}',
        )
        args = tc.parsed_arguments()
        assert args["path"] == "/tmp/test.txt"
        assert args["content"] == "hello\nworld"
        assert args["overwrite"] is True


# =============================================================================
# build_tool_schemas Tests
# =============================================================================


class _DummyTool(BaseTool):
    """A minimal test tool for registry tests."""

    def __init__(self, name: str = "dummy", desc: str = "A dummy tool"):
        super().__init__(ToolConfig())
        self._name = name
        self._desc = desc

    @property
    def tool_name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._desc

    async def _execute(self, args, **kwargs):
        return ToolResult(output="ok")


class _DummyToolWithSchema(BaseTool):
    """A test tool that provides a parameters schema."""

    def __init__(self):
        super().__init__(ToolConfig())

    @property
    def tool_name(self) -> str:
        return "schema_tool"

    @property
    def description(self) -> str:
        return "Tool with custom schema"

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        }

    async def _execute(self, args, **kwargs):
        return ToolResult(output="ok")


class TestBashShellResolution:
    def test_shell_override_prefers_specific_env(self, monkeypatch):
        monkeypatch.setenv("KT_BASH_PATH", "/custom/bash")
        monkeypatch.setenv("KT_SHELL_PATH", "/generic/shell")
        monkeypatch.setenv("SHELL", "/bin/zsh")

        assert _shell_override_env("bash") == "/custom/bash"

    def test_shell_override_falls_back_to_shell_env_for_posix_shells(self, monkeypatch):
        monkeypatch.delenv("KT_BASH_PATH", raising=False)
        monkeypatch.delenv("KT_SHELL_PATH", raising=False)
        monkeypatch.setenv("SHELL", "/usr/bin/bash")

        assert _shell_override_env("bash") == "/usr/bin/bash"

    def test_windows_git_bash_candidates_include_common_locations(self, monkeypatch):
        monkeypatch.setenv("ProgramFiles", r"C:\Program Files")
        monkeypatch.setenv("ProgramFiles(x86)", r"C:\Program Files (x86)")
        monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\apoll\AppData\Local")

        candidates = [path.replace("\\", "/") for path in _windows_git_bash_candidates()]

        assert any(path.endswith("Git/bin/bash.exe") for path in candidates)
        assert any(path.endswith("Git/usr/bin/bash.exe") for path in candidates)

    def test_resolve_shell_executable_prefers_override(self, monkeypatch):
        monkeypatch.setenv("KT_BASH_PATH", "/custom/bash")
        monkeypatch.setattr(
            "kohakuterrarium.builtins.tools.bash.shutil.which", lambda exe: exe
        )
        monkeypatch.setattr("kohakuterrarium.builtins.tools.bash.sys.platform", "linux")

        assert _resolve_shell_executable("bash") == "/custom/bash"

    def test_resolve_shell_executable_prefers_git_bash_on_windows(self, monkeypatch):
        monkeypatch.delenv("KT_BASH_PATH", raising=False)
        monkeypatch.delenv("KT_SHELL_PATH", raising=False)
        monkeypatch.delenv("SHELL", raising=False)
        monkeypatch.setattr("kohakuterrarium.builtins.tools.bash.sys.platform", "win32")
        monkeypatch.setattr(
            "kohakuterrarium.builtins.tools.bash._windows_git_bash_candidates",
            lambda: [r"C:\Program Files\Git\bin\bash.exe"],
        )
        monkeypatch.setattr(
            Path,
            "exists",
            lambda self: str(self) == r"C:\Program Files\Git\bin\bash.exe",
        )
        monkeypatch.setattr(
            "kohakuterrarium.builtins.tools.bash.shutil.which", lambda exe: None
        )

        assert _resolve_shell_executable("bash") == r"C:\Program Files\Git\bin\bash.exe"


class TestBuildToolSchemas:
    """Tests for build_tool_schemas utility."""

    def test_empty_registry(self):
        """Test with no registered tools."""
        registry = Registry()
        schemas = build_tool_schemas(registry)
        assert schemas == []

    def test_single_tool_default_schema(self):
        """Test building schema for known builtin tool."""
        registry = Registry()
        registry.register_tool(_DummyTool("bash", "Execute shell commands"))

        schemas = build_tool_schemas(registry)
        assert len(schemas) == 1
        assert schemas[0].name == "bash"
        assert schemas[0].description == "Execute shell commands"
        # bash has a builtin schema with 'command' parameter
        assert schemas[0].parameters["type"] == "object"
        assert "command" in schemas[0].parameters["properties"]

    def test_unknown_tool_gets_generic_schema(self):
        """Test that unknown tool gets generic content-based schema."""
        registry = Registry()
        registry.register_tool(_DummyTool("my_custom_tool", "A custom tool"))

        schemas = build_tool_schemas(registry)
        assert len(schemas) == 1
        assert schemas[0].name == "my_custom_tool"
        # Unknown tools get fallback content-based schema
        assert "content" in schemas[0].parameters["properties"]

    def test_tool_with_custom_schema(self):
        """Test building schema for tool with get_parameters_schema."""
        registry = Registry()
        registry.register_tool(_DummyToolWithSchema())

        schemas = build_tool_schemas(registry)
        assert len(schemas) == 1
        assert schemas[0].name == "schema_tool"
        assert schemas[0].parameters["properties"]["query"]["type"] == "string"
        assert schemas[0].parameters["required"] == ["query"]

    def test_multiple_tools(self):
        """Test building schemas for multiple tools."""
        registry = Registry()
        registry.register_tool(_DummyTool("bash", "Run commands"))
        registry.register_tool(_DummyTool("read", "Read files"))
        registry.register_tool(_DummyToolWithSchema())

        schemas = build_tool_schemas(registry)
        assert len(schemas) == 3

        names = {s.name for s in schemas}
        assert names == {"bash", "read", "schema_tool"}

    def test_api_format_roundtrip(self):
        """Test that built schemas produce valid API format."""
        registry = Registry()
        registry.register_tool(_DummyToolWithSchema())

        schemas = build_tool_schemas(registry)
        api_format = schemas[0].to_api_format()

        assert api_format["type"] == "function"
        assert api_format["function"]["name"] == "schema_tool"
        assert api_format["function"]["description"] == "Tool with custom schema"
        assert "query" in api_format["function"]["parameters"]["properties"]


# =============================================================================
# OpenAIProvider Configuration Tests
# =============================================================================


class TestOpenAIProviderConfig:
    """Tests for OpenAIProvider initialization and configuration."""

    def _make_provider(self, **kwargs):
        from kohakuterrarium.llm.openai import OpenAIProvider

        defaults = {"api_key": "test-key-not-real", "model": "gpt-4o-mini"}
        defaults.update(kwargs)
        return OpenAIProvider(**defaults)

    def test_last_tool_calls_initially_empty(self):
        """Test that last_tool_calls is empty on fresh provider."""
        provider = self._make_provider()
        assert provider.last_tool_calls == []

    def test_extra_body_stored(self):
        """Test that extra_body is stored on the provider."""
        provider = self._make_provider(extra_body={"reasoning": {"enabled": True}})
        assert provider.extra_body == {"reasoning": {"enabled": True}}

    def test_extra_body_defaults_empty(self):
        """Test that extra_body defaults to empty dict."""
        provider = self._make_provider()
        assert provider.extra_body == {}

    def test_missing_api_key_raises(self):
        """Test that missing API key raises ValueError."""
        from kohakuterrarium.llm.openai import OpenAIProvider

        with pytest.raises(ValueError, match="API key is required"):
            OpenAIProvider(api_key=None)

    def test_client_is_async_openai(self):
        """Test that the internal client is AsyncOpenAI."""
        from openai import AsyncOpenAI

        provider = self._make_provider()
        assert isinstance(provider._client, AsyncOpenAI)

    def test_custom_base_url(self):
        """Test that custom base_url is passed to the client."""
        provider = self._make_provider(base_url="https://openrouter.ai/api/v1")
        assert "openrouter" in str(provider._client.base_url)
