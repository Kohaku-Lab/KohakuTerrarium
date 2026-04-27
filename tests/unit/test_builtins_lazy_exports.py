import importlib


def test_import_mcp_tools_module_succeeds():
    mod = importlib.import_module("kohakuterrarium.mcp.tools")
    assert mod.MCPCallTool.__name__ == "MCPCallTool"


def test_builtins_reexports_still_work():
    from kohakuterrarium.builtins import CLIInput, BUILTIN_SUBAGENTS, get_builtin_tool
    from kohakuterrarium.builtins.subagents import COORDINATOR_CONFIG
    from kohakuterrarium.builtins.tools import ImageGenTool, MCPConnectTool

    assert CLIInput is not None
    assert get_builtin_tool is not None
    assert BUILTIN_SUBAGENTS
    assert COORDINATOR_CONFIG is not None
    assert MCPConnectTool.__name__ == "MCPConnectTool"
    assert ImageGenTool.__name__ == "ImageGenTool"
