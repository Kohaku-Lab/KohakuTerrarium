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
from kohakuterrarium.builtins.tools.bash.bash import BashTool
from kohakuterrarium.builtins.tools.media.canvas_image import CanvasImageTool
from kohakuterrarium.builtins.tools.delete_trigger import DeleteTriggerTool
from kohakuterrarium.builtins.tools.python import PythonTool
from kohakuterrarium.builtins.tools.files.edit import EditTool
from kohakuterrarium.builtins.tools.files.glob import GlobTool
from kohakuterrarium.builtins.tools.files.grep import GrepTool
from kohakuterrarium.builtins.tools.media.grok_image_gen import GrokImageGenTool
from kohakuterrarium.builtins.tools.media.image_gen import ImageGenTool
from kohakuterrarium.builtins.tools.info import InfoTool
from kohakuterrarium.builtins.tools.files.multi_edit import MultiEditTool
from kohakuterrarium.builtins.tools.notebook.edit import NotebookEditTool
from kohakuterrarium.builtins.tools.notebook.read import NotebookReadTool
from kohakuterrarium.builtins.tools.files.read import ReadTool
from kohakuterrarium.builtins.tools.scratchpad_tool import ScratchpadTool
from kohakuterrarium.builtins.tools.search_memory import SearchMemoryTool
from kohakuterrarium.builtins.tools.send_message import SendMessageTool
from kohakuterrarium.builtins.tools.app.show import ShowAppTool
from kohakuterrarium.builtins.tools.show_card import ShowCardTool
from kohakuterrarium.builtins.tools.skill import SkillTool
from kohakuterrarium.builtins.tools.stop_task import StopTaskTool
from kohakuterrarium.builtins.tools.files.tree import TreeTool
from kohakuterrarium.builtins.tools.media.video_gen import VideoGenTool
from kohakuterrarium.builtins.tools.web.fetch import WebFetchTool
from kohakuterrarium.builtins.tools.web.search import WebSearchTool
from kohakuterrarium.builtins.tools.files.write import WriteTool
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
    "CanvasImageTool",
    "DeleteTriggerTool",
    "PythonTool",
    "ReadTool",
    "ScratchpadTool",
    "SearchMemoryTool",
    "SendMessageTool",
    "ShowAppTool",
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
