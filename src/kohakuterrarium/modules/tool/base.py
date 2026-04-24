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
        notify_controller_on_background_complete: Whether a backgrounded tool
            completion should push a new event back into the controller loop
        extra: Tool-specific configuration
    """

    timeout: float = 60.0
    max_output: int = 0
    working_dir: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    notify_controller_on_background_complete: bool = True
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

    # Provider-native tools represent capabilities the LLM provider
    # performs itself (e.g. Codex image_generation, OpenAI web_search).
    # The tool appears in the agent's inventory, but the tool runner
    # MUST NOT execute it — the provider translates the entry into a
    # built-in tool spec on the wire and surfaces results as
    # structured assistant content.
    #
    # Provider-native tools are **opt-out**: every provider declares
    # which native tools it serves via ``provider_native_tools`` on
    # the provider class, and those entries are auto-registered into
    # every creature that runs on that provider. Creatures can
    # suppress any of them via ``disable_provider_tools`` in the
    # creature config. Subclasses set ``is_provider_native = True``
    # and populate ``provider_support`` with the canonical
    # ``provider_name`` of every provider that can honor this tool;
    # an explicitly-wired tool on a non-supporting provider is
    # silently dropped at agent start.
    is_provider_native: bool = False
    provider_support: frozenset[str] = frozenset()

    # Concurrency-safety flag used by the executor to partition parallel
    # tool batches (see Cluster 5 / G.1 of the extension-point decisions).
    # Tools flagged ``False`` acquire a shared serial lock, so at most
    # one unsafe tool runs at a time while safe tools keep running in
    # parallel. Default True — the historical behavior. Flip to False
    # in subclasses that mutate shared state in ways that race each
    # other (file writes, destructive shell commands, etc.).
    is_concurrency_safe: bool = True

    # Three-bucket ordering for :meth:`prompt_contribution` output. The
    # aggregator groups contributions by bucket and then sorts
    # alphabetically *within* a bucket:
    #   - ``"first"``  → appears before the normal-alphabetical bucket
    #   - ``"normal"`` → default; sorted alphabetical by tool name
    #   - ``"last"``   → appears after the normal-alphabetical bucket
    # Unknown values fall back to ``"normal"`` with a logged warning.
    prompt_contribution_bucket: str = "normal"

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
        if self.is_provider_native:
            # Should never be reached — the tool runner is expected to
            # skip provider-native tools. Raising here fails loud so a
            # regression in the filter surfaces immediately.
            return ToolResult(
                error=(
                    f"Tool {self.tool_name!r} is provider-native and must be "
                    "handled by the LLM provider, not the tool runner."
                )
            )
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

    def prompt_contribution(self) -> str | None:
        """Optional self-described guidance, inserted into the system prompt
        once per session when the tool is registered.

        Subclasses override to return a short prose hint ("use me like
        this"). Return ``None`` to skip the contribution (default). The
        text should be kept short — full tool reference documentation
        stays behind the ``info`` tool.

        Called once at aggregation time per Cluster 5 / E.1 of the
        extension-point decisions; cached in the assembled system
        prompt (so the prefix stays stable for prompt caching).
        """
        return None


@dataclass
class ToolInfo:
    """
    Tool information for registration and system prompt.

    Attributes:
        tool_name: Tool identifier
        description: One-line description
        execution_mode: Execution mode
        documentation: Full documentation for info lookups
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
