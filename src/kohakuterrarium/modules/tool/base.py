"""Define tool protocols, execution context, and multimodal results."""

import traceback
from abc import abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from kohakuterrarium.builtin_skills import get_builtin_tool_doc
from kohakuterrarium.modules.tool.media_policy import MediaPolicy
from kohakuterrarium.modules.tool.runtime_options import validate_tool_options
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from kohakuterrarium.llm.message import ContentPart


class ExecutionMode(Enum):
    """Tool execution mode."""

    DIRECT = "direct"
    BACKGROUND = "background"
    STATEFUL = "stateful"


@dataclass
class ToolConfig:
    """Configure execution limits, environment, and background notification."""

    timeout: float = 60.0
    max_output: int = 256 * 1024
    working_dir: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    notify_controller_on_background_complete: bool = True
    # Per-tool documentation tier; None defers to the creature default.
    doc_mode: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolContext:
    """Provide executor-injected agent and runtime state to context-aware tools."""

    agent_name: str
    session: Any
    working_dir: Path
    memory_path: Path | None = None
    environment: Any = None
    tool_format: str = "native"
    agent: Any = None
    file_read_state: Any = None
    path_guard: Any = None
    runtime_services: dict[str, Any] = field(default_factory=dict)
    creature_id: str = ""

    @property
    def channels(self) -> Any:
        """Backward-compatible accessor for session.channels."""
        return self.session.channels if self.session else None

    def resolve_path(self, path_str: str) -> Path:
        """Resolve relative paths against the agent directory, not process cwd."""
        p = Path(path_str).expanduser()
        if not p.is_absolute():
            return (self.working_dir / p).resolve()
        return p.resolve()

    @property
    def scratchpad(self) -> Any:
        """Backward-compatible accessor for session.scratchpad."""
        return self.session.scratchpad if self.session else None


def resolve_tool_path(path_str: str, context: ToolContext | None = None) -> Path:
    """Resolve a path against agent context when one is available."""
    if context:
        return context.resolve_path(path_str)
    return Path(path_str).expanduser().resolve()


def has_interactive_responder(router: Any) -> bool:
    """Return whether a router has an output capable of replying interactively."""
    default = getattr(router, "default_output", None)
    if default is not None and getattr(default, "supports_interactive", True):
        return True
    return any(
        getattr(out, "on_supersede", None) is not None
        for out in getattr(router, "_secondary_outputs", [])
    )


@dataclass
class ToolResult:
    """Represent text or multimodal tool output and execution status."""

    output: "str | list[ContentPart]" = ""
    exit_code: int | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        """Check if execution was successful."""
        return self.error is None and (self.exit_code is None or self.exit_code == 0)

    def get_text_output(self) -> str:
        """Return text output, concatenating text parts for multimodal results."""
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
    """Define the interface required for executable controller tools."""

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
        """Execute parsed arguments with optional executor context."""
        ...


class BaseTool:
    """Provide common configuration, error handling, and documentation lookup."""

    needs_context: bool = False
    require_manual_read: bool = False

    # Native tools remain in inventory but must be executed by the provider,
    # never by the local runner. Unsupported explicit registrations are dropped.
    is_provider_native: bool = False
    provider_support: frozenset[str] = frozenset()

    @classmethod
    def provider_native_option_schema(cls) -> dict[str, dict[str, Any]]:
        """Declare UI-renderable options accepted by a provider-native tool.

        ``enum`` is reserved for provider-enforced closed sets; use suggestions
        for values that users may override. An empty schema exposes no controls.
        """
        return {}

    def runtime_option_schema(self) -> dict[str, dict[str, Any]]:
        """Declare runtime-mutable options for an ordinary local tool."""
        return {}

    # Unsafe tools share a serial lock while safe tools may remain parallel.
    is_concurrency_safe: bool = True

    # False drops the ``run_in_background`` argument from this tool's schema.
    supports_background: bool = False

    # How inline media in results is transported and shown; a result may
    # override it through ``ToolResult.metadata["media_policy"]``.
    media_policy: MediaPolicy = MediaPolicy()

    # Buckets order prompt contributions; names remain alphabetical within each bucket.
    prompt_contribution_bucket: str = "normal"

    def __init__(self, config: ToolConfig | None = None):
        self.config = config or ToolConfig()
        self._manual_read = False

    def validate_runtime_options(
        self, values: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Validate configured runtime options without changing the tool."""
        schema = self.runtime_option_schema() or {}
        source = self.config.extra if values is None else values
        selected = {key: source[key] for key in schema if key in source}
        return validate_tool_options(self.tool_name, selected, schema)

    def refresh_runtime_options(self, options: dict[str, Any]) -> None:
        """Apply validated effective options after a runtime update."""

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
            # Fail visibly if runner filtering violates the provider-native boundary.
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
            # Preserve compatibility with tools that predate the ToolResult contract.
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
        """Implement tool behavior while the base class handles failures."""
        raise NotImplementedError

    def get_full_documentation(self, tool_format: str = "native") -> str:
        """Return built-in documentation or a minimal generated fallback."""
        doc = get_builtin_tool_doc(self.tool_name)
        if doc:
            return doc
        return f"# {self.tool_name}\n\n{self.description}\n"

    def prompt_contribution(self) -> str | None:
        """Return brief system-prompt guidance, leaving full reference text to info."""
        return None


@dataclass
class ToolInfo:
    """Store tool metadata used for registration and prompt aggregation."""

    tool_name: str
    description: str
    execution_mode: ExecutionMode = ExecutionMode.BACKGROUND
    documentation: str = ""
    _tool: "Tool | None" = None

    def resolve_documentation(self) -> str:
        """Return the reference text, reading it from the tool on first need.

        Registration used to load every packaged markdown file eagerly, costing
        each agent the whole corpus at boot to serve one rare fallback.
        """
        if self.documentation:
            return self.documentation
        tool = self._tool
        if tool is None or not hasattr(tool, "get_full_documentation"):
            return ""
        return tool.get_full_documentation()  # type: ignore[attr-defined]

    @classmethod
    def from_tool(cls, tool: Tool) -> "ToolInfo":
        """Create ToolInfo from a Tool instance, deferring its documentation."""
        return cls(
            tool_name=tool.tool_name,
            description=tool.description,
            execution_mode=tool.execution_mode,
            _tool=tool,
        )

    def to_prompt_line(self) -> str:
        """Format for system prompt tool list."""
        return f"- {self.tool_name}: {self.description}"
