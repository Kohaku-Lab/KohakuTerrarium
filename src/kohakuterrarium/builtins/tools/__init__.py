"""
Built-in tool implementations.

All tools use the @register_builtin decorator for automatic registration.
This module imports all tool classes to trigger their registration, and
re-exports public API from tool_catalog for convenience.

Internal code should import from ``builtins.tool_catalog`` directly to
avoid pulling in all tool modules.
"""

from kohakuterrarium.builtins.tool_catalog import (
    get_builtin_tool,
    is_builtin_tool,
    list_builtin_tools,
    register_builtin,
)

# Import tools to trigger registration via @register_builtin decorator
from kohakuterrarium.builtins.tools.ask_user import AskUserTool
from kohakuterrarium.builtins.tools.bash import BashTool, PythonTool
from kohakuterrarium.builtins.tools.edit import EditTool
from kohakuterrarium.builtins.tools.glob import GlobTool
from kohakuterrarium.builtins.tools.grep import GrepTool
from kohakuterrarium.builtins.tools.image_gen import ImageGenTool
from kohakuterrarium.builtins.tools.info import InfoTool
from kohakuterrarium.builtins.tools.json_read import JsonReadTool
from kohakuterrarium.builtins.tools.json_write import JsonWriteTool
from kohakuterrarium.builtins.tools.multi_edit import MultiEditTool
from kohakuterrarium.builtins.tools.notebook_edit import NotebookEditTool
from kohakuterrarium.builtins.tools.notebook_read import NotebookReadTool
from kohakuterrarium.builtins.tools.read import ReadTool
from kohakuterrarium.builtins.tools.scratchpad_tool import ScratchpadTool
from kohakuterrarium.builtins.tools.search_memory import SearchMemoryTool
from kohakuterrarium.builtins.tools.send_message import SendMessageTool
from kohakuterrarium.builtins.tools.skill import SkillTool
from kohakuterrarium.builtins.tools.stop_task import StopTaskTool
from kohakuterrarium.builtins.tools.tree import TreeTool
from kohakuterrarium.builtins.tools.web_fetch import WebFetchTool
from kohakuterrarium.builtins.tools.web_search import WebSearchTool
from kohakuterrarium.builtins.tools.write import WriteTool
from kohakuterrarium.mcp.tools import (
    MCPCallTool,
    MCPConnectTool,
    MCPDisconnectTool,
    MCPListTool,
)

__all__ = [
    # Registry
    "register_builtin",
    "get_builtin_tool",
    "list_builtin_tools",
    "is_builtin_tool",
    # Tools
    "AskUserTool",
    "BashTool",
    "PythonTool",
    "ReadTool",
    "ScratchpadTool",
    "SearchMemoryTool",
    "SendMessageTool",
    "SkillTool",
    "WriteTool",
    "EditTool",
    "GlobTool",
    "MultiEditTool",
    "GrepTool",
    "NotebookEditTool",
    "NotebookReadTool",
    "ImageGenTool",
    "InfoTool",
    "JsonReadTool",
    "JsonWriteTool",
    "StopTaskTool",
    "TreeTool",
    "WebFetchTool",
    "WebSearchTool",
    # MCP
    "MCPListTool",
    "MCPCallTool",
    "MCPConnectTool",
    "MCPDisconnectTool",
]
