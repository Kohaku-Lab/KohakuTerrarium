"""
Builtins module - all built-in components for the framework.

Contains:
- tools: Built-in tool implementations (bash, python, read, write, edit, glob, grep)
- subagents: Built-in sub-agent configurations (explore, plan, memory_read, memory_write)
- inputs: Built-in input modules (cli, tui, none)
- outputs: Built-in output modules (stdout, tts)
- skills: Skill documentation files
"""

from kohakuterrarium.builtins.inputs import (
    CLIInput,
    NonBlockingCLIInput,
    TUIInput,
    create_builtin_input,
    get_builtin_input,
    is_builtin_input,
    list_builtin_inputs,
)
from kohakuterrarium.builtins.outputs import (
    ConsoleTTS,
    DummyTTS,
    PrefixedStdoutOutput,
    StdoutOutput,
    TTSConfig,
    TTSModule,
    TUIOutput,
    create_builtin_output,
    get_builtin_output,
    is_builtin_output,
    list_builtin_outputs,
)
from kohakuterrarium.builtins.subagents import (
    BUILTIN_SUBAGENTS,
    get_builtin_subagent_config,
    list_builtin_subagents,
)
from kohakuterrarium.builtins.tools import (
    BashTool,
    EditTool,
    GlobTool,
    GrepTool,
    PythonTool,
    ReadTool,
    WriteTool,
    get_builtin_tool,
    is_builtin_tool,
    list_builtin_tools,
    register_builtin,
)

__all__ = [
    # Tool registry
    "register_builtin",
    "get_builtin_tool",
    "list_builtin_tools",
    "is_builtin_tool",
    # Tool implementations
    "BashTool",
    "PythonTool",
    "ReadTool",
    "WriteTool",
    "EditTool",
    "GlobTool",
    "GrepTool",
    # Sub-agent registry
    "BUILTIN_SUBAGENTS",
    "get_builtin_subagent_config",
    "list_builtin_subagents",
    # Input registry
    "get_builtin_input",
    "is_builtin_input",
    "list_builtin_inputs",
    "create_builtin_input",
    # Input implementations
    "CLIInput",
    "NonBlockingCLIInput",
    "TUIInput",
    # Output registry
    "get_builtin_output",
    "is_builtin_output",
    "list_builtin_outputs",
    "create_builtin_output",
    # Output implementations
    "StdoutOutput",
    "PrefixedStdoutOutput",
    "TTSModule",
    "TTSConfig",
    "ConsoleTTS",
    "DummyTTS",
    "TUIOutput",
]
