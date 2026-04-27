from kohakuterrarium.core.registry import Registry
from kohakuterrarium.llm.tools import build_tool_schemas
from kohakuterrarium.mcp.tools import (
    MCPCallTool,
    MCPConnectTool,
    MCPDisconnectTool,
    MCPListTool,
)


class TestMCPSchemas:
    def test_mcp_call_schema_exposes_server_tool_args(self):
        registry = Registry()
        registry.register_tool(MCPCallTool())

        schemas = build_tool_schemas(registry)
        schema = schemas[0].parameters
        assert "server" in schema["properties"]
        assert "tool" in schema["properties"]
        assert "args" in schema["properties"]
        assert "content" not in schema["properties"]
        assert schema["required"] == ["server", "tool"]

    def test_all_meta_tools_define_real_schemas(self):
        registry = Registry()
        registry.register_tool(MCPListTool())
        registry.register_tool(MCPCallTool())
        registry.register_tool(MCPConnectTool())
        registry.register_tool(MCPDisconnectTool())

        schemas = {s.name: s.parameters for s in build_tool_schemas(registry)}
        assert "server" in schemas["mcp_list"]["properties"]
        assert "transport" in schemas["mcp_connect"]["properties"]
        assert "connect_timeout" in schemas["mcp_connect"]["properties"]
        assert "server" in schemas["mcp_disconnect"]["properties"]
