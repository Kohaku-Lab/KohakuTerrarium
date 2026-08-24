"""Import built-in tools for registration and expose the public catalog API.

Internal callers that only need registry operations should import
``builtins.tool_catalog`` to avoid loading every tool implementation.
"""

from kohakuterrarium.builtins.tool_catalog import (
    get_builtin_tool,
    is_builtin_tool,
    list_builtin_tools,
    register_builtin,
)

# Import side effects populate the built-in registry.
from kohakuterrarium.builtins.tools.ask_user import AskUserTool
from kohakuterrarium.builtins.tools.bash import BashTool
from kohakuterrarium.builtins.tools.delete_trigger import DeleteTriggerTool
from kohakuterrarium.builtins.tools.python import PythonTool
from kohakuterrarium.builtins.tools.edit import EditTool
from kohakuterrarium.builtins.tools.glob import GlobTool
from kohakuterrarium.builtins.tools.grep import GrepTool
from kohakuterrarium.builtins.tools.grok_image_gen import GrokImageGenTool
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
from kohakuterrarium.builtins.tools.show_card import ShowCardTool
from kohakuterrarium.builtins.tools.skill import SkillTool
from kohakuterrarium.builtins.tools.stop_task import StopTaskTool
from kohakuterrarium.builtins.tools.tree import TreeTool
from kohakuterrarium.builtins.tools.video_gen import VideoGenTool
from kohakuterrarium.builtins.tools.web_fetch import WebFetchTool
from kohakuterrarium.builtins.tools.web_search import WebSearchTool
from kohakuterrarium.builtins.tools.write import WriteTool
from kohakuterrarium.mcp.tools import MCPCallTool
from kohakuterrarium.mcp.tools import MCPConnectTool
from kohakuterrarium.mcp.tools import MCPDisconnectTool
from kohakuterrarium.mcp.tools import MCPListTool

__all__ = [
    # Registry
    "register_builtin",
    "get_builtin_tool",
    "list_builtin_tools",
    "is_builtin_tool",
    # Tools
    "AskUserTool",
    "BashTool",
    "DeleteTriggerTool",
    "PythonTool",
    "ReadTool",
    "ScratchpadTool",
    "SearchMemoryTool",
    "SendMessageTool",
    "ShowCardTool",
    "SkillTool",
    "WriteTool",
    "EditTool",
    "GlobTool",
    "MultiEditTool",
    "GrepTool",
    "GrokImageGenTool",
    "NotebookEditTool",
    "NotebookReadTool",
    "ImageGenTool",
    "InfoTool",
    "JsonReadTool",
    "JsonWriteTool",
    "StopTaskTool",
    "TreeTool",
    "VideoGenTool",
    "WebFetchTool",
    "WebSearchTool",
    # MCP
    "MCPListTool",
    "MCPCallTool",
    "MCPConnectTool",
    "MCPDisconnectTool",
]
