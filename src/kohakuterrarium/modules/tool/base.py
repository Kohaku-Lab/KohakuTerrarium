"""
Tool protocol and base classes.

Tools are executable functions that can be called by the controller.
Supports multimodal tool results (text + images).
"""

import traceback
from abc import abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from kohakuterrarium.builtin_skills import get_builtin_tool_doc
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from kohakuterrarium.llm.message import ContentPart


class ExecutionMode(Enum):
    """Tool execution mode."""

    DIRECT = "direct"  # Complete all jobs, return results immediately
    BACKGROUND = "background"  # Periodic status updates, context refresh
    STATEFUL = "stateful"  # Multi-turn interaction (like generators)


@dataclass
class ToolConfig:
    """
    Configuration for a tool.

    Attributes:
        timeout: Maximum execution time in seconds (0 = no timeout)
        max_output: Maximum output size in bytes (0 = no limit)
        working_dir: Working directory for execution
        env: Additional environment variables
        extra: Tool-specific configuration
    """

    timeout: float = 60.0
    max_output: int = 0
    working_dir: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolContext:
    """
    Context available to tools during execution.

    Injected by the executor for tools that opt-in via needs_context.
    """

    agent_name: str
    session: Any  # Session object - carries channels, scratchpad, extras
    working_dir: Path
    memory_path: Path | None = None
    environment: Any = None  # Environment - shared state (None for standalone agents)
    tool_format: str = "native"  # "native", "bracket", "xml", or custom
    agent: Any = None  # Agent instance - for trigger_manager access, etc.
    file_read_state: Any = None  # FileReadState - tracks which files the model has read
    path_guard: Any = None  # PathBoundaryGuard - warns/blocks access outside cwd

    @property
    def channels(self) -> Any:
        """Backward-compatible accessor for session.channels."""
        return self.session.channels if self.session else None

    def resolve_path(self, path_str: str) -> Path:
        """Resolve a path relative to the agent's working directory.

        If *path_str* is relative, it is anchored to ``self.working_dir``
        instead of the process cwd.
        """
        p = Path(path_str).expanduser()
        if not p.is_absolute():
            return (self.working_dir / p).resolve()
        return p.resolve()

    @property
    def scratchpad(self) -> Any:
        """Backward-compatible accessor for session.scratchpad."""
        return self.session.scratchpad if self.session else None


def resolve_tool_path(path_str: str, context: ToolContext | None = None) -> Path:
    """Resolve *path_str* against the agent's working directory.

    Convenience wrapper for tools that may or may not have a context.
    """
    if context:
        return context.resolve_path(path_str)
    return Path(path_str).expanduser().resolve()


@dataclass
class ToolResult:
    """
    Result from tool execution.

    Supports both text-only and multimodal output (text + images).

    Attributes:
        output: Output content - str or list of ContentPart for multimodal
        exit_code: Exit code (None if not applicable)
        error: Error message if failed
        metadata: Additional result metadata
    """

    output: "str | list[ContentPart]" = ""
    exit_code: int | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        """Check if execution was successful."""
        return self.error is None and (self.exit_code is None or self.exit_code == 0)

    def get_text_output(self) -> str:
        """
        Extract text output from result.

        For multimodal results, concatenates all text parts.
        """
        if isinstance(self.output, str):
            return self.output
        return "\n".join(
            part.text for part in self.output if getattr(part, "type", None) == "text"
        )

    def has_images(self) -> bool:
        """Check if result contains images."""
        if isinstance(self.output, str):
            return False
        return any(getattr(part, "type", None) == "image_url" for part in self.output)

    def is_multimodal(self) -> bool:
        """Check if result uses multimodal format."""
        return isinstance(self.output, list)


@runtime_checkable
class Tool(Protocol):
    """
    Protocol for tools.

    Tools must implement:
    - name: Tool identifier
    - description: One-line description for system prompt
    - execution_mode: How the tool should be executed
    - execute: Async method to run the tool
    """

    @property
    def tool_name(self) -> str:
        """Tool identifier used in tool calls."""
        ...

    @property
    def description(self) -> str:
        """One-line description for system prompt aggregation."""
        ...

    @property
    def execution_mode(self) -> ExecutionMode:
        """How this tool should be executed."""
        ...

    async def execute(
        self, args: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        """
        Execute the tool with given arguments.

        Args:
            args: Arguments parsed from tool call
            context: Optional ToolContext injected by executor

        Returns:
            ToolResult with output and status
        """
        ...


class BaseTool:
    """
    Base class for tools with common functionality.

    Subclasses should implement:
    - tool_name property
    - description property
    - _execute method
    """

    needs_context: bool = False  # Set True in subclass to receive ToolContext
    require_manual_read: bool = False  # Block usage until info tool reads the manual

    def __init__(self, config: ToolConfig | None = None):
        self.config = config or ToolConfig()
        self._manual_read = False  # Set to True after info tool reads this tool's docs

    @property
    @abstractmethod
    def tool_name(self) -> str:
        """Tool identifier."""
        raise NotImplementedError

    @property
    @abstractmethod
    def description(self) -> str:
        """One-line description."""
        raise NotImplementedError

    @property
    def execution_mode(self) -> ExecutionMode:
        """Default to background execution."""
        return ExecutionMode.BACKGROUND

    async def execute(
        self, args: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        """Execute with error handling."""
        try:
            if self.needs_context:
                result = await self._execute(args, context=context)
            else:
                result = await self._execute(args)
            # Guard: _execute must return ToolResult, not str
            if isinstance(result, str):
                logger.warning(
                    "Tool _execute returned str instead of ToolResult",
                    tool_name=self.tool_name,
                    result_preview=result[:100],
                    stack="".join(traceback.format_stack()[-4:-1]),
                )
                return ToolResult(output=result, exit_code=0)
            return result
        except Exception as e:
            return ToolResult(error=str(e))

    @abstractmethod
    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        """
        Internal execution method.

        Subclasses implement this without worrying about error handling.
        Tools that set needs_context = True will receive context as a keyword arg.
        """
        raise NotImplementedError

    def get_full_documentation(self, tool_format: str = "native") -> str:
        """
        Get full documentation for the info tool.

        Reads from builtin_skills/tools/{name}.md if available,
        otherwise returns a minimal default.

        Args:
            tool_format: "native", "bracket", "xml", or custom
        """
        doc = get_builtin_tool_doc(self.tool_name)
        if doc:
            return doc
        return f"# {self.tool_name}\n\n{self.description}\n"


@dataclass
class ToolInfo:
    """
    Tool information for registration and system prompt.

    Attributes:
        tool_name: Tool identifier
        description: One-line description
        execution_mode: Execution mode
        documentation: Full documentation (for ##info##)
    """

    tool_name: str
    description: str
    execution_mode: ExecutionMode = ExecutionMode.BACKGROUND
    documentation: str = ""

    @classmethod
    def from_tool(cls, tool: Tool) -> "ToolInfo":
        """Create ToolInfo from a Tool instance."""
        doc = ""
        if hasattr(tool, "get_full_documentation"):
            doc = tool.get_full_documentation()  # type: ignore
        return cls(
            tool_name=tool.tool_name,
            description=tool.description,
            execution_mode=tool.execution_mode,
            documentation=doc,
        )

    def to_prompt_line(self) -> str:
        """Format for system prompt tool list."""
        return f"- {self.tool_name}: {self.description}"
