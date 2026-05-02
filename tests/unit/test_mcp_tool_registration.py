"""Regression coverage for MCP meta-tool builtin registration."""

from kohakuterrarium.bootstrap.tools import create_tool
from kohakuterrarium.builtins.tool_catalog import get_builtin_tool, is_builtin_tool
from kohakuterrarium.core.config_types import ToolConfigItem
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.llm.tools import build_tool_schemas

MCP_META_TOOLS = ("mcp_list", "mcp_call", "mcp_connect", "mcp_disconnect")


def test_mcp_meta_tools_register_with_builtin_catalog_import():
    """Importing builtins.tools must fire MCP @register_builtin decorators.

    This is the startup path that makes ``tools: [{name: mcp_call}]``
    resolve through the same catalog as every other builtin tool.
    """
    import kohakuterrarium.builtins.tools  # noqa: F401

    for name in MCP_META_TOOLS:
        assert is_builtin_tool(name), f"{name} missing from builtin catalog"
        assert get_builtin_tool(name) is not None


def test_mcp_meta_tools_resolve_from_tool_config():
    """Creature ``tools:`` entries can instantiate MCP meta-tools."""
    import kohakuterrarium.builtins.tools  # noqa: F401

    for name in MCP_META_TOOLS:
        tool = create_tool(ToolConfigItem(name=name, type="builtin"), loader=None)
        assert tool is not None
        assert tool.tool_name == name


def test_mcp_meta_tools_have_native_function_schemas_after_config_resolution():
    """Native function calling sees the resolved MCP meta-tools too."""
    import kohakuterrarium.builtins.tools  # noqa: F401

    registry = Registry()
    for name in MCP_META_TOOLS:
        tool = create_tool(ToolConfigItem(name=name, type="builtin"), loader=None)
        assert tool is not None
        registry.register_tool(tool)

    schemas = {
        schema.name: schema.parameters for schema in build_tool_schemas(registry)
    }
    assert "server" in schemas["mcp_list"]["properties"]
    assert "server" in schemas["mcp_call"]["properties"]
    assert "tool" in schemas["mcp_call"]["properties"]
    assert "args" in schemas["mcp_call"]["properties"]
    assert "transport" in schemas["mcp_connect"]["properties"]
    assert "server" in schemas["mcp_disconnect"]["properties"]
