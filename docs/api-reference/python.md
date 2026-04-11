# Python API Reference

Comprehensive reference for all public Python APIs. For architectural context, see [Concepts](../concepts/overview.md).

## Core: Agent (`core/agent.py`)

```python
class Agent:
    @classmethod
    def from_path(
        cls,
        config_path: str,
        *,
        input_module: InputModule | None = None,
        output_module: OutputModule | None = None,
        session: Session | None = None,
        environment: Environment | None = None,
        llm_override: str | None = None,
        pwd: str | None = None,
    ) -> "Agent":
        """Create agent from config directory path."""

    def __init__(
        self,
        config: AgentConfig,
        *,
        input_module: InputModule | None = None,
        output_module: OutputModule | None = None,
        session: Session | None = None,
        environment: Environment | None = None,
        llm_override: str | None = None,
        pwd: str | None = None,
    ): ...

    async def start(self) -> None:
        """Start all modules (input, output, triggers, plugins, etc.)."""

    async def stop(self) -> None:
        """Stop all modules and cleanup."""

    async def run(self) -> None:
        """Main event loop - process inputs and triggers."""

    async def inject_input(self, text: str, source: str = "programmatic") -> None:
        """Inject user input programmatically (bypasses input module)."""

    async def inject_event(self, event: TriggerEvent) -> None:
        """Inject a custom TriggerEvent programmatically."""

    def interrupt(self) -> None:
        """Interrupt the current processing cycle immediately.

        Cancels the processing task (LLM stream, tool gather, etc.).
        The agent stays alive for the next input.
        Background sub-agents are NOT cancelled — use _cancel_job() individually.
        """

    def switch_model(self, profile_name: str) -> str:
        """Switch the LLM provider to a different model profile.

        Updates the controller, compact manager, and emits session_info.
        Returns the model identifier string of the new provider.
        """

    def set_output_handler(self, handler: Any, replace_default: bool = False) -> None:
        """Set a custom output handler callback for text chunks.

        If replace_default=True, replaces the default output. Otherwise adds
        as a secondary output module.
        """

    def attach_session_store(self, store: SessionStore) -> None:
        """Attach a SessionStore for persistent event recording.

        Wires session output, sub-agent conversation capture,
        trigger persistence, and compact manager.
        """

    def get_state(self) -> dict[str, Any]:
        """Get agent state for monitoring.

        Returns dict with: name, running, tools, subagents,
        message_count, pending_jobs.
        """

    @property
    def is_running(self) -> bool
    @property
    def tools(self) -> list[str]
    @property
    def subagents(self) -> list[str]
    @property
    def conversation_history(self) -> list[dict]
```

**Hot-plug methods:**

```python
    async def add_trigger(self, trigger: BaseTrigger, trigger_id: str | None = None) -> str:
        """Add and start a trigger on a running agent. Returns trigger_id."""

    async def remove_trigger(self, trigger_id_or_trigger: str | BaseTrigger) -> bool:
        """Stop and remove a trigger by ID or instance. Returns True if removed."""

    def update_system_prompt(self, content: str, replace: bool = False) -> None:
        """Append to (or replace) the system prompt. Takes effect on next LLM call."""

    def get_system_prompt(self) -> str:
        """Read the current system prompt text."""
```

**Internal job management:**

```python
    def _cancel_job(self, job_id: str, job_name: str) -> None:
        """Cancel a single running job by ID (tool or sub-agent).

        Tries executor first, then sub-agent manager. Called from TUI/API.
        """
```

---

## Core: Controller (`core/controller.py`)

```python
class Controller:
    def __init__(
        self,
        llm: LLMProvider,
        conversation: Conversation,
        parser: StreamParser,
        config: ControllerConfig | None = None,
    ): ...

    async def push_event(self, event: TriggerEvent) -> None:
        """Push event to the controller's queue."""

    async def run_once(self) -> AsyncIterator[ParseEvent]:
        """Run one conversation turn. Yields ParseEvents."""

    def get_job_result(self, job_id: str) -> JobResult | None
    def get_job_status(self, job_id: str) -> JobStatus | None
```

### ControllerConfig

```python
@dataclass
class ControllerConfig:
    max_messages: int = 0              # 0 = unlimited
    max_context_chars: int = 0         # 0 = unlimited
    ephemeral: bool = False            # Clear after each turn
    include_tools: bool = True
    include_hints: bool = True
    skill_mode: str = "dynamic"        # "dynamic" or "static"
    known_outputs: set[str] | None = None
```

---

## Core: Executor (`core/executor.py`)

```python
class Executor:
    def __init__(self, job_store: JobStore | None = None): ...

    def register_tool(self, tool: Tool) -> None
    async def start_tool(self, tool_name: str, args: dict, job_id: str | None = None) -> str:
        """Start tool execution (non-blocking). Returns job_id."""

    async def wait_for_direct_tools(self, job_ids: list[str], timeout: float | None = None) -> dict[str, JobResult]
    def get_status(self, job_id: str) -> JobStatus | None
    def get_result(self, job_id: str) -> JobResult | None
    def get_running_jobs(self) -> list[JobStatus]
```

---

## Core: Events (`core/events.py`)

```python
@dataclass
class TriggerEvent:
    type: str
    content: str | list[ContentPart] = ""
    context: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    job_id: str | None = None
    prompt_override: str | None = None
    stackable: bool = True

    def get_text_content(self) -> str
    def is_multimodal(self) -> bool
    def with_context(self, **kwargs) -> "TriggerEvent"

class EventType:
    USER_INPUT = "user_input"
    IDLE = "idle"
    TIMER = "timer"
    CONTEXT_UPDATE = "context_update"
    TOOL_COMPLETE = "tool_complete"
    SUBAGENT_OUTPUT = "subagent_output"
    MONITOR = "monitor"
    ERROR = "error"
    STARTUP = "startup"
    SHUTDOWN = "shutdown"
```

**Factory functions:**

```python
def create_user_input_event(content: str, source: str = "cli", **extra_context) -> TriggerEvent
def create_tool_complete_event(job_id: str, content: str, exit_code: int | None = None, error: str | None = None, **extra_context) -> TriggerEvent
def create_error_event(error_type: str, message: str, job_id: str | None = None, **extra_context) -> TriggerEvent
```

---

## Core: Job System (`core/job.py`)

```python
class JobState(Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"

class JobType(Enum):
    TOOL = "tool"
    SUBAGENT = "subagent"
    BASH = "bash"

@dataclass
class JobStatus:
    job_id: str
    job_type: JobType
    type_name: str
    state: JobState
    start_time: datetime
    duration: float | None = None
    output_lines: int = 0
    output_bytes: int = 0
    preview: str = ""
    error: str | None = None

    @property
    def is_running(self) -> bool
    @property
    def is_complete(self) -> bool
    def to_context_string(self) -> str

@dataclass
class JobResult:
    job_id: str
    output: str
    exit_code: int | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool
    def truncated(self, max_chars: int = 2000) -> str
    def get_lines(self, start: int = 0, count: int = 50) -> str

class JobStore:
    def register(self, status: JobStatus) -> None
    def update_status(self, job_id: str, state: JobState | None = None, **kwargs) -> None
    def store_result(self, result: JobResult) -> None
    def get_status(self, job_id: str) -> JobStatus | None
    def get_result(self, job_id: str) -> JobResult | None
    def get_running_jobs(self) -> list[JobStatus]
    def cleanup_old(self, max_age_seconds: float = 3600) -> int
```

---

## Core: Conversation (`core/conversation.py`)

```python
class Conversation:
    def __init__(self, config: ConversationConfig | None = None): ...

    def append(self, role: str, content: str | list[ContentPart], **kwargs) -> Message
    def to_messages(self) -> list[dict[str, Any]]
    def get_messages(self) -> list[Message]
    def get_context_length(self) -> int
    def get_last_message(self) -> Message | None
    def clear(self, keep_system: bool = True) -> None
    def to_json(self) -> str
    @classmethod
    def from_json(cls, json_str: str) -> "Conversation"

@dataclass
class ConversationConfig:
    max_messages: int = 0
    max_context_chars: int = 0
    keep_system: bool = True
```

---

## Core: Session (`core/session.py`)

```python
@dataclass
class Session:
    key: str
    channels: ChannelRegistry
    scratchpad: Scratchpad
    tui: Any | None = None
    extra: dict[str, Any]

def get_session(key: str | None = None) -> Session
def set_session(session: Session, key: str | None = None) -> None
def remove_session(key: str | None = None) -> None
def list_sessions() -> list[str]
```

---

## Core: Configuration (`core/config.py`)

### Config Loaders

```python
def load_agent_config(agent_path: str | Path) -> AgentConfig:
    """Load agent configuration from folder.

    Finds config.yaml/.yml/.json/.toml in agent_path, interpolates
    env vars, resolves base_config inheritance, loads system prompt chain.

    Raises:
        FileNotFoundError: If config file not found
        ValueError: If config is invalid
    """

def build_agent_config(config_data: dict[str, Any], agent_path: Path) -> AgentConfig:
    """Build AgentConfig from a raw config dict.

    Handles base_config inheritance, system prompt loading, template
    rendering. Used by load_agent_config (from file) and by terrarium
    runtime (inline root agent config from dict).
    """
```

### AgentConfig

```python
@dataclass
class AgentConfig:
    name: str
    version: str = "1.0"
    session_key: str | None = None
    controller: ControllerConfig
    system_prompt_file: str | None = None
    input: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    tools: list[dict[str, Any]] = field(default_factory=list)
    subagents: list[dict[str, Any]] = field(default_factory=list)
    triggers: list[dict[str, Any]] = field(default_factory=list)
    memory: dict[str, Any] | None = None
    startup_trigger: dict[str, Any] | None = None

    @classmethod
    def from_file(cls, path: Path) -> "AgentConfig"
```

---

## Core: Registry (`core/registry.py`)

```python
class Registry:
    def register_tool(self, tool: Tool) -> None
    def get_tool(self, name: str) -> Tool | None
    def get_tool_info(self, name: str) -> ToolInfo | None
    def list_tools(self) -> list[str]
    def register_subagent(self, name: str, config: Any) -> None
    def get_subagent(self, name: str) -> Any | None
    def list_subagents(self) -> list[str]
    def register_command(self, name: str, handler: Callable) -> None
    def get_command(self, name: str) -> Callable | None
    def clear(self) -> None

# Global functions
def get_registry() -> Registry
def register_tool(tool: Tool) -> None

# Decorators
@tool("my_tool")
@command("my_command")
```

---

## Modules: Input (`modules/input/base.py`)

```python
class InputModule(Protocol):
    async def start(self) -> None
    async def stop(self) -> None
    async def get_input(self) -> TriggerEvent | None

class BaseInputModule(ABC):
    @property
    def is_running(self) -> bool
    async def start(self) -> None
    async def stop(self) -> None
    async def _on_start(self) -> None        # Override in subclass
    async def _on_stop(self) -> None         # Override in subclass
    @abstractmethod
    async def get_input(self) -> TriggerEvent | None
```

---

## Modules: Output (`modules/output/`)

```python
class OutputModule(Protocol):
    async def start(self) -> None
    async def stop(self) -> None
    async def write(self, content: str) -> None
    async def write_stream(self, chunk: str) -> None
    async def flush(self) -> None
    async def on_processing_start(self) -> None

class BaseOutputModule(ABC):
    @property
    def is_running(self) -> bool
    @abstractmethod
    async def write(self, content: str) -> None
    async def write_stream(self, chunk: str) -> None  # Default calls write()
    async def flush(self) -> None                      # Default no-op

class OutputRouter:
    def __init__(self, default_output, named_outputs=None, suppress_tool_blocks=True, suppress_subagent_blocks=True): ...
    async def route(self, event: ParseEvent) -> None
    async def flush(self) -> None
    def get_output_feedback(self) -> str | None
    def get_output_targets(self) -> list[str]
    def reset(self) -> None
    def clear_all(self) -> None
```

---

## Modules: Tool (`modules/tool/base.py`)

```python
class Tool(Protocol):
    @property
    def tool_name(self) -> str
    @property
    def description(self) -> str
    @property
    def execution_mode(self) -> ExecutionMode  # DIRECT, BACKGROUND, STATEFUL
    async def execute(self, args: dict[str, Any]) -> ToolResult

class ExecutionMode(Enum):
    DIRECT = "direct"
    BACKGROUND = "background"
    STATEFUL = "stateful"

@dataclass
class ToolResult:
    output: str | list[ContentPart] = ""
    exit_code: int | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool
    def get_text_output(self) -> str
    def has_images(self) -> bool

class BaseTool:
    @property
    @abstractmethod
    def tool_name(self) -> str
    @property
    @abstractmethod
    def description(self) -> str
    @property
    def execution_mode(self) -> ExecutionMode  # Default: BACKGROUND
    async def execute(self, args: dict) -> ToolResult  # With error handling
    @abstractmethod
    async def _execute(self, args: dict) -> ToolResult
    def get_full_documentation(self) -> str

@dataclass
class ToolContext:
    agent_name: str
    session: Session
    working_dir: Path
    memory_path: Path | None = None

    @property
    def channels(self) -> ChannelRegistry
    @property
    def scratchpad(self) -> Scratchpad

@dataclass
class ToolConfig:
    timeout: float = 60.0
    max_output: int = 0
    working_dir: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
```

---

## Modules: Trigger (`modules/trigger/base.py`)

```python
class TriggerModule(Protocol):
    async def start(self) -> None
    async def stop(self) -> None
    async def wait_for_trigger(self) -> TriggerEvent | None
    def set_context(self, context: dict[str, Any]) -> None

class BaseTrigger(ABC):
    def __init__(self, prompt: str | None = None, **options): ...
    @property
    def is_running(self) -> bool
    def set_context(self, context: dict[str, Any]) -> None
    def _on_context_update(self, context: dict[str, Any]) -> None  # Override
    @abstractmethod
    async def wait_for_trigger(self) -> TriggerEvent | None
    def _create_event(self, event_type: str, content: str | None = None, context: dict | None = None) -> TriggerEvent
```

---

## Modules: Sub-Agent (`modules/subagent/`)

```python
@dataclass
class SubAgentConfig:
    name: str
    description: str = ""
    tools: list[str] = field(default_factory=list)
    system_prompt: str = ""
    prompt_file: str | None = None
    can_modify: bool = False
    stateless: bool = True
    interactive: bool = False
    context_mode: ContextUpdateMode = ContextUpdateMode.INTERRUPT_RESTART
    output_to: OutputTarget = OutputTarget.CONTROLLER
    output_module: str | None = None
    return_as_context: bool = False
    max_turns: int = 10
    timeout: float = 300.0
    model: str | None = None
    temperature: float | None = None
    memory_path: str | None = None

    def load_prompt(self, agent_path: Path | None = None) -> str
    @classmethod
    def from_dict(cls, data: dict) -> "SubAgentConfig"

class OutputTarget(Enum):
    CONTROLLER = "controller"
    EXTERNAL = "external"

class ContextUpdateMode(Enum):
    INTERRUPT_RESTART = "interrupt_restart"
    QUEUE_APPEND = "queue_append"
    FLUSH_REPLACE = "flush_replace"

@dataclass
class SubAgentResult:
    output: str = ""
    success: bool = True
    error: str | None = None
    turns: int = 0
    duration: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

class SubAgentManager:
    def __init__(
        self,
        parent_registry: Registry,
        llm: LLMProvider,
        job_store: JobStore | None = None,
        agent_path: Path | None = None,
        current_depth: int = 0,
        max_depth: int = 3,
        tool_format: str | None = None,
    ): ...

    def register(self, config: SubAgentConfig) -> None:
        """Register a sub-agent configuration."""

    def get_config(self, name: str) -> SubAgentConfig | None
    def list_subagents(self) -> list[str]

    async def spawn(
        self,
        name: str,
        task: str,
        job_id: str | None = None,
        background: bool = True,
    ) -> str:
        """Spawn a sub-agent to execute a task.

        If background=True (default), runs as background task.
        If background=False, runs synchronously and returns job_id after completion.
        Raises ValueError if depth limit reached.
        """

    async def spawn_from_event(self, event: SubAgentCallEvent) -> tuple[str, bool]:
        """Spawn sub-agent from a parsed event.

        Returns (job_id, is_background).
        """

    async def wait_for(self, job_id: str, timeout: float | None = None) -> SubAgentResult | None:
        """Wait for a sub-agent to complete. Returns None on timeout."""

    async def wait_all(self, timeout: float | None = None) -> dict[str, SubAgentResult]:
        """Wait for all running sub-agents."""

    async def cancel(self, job_id: str) -> bool:
        """Cancel a running sub-agent (cooperative + asyncio task cancel)."""

    async def cancel_all(self) -> int:
        """Cancel all running sub-agent tasks. Returns count cancelled."""

    def get_status(self, job_id: str) -> JobStatus | None
    def get_result(self, job_id: str) -> SubAgentResult | None
    def get_running_jobs(self) -> list[JobStatus]:
        """Get all running sub-agent jobs."""

    def cleanup(self, job_id: str) -> None:
        """Cleanup a completed sub-agent job."""

    def cleanup_all_completed(self) -> int:
        """Cleanup all completed jobs. Returns count cleaned."""
```

**SubAgent cancellation:**

```python
class SubAgent:
    def cancel(self) -> None:
        """Request cancellation. Checked during LLM streaming and between turns."""

    @property
    def is_running(self) -> bool
```

---

## Parsing (`parsing/`)

```python
@dataclass
class TextEvent:
    text: str

@dataclass
class ToolCallEvent:
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    raw: str = ""

@dataclass
class SubAgentCallEvent:
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    raw: str = ""

@dataclass
class CommandEvent:
    command: str
    args: str = ""

@dataclass
class OutputEvent:
    target: str
    content: str = ""

@dataclass
class BlockStartEvent:
    block_type: str
    name: str | None = None

@dataclass
class BlockEndEvent:
    block_type: str
    success: bool = True
    error: str | None = None

ParseEvent = (
    TextEvent | ToolCallEvent | SubAgentCallEvent |
    CommandEvent | OutputEvent | BlockStartEvent | BlockEndEvent
)

class StreamParser:
    def __init__(self, config: ParserConfig | None = None): ...
    def feed(self, chunk: str) -> list[ParseEvent]
    def flush(self) -> list[ParseEvent]

@dataclass
class ParserConfig:
    emit_block_events: bool = False
    buffer_text: bool = True
    text_buffer_size: int = 1
    known_tools: set[str] = field(default_factory=set)
    known_subagents: set[str] = field(default_factory=set)
    known_commands: set[str] = field(default_factory=set)
    known_outputs: set[str] = field(default_factory=set)
    content_arg_map: dict[str, str] = field(default_factory=dict)
    tool_format: ToolCallFormat = field(default_factory=lambda: BRACKET_FORMAT)
```

---

## LLM Provider (`llm/`)

```python
class LLMProvider(Protocol):
    async def chat(self, messages: list[dict], stream: bool = True, **kwargs) -> AsyncIterator[str] | ChatResponse

class BaseLLMProvider:
    @property
    def last_usage(self) -> dict[str, int]:
        """Last LLM call's token usage (prompt_tokens, completion_tokens, total_tokens)."""

class OpenAIProvider(BaseLLMProvider):
    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gpt-4o-mini",
        base_url: str = OPENAI_BASE_URL,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        timeout: float = 60.0,
        extra_headers: dict[str, str] | None = None,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ): ...

    async def close(self) -> None

class CodexProvider(BaseLLMProvider):
    """LLM provider using Codex OAuth (ChatGPT subscription)."""
    def __init__(self, model: str = "gpt-5.4"): ...
    async def close(self) -> None
```

---

## Session Persistence (`session/`)

```python
class SessionStore:
    """Persistent session storage backed by KohakuVault (.kohakutr files)."""
    def __init__(self, path: str | Path) -> None: ...

    # Event log
    def append_event(self, agent: str, event_type: str, data: dict) -> str
    def get_events(self, agent: str) -> list[dict]
    def get_all_events(self) -> list[tuple[str, dict]]

    # Conversation snapshots
    def save_conversation(self, agent: str, messages: list[dict] | str) -> None
    def load_conversation(self, agent: str) -> list[dict] | None

    # Agent state
    def save_state(self, agent: str, *, scratchpad: dict | None = None,
                   turn_count: int | None = None, token_usage: dict | None = None,
                   triggers: list[dict] | None = None,
                   compact_count: int | None = None) -> None
    def load_scratchpad(self, agent: str) -> dict
    def load_turn_count(self, agent: str) -> int
    def load_token_usage(self, agent: str) -> dict
    def load_triggers(self, agent: str) -> list[dict]:
        """Load saved resumable triggers for an agent."""

    # Channel messages
    def save_channel_message(self, channel: str, data: dict) -> str
    def get_channel_messages(self, channel: str) -> list[dict]

    # Sub-agent tracking
    def next_subagent_run(self, parent: str, name: str) -> int
    def save_subagent(self, parent: str, name: str, run: int, meta: dict,
                      conv_json: str | None = None) -> None

    # Sub-agent conversation loading
    def load_subagent_meta(self, parent: str, name: str, run: int) -> dict | None
    def load_subagent_conversation(self, parent: str, name: str, run: int) -> str | None

    # Job records
    def save_job(self, job_id: str, data: dict) -> None:
        """Save a job execution record."""
    def load_job(self, job_id: str) -> dict | None:
        """Load a job record."""

    # Session metadata
    def init_meta(self, session_id: str, config_type: str, config_path: str,
                  pwd: str, agents: list[str], **kwargs) -> None:
        """Initialize session metadata. Called once when session is created.

        kwargs: config_snapshot, terrarium_name, terrarium_channels,
        terrarium_creatures.
        """
    def load_meta(self) -> dict[str, Any]
    def update_status(self, status: str) -> None:
        """Update session status (running, paused, completed, crashed)."""
    def touch(self) -> None:
        """Update last_active timestamp."""

    # Full-text search
    def search(self, query: str, k: int = 10) -> list[dict]

    # Lifecycle
    @property
    def path(self) -> str:
        """Path to the .kohakutr file."""
    def flush(self) -> None
    def close(self, update_status: bool = True) -> None:
        """Flush and close all tables.

        Set update_status=False for read-only access to avoid
        corrupting timestamps.
        """

class SessionOutput(OutputModule):
    """Output module that records events to a SessionStore."""
    def __init__(self, agent_name: str, store: SessionStore, agent: Agent): ...

# Resume functions
def resume_agent(kt_path: str | Path) -> Agent
def resume_terrarium(kt_path: str | Path) -> TerrariumRuntime
def detect_session_type(kt_path: str | Path) -> str  # "agent" or "terrarium"
```

---

## Session Memory (`session/memory.py`)

```python
class SessionMemory:
    """Search index over session event history (FTS5 + vector)."""
    def __init__(self, db_path: str, embedder: BaseEmbedder | None = None, store: Any = None): ...

    @property
    def has_vectors(self) -> bool

    def index_events(self, agent: str, events: list[dict], start_from: int = 0) -> int:
        """Index session events into FTS + vector search. Returns new blocks indexed."""

    def search(self, query: str, mode: str = "auto", k: int = 10, agent: str | None = None) -> list[SearchResult]:
        """Search session memory. mode: "fts", "semantic", "hybrid", or "auto"."""

    def get_stats(self) -> dict[str, Any]:
        """Get index statistics (fts_blocks, vec_blocks, has_vectors, dimensions)."""

@dataclass
class SearchResult:
    content: str
    round_num: int
    block_num: int
    agent: str
    block_type: str        # "text", "tool", "trigger", "user"
    score: float
    ts: float = 0.0
    tool_name: str = ""
    channel: str = ""

    @property
    def age_str(self) -> str
```

---

## Embedding (`session/embedding.py`)

```python
class BaseEmbedder(ABC):
    dimensions: int = 0
    def encode(self, texts: list[str]) -> np.ndarray: ...
    def encode_one(self, text: str) -> np.ndarray: ...

class Model2VecEmbedder(BaseEmbedder):
    """Static embeddings (~8 MB, microsecond speed). Default provider."""
    def __init__(self, model_name: str = "minishlab/potion-base-8M"): ...

class SentenceTransformerEmbedder(BaseEmbedder):
    """HuggingFace sentence-transformers (Jina, Gemma, bge, etc.)."""
    def __init__(self, model_name: str = "google/embeddinggemma-300m", dimensions: int | None = None, device: str = "cpu"): ...

class APIEmbedder(BaseEmbedder):
    """OpenAI-compatible /v1/embeddings endpoint."""
    def __init__(self, api_key: str, model: str = "text-embedding-3-small", base_url: str = "https://api.openai.com/v1", dimensions: int | None = None): ...

class NullEmbedder(BaseEmbedder):
    """No-op. Only FTS keyword search is available."""

def create_embedder(config: dict[str, Any] | None = None) -> BaseEmbedder:
    """Create an embedder from config dict. provider: "auto" | "model2vec" | "sentence-transformer" | "api" | "none"."""
```

---

## LLM Profiles (`llm/`)

Centralized model configuration, split across three modules:

- **`llm/profiles.py`** — `LLMProfile` dataclass, profile resolution and management
- **`llm/presets.py`** — Built-in model presets and aliases (pure data)
- **`llm/api_keys.py`** — API key storage and retrieval

Profiles define complete LLM settings (provider, model, context limits, extra params). User profiles stored in `~/.kohakuterrarium/llm_profiles.yaml`, API keys in `~/.kohakuterrarium/api_keys.yaml`.

```python
# llm/profiles.py
@dataclass
class LLMProfile:
    name: str
    provider: str              # "codex-oauth" | "openai" | "anthropic"
    model: str
    max_context: int = 256000
    max_output: int = 65536
    base_url: str = ""
    api_key_env: str = ""
    temperature: float | None = None
    reasoning_effort: str = ""

    def to_dict(self) -> dict[str, Any]
    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> "LLMProfile"

# Profile management (llm/profiles.py)
def load_profiles() -> dict[str, LLMProfile]
def get_profile(name: str) -> LLMProfile | None
def get_preset(name: str) -> LLMProfile | None
def save_profile(profile: LLMProfile) -> None
def delete_profile(name: str) -> bool

# Resolution (llm/profiles.py)
def resolve_controller_llm(controller_config: dict[str, Any], llm_override: str | None = None) -> LLMProfile | None:
    """Resolve LLM for a controller. Order: CLI override -> config["llm"] -> default_model -> None."""

# API keys (llm/api_keys.py)
def save_api_key(provider: str, key: str) -> None
def get_api_key(provider_or_env: str) -> str
def list_api_keys() -> dict[str, str]  # masked keys

# Built-in presets (llm/presets.py)
PRESETS: dict[str, dict[str, Any]]   # 50+ presets: OpenAI, Claude, Gemini, Qwen, Kimi, MiMo, etc.
ALIASES: dict[str, str]             # Short names -> canonical preset names
```

---

## Auto-Compaction (`core/compact.py`)

```python
@dataclass
class CompactConfig:
    max_tokens: int = 256_000      # Model context window size
    threshold: float = 0.80        # Compact when prompt_tokens >= this fraction
    target: float = 0.40           # Aim for this fraction after compact
    keep_recent_turns: int = 8     # Keep last N turns raw (not summarized)
    enabled: bool = True
    compact_model: str | None = None  # Optional different model for summarization

class CompactManager:
    """Non-blocking context compaction. Attached to an agent."""
    def __init__(self, config: CompactConfig | None = None): ...

    @property
    def is_compacting(self) -> bool

    def should_compact(self, prompt_tokens: int = 0) -> bool:
        """Check if compaction should be triggered based on token usage."""

    def trigger_compact(self) -> None:
        """Start compaction as a background asyncio task."""

    async def cancel(self) -> None:
        """Cancel any running compaction."""
```

---

## Builtins

### Built-in Tools

| Name | Description | Execution Mode |
|------|-------------|---------------|
| `bash` | Execute shell commands | DIRECT |
| `python` | Execute Python code and return output | DIRECT |
| `read` | Read file contents | DIRECT |
| `write` | Create/overwrite files | DIRECT |
| `edit` | Search-replace in files | DIRECT |
| `glob` | Find files by pattern | DIRECT |
| `grep` | Regex search in files | DIRECT |
| `tree` | Directory structure | DIRECT |
| `think` | Extended reasoning step | DIRECT |
| `scratchpad` | Session key-value memory | DIRECT |
| `search_memory` | Search session history (keyword or semantic) | DIRECT |
| `send_message` | Send to named channel | DIRECT |
| `wait_channel` | Wait for channel message | BACKGROUND |
| `http` | Make HTTP requests | DIRECT |
| `ask_user` | Prompt user for input | DIRECT |
| `json_read` | Query JSON files | DIRECT |
| `json_write` | Modify JSON files | DIRECT |
| `info` | Load tool/sub-agent docs | DIRECT |
| `create_trigger` | Create a trigger at runtime (timer, scheduler, channel) | DIRECT |
| `list_triggers` | Show active triggers | DIRECT |
| `stop_task` | Cancel a running background task by job ID | DIRECT |

**Terrarium management tools (9):** Used by the `root` creature.

| Name | Description | Execution Mode |
|------|-------------|---------------|
| `terrarium_create` | Create and start a terrarium | DIRECT |
| `terrarium_status` | Get terrarium status | DIRECT |
| `terrarium_stop` | Stop a running terrarium | DIRECT |
| `terrarium_send` | Send to a terrarium channel | DIRECT |
| `terrarium_observe` | Observe channel traffic | BACKGROUND |
| `terrarium_history` | Get channel message history | DIRECT |
| `creature_start` | Add a new creature via hot-plug | DIRECT |
| `creature_stop` | Stop and remove a creature | DIRECT |
| `creature_interrupt` | Interrupt a creature's current LLM turn | DIRECT |

```python
from kohakuterrarium.builtins.tool_catalog import get_builtin_tool
BashTool = get_builtin_tool("bash")
```

### Built-in Sub-Agents

| Name | Tools | Output |
|------|-------|--------|
| `explore` | glob, grep, read | Controller |
| `plan` | glob, grep, read | Controller |
| `worker` | read, write, edit, bash, glob, grep | Controller |
| `critic` | read, glob, grep | Controller |
| `summarize` | (none) | Controller |
| `research` | http, read, glob, grep | Controller |
| `coordinator` | send_message, wait_channel | Controller |
| `memory_read` | read, glob | Controller |
| `memory_write` | write, read | Controller |
| `response` | (none) | External |

```python
from kohakuterrarium.builtins.subagent_catalog import get_builtin_subagent_config, BUILTIN_SUBAGENTS
```

### Built-in Inputs

- `cli` - command-line prompt (`prompt` config)
- `tui` - terminal UI via shared session (`prompt`, `session_key` config)
- `none` - blocks forever, for trigger-only agents

### Built-in Outputs

- `stdout` - standard output with streaming support
- `tui` - terminal UI via shared session (`session_key` config)

### Framework Commands

| Command | Usage | Description |
|---------|-------|-------------|
| `info` | `[/info]bash[info/]` | Get full tool/sub-agent documentation |
| `jobs` | `[/jobs][jobs/]` | List running background jobs |
| `wait` | `[/wait]job_id[wait/]` | Wait for a background job to complete |

---

## Terrarium API (`terrarium/api.py`)

```python
class TerrariumAPI:
    # Channel operations
    async def list_channels(self) -> list[dict[str, str]]
    async def channel_info(self, name: str) -> dict[str, Any] | None
    async def send_to_channel(self, name: str, content: str, sender: str = "human", metadata: dict | None = None) -> str

    # Creature operations
    async def list_creatures(self) -> list[dict[str, Any]]
    async def get_creature_status(self, name: str) -> dict[str, Any] | None
    async def stop_creature(self, name: str) -> bool
    async def start_creature(self, name: str) -> bool

    # Terrarium operations
    def get_status(self) -> dict[str, Any]
    @property
    def is_running(self) -> bool
```

### Terrarium Runtime (`terrarium/runtime.py`)

```python
class TerrariumRuntime:
    def __init__(
        self,
        config: TerrariumConfig,
        *,
        environment: Environment | None = None,
        llm_override: str | None = None,
        pwd: str | None = None,
    ): ...

    async def start(self) -> None:
        """Start the terrarium: create channels, build creatures, start agents."""

    async def stop(self) -> None:
        """Stop all creatures and clean up."""

    async def run(self) -> None:
        """Run all creatures until interrupted or all stop."""

    # ── Status / Accessors ──

    @property
    def root_agent(self) -> Agent | None:
        """The root agent, if configured."""

    @property
    def is_running(self) -> bool

    @property
    def creatures(self) -> dict[str, CreatureHandle]:
        """All creature handles, keyed by name."""

    @property
    def session_store(self) -> SessionStore | None:
        """The attached SessionStore, or None."""

    def get_creature_agent(self, name: str) -> Agent | None:
        """Get a creature's Agent instance by name."""

    def list_channels(self) -> list[dict[str, str]]:
        """List all shared channels as dicts (name, type, description)."""

    def get_status(self) -> dict[str, Any]:
        """Return a status dict for monitoring.

        Keys: name, running, has_root, creatures (dict of states),
        channels (list), pwd, root_model, root_session_id,
        root_max_context, root_compact_threshold.
        """

    def attach_session_store(self, store: SessionStore) -> None:
        """Attach a SessionStore to all creatures, root agent, and channels.

        Must be called AFTER start() (when creatures exist).
        """

    @property
    def api(self) -> TerrariumAPI:
        """Lazy-initialized programmatic API for this runtime."""

    @property
    def observer(self) -> ChannelObserver:
        """Lazy-initialized channel observer."""
```

**Terrarium Config Loading:**

```python
def load_terrarium_config(path: str | Path) -> TerrariumConfig:
    """Load terrarium config from YAML file or directory.

    Supports direct file path or directory containing terrarium.yaml.
    Creature config paths resolved relative to YAML directory.

    Raises:
        FileNotFoundError: If config file cannot be found.
        ValueError: If required fields are missing.
    """
```

**Hot-Plug (via HotPlugMixin):**

```python
    async def add_creature(self, config: CreatureConfig) -> CreatureHandle
    async def remove_creature(self, name: str) -> bool
    async def add_channel(self, name: str, type: str, description: str) -> None
    async def wire_channel(self, creature: str, channel: str, direction: str) -> None
```

---

## Observer (`terrarium/observer.py`)

```python
class ChannelObserver:
    def __init__(self, session: Session, max_history: int = 1000): ...
    async def observe(self, channel_name: str) -> None
    def on_message(self, callback: Callable) -> None
    def record(self, channel_name: str, msg: ChannelMessage) -> None
    def get_messages(self, channel: str | None = None, last_n: int = 20) -> list[ObservedMessage]
    async def stop(self) -> None

@dataclass
class ObservedMessage:
    channel: str
    sender: str
    content: str
    message_id: str
    timestamp: datetime
    metadata: dict[str, Any]
```

---

## Output Log (`terrarium/output_log.py`)

```python
class OutputLogCapture:
    def get_entries(self, last_n: int = 20, entry_type: str | None = None) -> list[LogEntry]
    def get_text(self, last_n: int = 20) -> str
    @property
    def entry_count(self) -> int
    def clear(self) -> None

@dataclass
class LogEntry:
    timestamp: datetime
    content: str
    entry_type: str = "text"    # "text", "stream_flush", "activity"
    metadata: dict[str, Any] = field(default_factory=dict)

    def preview(self, max_len: int = 80) -> str
```

---

## Testing (`testing/`)

```python
class ScriptedLLM:
    """Deterministic LLM mock implementing LLMProvider."""
    def __init__(self, scripts: list[str] | list[ScriptEntry]): ...
    call_count: int
    last_user_message: str
    call_log: list

class OutputRecorder(BaseOutputModule):
    """Captures all output for assertions."""
    all_text: str
    stream_text: str
    writes: list[str]
    activities: list
    def assert_text_contains(self, text: str) -> None
    def assert_activity_count(self, activity_type: str, count: int) -> None

class EventRecorder:
    """Records events with timing for ordering assertions."""
    count: int
    def types_in_order(self) -> list[str]
    def assert_order(self, *types: str) -> None

class TestAgentBuilder:
    """Builder for test harness with real Controller + Executor."""
    def with_llm_script(self, scripts) -> Self
    def with_builtin_tools(self, tools: list[str]) -> Self
    def with_system_prompt(self, prompt: str) -> Self
    def with_session(self, key: str) -> Self
    def with_named_output(self, name: str, output: OutputModule) -> Self
    def build(self) -> TestEnvironment
```

---

## Backgroundify (`core/backgroundify.py`)

Mid-flight task promotion from direct (blocking) to background execution.

```python
@dataclass(frozen=True, slots=True)
class PromotionResult:
    """Sentinel returned by BackgroundifyHandle.wait() when promoted."""
    job_id: str
    message: str = "Task promoted to background. Result arrives later."

class BackgroundifyHandle:
    """Wraps an asyncio.Task with mid-flight promotion to background.

    Usage:
        handle = backgroundify(task, "bash_abc123")

        result = await handle.wait()
        if isinstance(result, PromotionResult):
            # Task was promoted — add placeholder, continue
        else:
            # Task completed — use result normally

        # From TUI/frontend:
        handle.promote()  # returns True if promotion succeeded
    """

    def __init__(
        self,
        task: asyncio.Task,
        job_id: str,
        on_bg_complete: Callable[[str, Any], Awaitable[None]] | None = None,
    ): ...

    @property
    def job_id(self) -> str
    @property
    def promoted(self) -> bool
    @property
    def done(self) -> bool
    @property
    def task(self) -> asyncio.Task:
        """The underlying asyncio task (for cancellation)."""

    def promote(self) -> bool:
        """Promote to background. Returns False if task already completed."""

    async def wait(self) -> Any:
        """Wait for completion OR promotion (whichever comes first).

        Returns the real task result if it completes first,
        or PromotionResult if promote() is called first.
        """

def backgroundify(
    task: asyncio.Task,
    job_id: str,
    on_bg_complete: Callable[[str, Any], Awaitable[None]] | None = None,
    background_init: bool = False,
) -> BackgroundifyHandle:
    """Wrap a task with background promotion capability.

    If background_init=True, promote immediately (= current background mode).
    """
```

---

## Channels (`core/channel.py`)

Named async channel system for cross-component communication.

```python
@dataclass
class ChannelMessage:
    """A message sent through a channel."""
    sender: str
    content: str | dict
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    message_id: str = field(default_factory=generate_message_id)
    reply_to: str | None = None
    channel: str | None = None

class BaseChannel(ABC):
    """Base interface for all channel types."""
    def __init__(self, name: str, description: str = "", max_history: int = 200): ...

    def on_send(self, callback: Any) -> None:
        """Register a callback fired on every send (before queue consumption).

        Callback signature: callback(channel_name: str, message: ChannelMessage)
        """

    def remove_on_send(self, callback: Any) -> None:
        """Remove a send callback."""

    @abstractmethod
    async def send(self, message: ChannelMessage) -> None: ...

    @property
    def channel_type(self) -> str
    @property
    def empty(self) -> bool
    @property
    def qsize(self) -> int

class SubAgentChannel(BaseChannel):
    """Point-to-point queue channel. Each message consumed by one receiver."""
    def __init__(self, name: str, maxsize: int = 0, description: str = ""): ...

    async def send(self, message: ChannelMessage) -> None
    async def receive(self, timeout: float | None = None) -> ChannelMessage:
        """Block until message available. Raises asyncio.TimeoutError on timeout."""
    def try_receive(self) -> ChannelMessage | None:
        """Non-blocking receive. Returns None if empty."""

class AgentChannel(BaseChannel):
    """Broadcast channel. All subscribers receive every message (except sender)."""
    def __init__(self, name: str, description: str = ""): ...

    async def send(self, message: ChannelMessage) -> None:
        """Broadcast to all subscribers except the sender."""

    def subscribe(self, subscriber_id: str) -> ChannelSubscription:
        """Subscribe to this channel. Returns existing subscription if already subscribed."""

    def unsubscribe(self, subscriber_id: str) -> None

    @property
    def subscriber_count(self) -> int

class ChannelSubscription:
    """A subscriber's view of a broadcast channel."""
    subscriber_id: str

    async def receive(self, timeout: float | None = None) -> ChannelMessage
    def try_receive(self) -> ChannelMessage | None
    def unsubscribe(self) -> None
    @property
    def empty(self) -> bool
    @property
    def qsize(self) -> int

class ChannelRegistry:
    """Registry of named channels."""
    def get_or_create(
        self,
        name: str,
        channel_type: str = "queue",
        maxsize: int = 0,
        description: str = "",
    ) -> BaseChannel
    def get(self, name: str) -> BaseChannel | None
    def list_channels(self) -> list[str]
    def get_channel_info(self) -> list[dict[str, str]]:
        """Get info (name, type, description) for all registered channels."""
    def remove(self, name: str) -> bool
```

---

## KohakuManager (`serving/manager.py`)

Unified service manager for agents and terrariums. Transport-agnostic -- used by CLI, TUI, Web, or any interface.

### Agent Lifecycle

```python
class KohakuManager:
    def __init__(self, session_dir: str | None = None) -> None: ...

    async def agent_create(
        self,
        config_path: str | None = None,
        config: AgentConfig | None = None,
        llm_override: str | None = None,
        pwd: str | None = None,
    ) -> str:
        """Create and start a standalone agent. Returns agent_id."""

    async def register_agent(self, agent: Agent, store: SessionStore | None = None) -> str:
        """Register a pre-built agent (e.g. from resume). Returns agent_id."""

    async def agent_stop(self, agent_id: str) -> None:
        """Stop and cleanup an agent."""

    async def agent_chat(self, agent_id: str, message: str) -> AsyncIterator[str]:
        """Send a message and stream the response."""

    def agent_status(self, agent_id: str) -> dict:
        """Get agent status (running, tools, subagents)."""

    def agent_list(self) -> list[dict]:
        """List all running agents."""

    def agent_interrupt(self, agent_id: str) -> None:
        """Interrupt the agent's current turn."""

    def agent_get_jobs(self, agent_id: str) -> list[dict]:
        """Get running/recent jobs for an agent (tools + sub-agents)."""

    async def agent_cancel_job(self, agent_id: str, job_id: str) -> bool:
        """Cancel a running job (tool or sub-agent). Returns True if cancelled."""

    def agent_switch_model(self, agent_id: str, profile_name: str) -> str:
        """Switch an agent's LLM model. Returns the new model name."""

    async def agent_execute_command(self, agent_id: str, command: str, args: str = "") -> dict:
        """Execute a user slash command on an agent. Returns result dict."""

    def agent_get_history(self, agent_id: str) -> list[dict]:
        """Get conversation history for an agent."""
```

### Agent Channel Ops

```python
    def agent_channel_list(self, agent_id: str) -> list[dict[str, str]]
    def agent_channel_info(self, agent_id: str, channel: str) -> dict | None
    async def agent_channel_send(
        self, agent_id: str, channel: str, content: str, sender: str = "human"
    ) -> str:
        """Send a message to a standalone agent's channel. Returns message_id."""
    async def agent_channel_stream(
        self, agent_id: str, channels: list[str] | None = None
    ) -> AsyncIterator[ChannelEvent]:
        """Stream channel events for a standalone agent."""
```

### Terrarium Lifecycle

```python
    async def terrarium_create(
        self,
        config_path: str | None = None,
        config: TerrariumConfig | None = None,
        pwd: str | None = None,
    ) -> str:
        """Create and start a terrarium. Returns terrarium_id."""

    async def register_terrarium(
        self, runtime: TerrariumRuntime, store: SessionStore | None = None
    ) -> str:
        """Register a pre-built terrarium (e.g. from resume). Returns terrarium_id."""

    async def terrarium_stop(self, terrarium_id: str) -> None:
        """Stop all creatures and cleanup."""

    def terrarium_mount(self, terrarium_id: str, target: str) -> AgentSession:
        """Mount onto a creature (or root) for input injection and output capture.

        target: "root" for root agent, or a creature name.
        """

    async def terrarium_chat(
        self, terrarium_id: str, target: str, message: str
    ) -> AsyncIterator[str]:
        """Chat with any creature (or root) in a terrarium."""

    def terrarium_status(self, terrarium_id: str) -> dict
    def terrarium_list(self) -> list[dict]
```

### Terrarium Channel Ops

```python
    def terrarium_channel_list(self, terrarium_id: str) -> list[dict[str, str]]
    def terrarium_channel_info(self, terrarium_id: str, channel: str) -> dict | None
    async def terrarium_channel_send(
        self, terrarium_id: str, channel: str, content: str, sender: str = "human"
    ) -> str:
        """Send a message to a shared terrarium channel. Returns message_id."""
    async def terrarium_channel_add(
        self, terrarium_id: str, name: str,
        channel_type: str = "queue", description: str = "",
    ) -> None:
        """Add a shared channel to a running terrarium (hot-plug)."""
    async def terrarium_channel_stream(
        self, terrarium_id: str, channels: list[str] | None = None
    ) -> AsyncIterator[ChannelEvent]:
        """Stream shared channel events from a terrarium."""
```

### Creature Ops

```python
    def creature_list(self, terrarium_id: str) -> list[dict]

    async def creature_add(self, terrarium_id: str, config: CreatureConfig) -> str:
        """Add a creature to a running terrarium (hot-plug). Returns name."""

    async def creature_remove(self, terrarium_id: str, name: str) -> bool

    async def creature_wire(
        self, terrarium_id: str, creature: str, channel: str, direction: str
    ) -> None:
        """Wire a creature to a channel (listen or send)."""

    def creature_switch_model(self, terrarium_id: str, name: str, profile_name: str) -> str

    async def creature_interrupt(self, terrarium_id: str, name: str) -> None

    def creature_get_jobs(self, terrarium_id: str, name: str) -> list[dict]

    async def creature_cancel_job(self, terrarium_id: str, name: str, job_id: str) -> bool
```

### Creature Channel Ops

```python
    def creature_channel_list(self, terrarium_id: str, creature: str) -> list[dict[str, str]]
    def creature_channel_info(self, terrarium_id: str, creature: str, channel: str) -> dict | None
    async def creature_channel_send(
        self, terrarium_id: str, creature: str, channel: str,
        content: str, sender: str = "human",
    ) -> str:
        """Send a message to a creature's private channel. Returns message_id."""
```

### Manager Lifecycle

```python
    async def shutdown(self) -> None:
        """Stop all agents and terrariums."""
```

---

## Plugin System (`modules/plugin/`)

### BasePlugin

```python
class BasePlugin:
    """Base class for plugins. Override only what you need.

    Pre/post hooks run linearly by priority (lower = runs first in pre).
    Return None to keep value unchanged, return a value to replace it.
    """

    name: str = "unnamed"
    priority: int = 50

    # ── Lifecycle ──
    async def on_load(self, context: PluginContext) -> None:
        """Called when plugin is loaded."""
    async def on_unload(self) -> None:
        """Called when agent shuts down."""

    # ── LLM hooks ──
    async def pre_llm_call(self, messages: list[dict], **kwargs) -> list[dict] | None:
        """Before LLM call. Return modified messages or None.

        kwargs: model (str), tools (list | None, native mode only)
        """
    async def post_llm_call(
        self, messages: list[dict], response: str, usage: dict, **kwargs
    ) -> None:
        """After LLM call. Observation only."""

    # ── Tool hooks ──
    async def pre_tool_execute(self, args: dict, **kwargs) -> dict | None:
        """Before tool execution. Return modified args or None.

        kwargs: tool_name (str), job_id (str)
        Raise PluginBlockError to prevent execution.
        """
    async def post_tool_execute(self, result: Any, **kwargs) -> Any | None:
        """After tool execution. Return modified result or None."""

    # ── Sub-agent hooks ──
    async def pre_subagent_run(self, task: str, **kwargs) -> str | None:
        """Before sub-agent run. Return modified task or None.

        Raise PluginBlockError to prevent execution.
        """
    async def post_subagent_run(self, result: Any, **kwargs) -> Any | None:
        """After sub-agent run. Return modified result or None."""

    # ── Callbacks (fire-and-forget) ──
    async def on_agent_start(self) -> None
    async def on_agent_stop(self) -> None
    async def on_event(self, event: TriggerEvent) -> None:
        """Called on incoming trigger event. Observation only."""
    async def on_interrupt(self) -> None
    async def on_task_promoted(self, job_id: str, tool_name: str) -> None
    async def on_compact_start(self, context_length: int) -> None
    async def on_compact_end(self, summary: str, messages_removed: int) -> None

class PluginBlockError(Exception):
    """Raised by a plugin to block tool/sub-agent execution.

    The error message becomes the tool result.
    Only meaningful in pre_tool_execute and pre_subagent_run.
    """
```

### PluginContext

```python
@dataclass
class PluginContext:
    """Context provided to plugins on load."""
    agent_name: str = ""
    working_dir: Path = field(default_factory=Path.cwd)
    session_id: str = ""
    model: str = ""

    def switch_model(self, name: str) -> str:
        """Switch the LLM model. Returns resolved model name."""

    def inject_event(self, event: TriggerEvent) -> None:
        """Push a trigger event into the agent's event queue."""

    def get_state(self, key: str) -> Any:
        """Read plugin-scoped state from session store."""

    def set_state(self, key: str, value: Any) -> None:
        """Write plugin-scoped state to session store."""
```

### PluginManager

```python
class PluginManager:
    """Manages plugin lifecycle, hook wrapping, and callback dispatch."""

    def register(self, plugin: BasePlugin) -> None
    def enable(self, name: str) -> bool
    def disable(self, name: str) -> bool
    def is_enabled(self, name: str) -> bool
    def list_plugins(self) -> list[dict[str, Any]]:
        """Returns list of {name, priority, enabled} for each plugin."""

    async def load_all(self, context: PluginContext) -> None:
        """Call on_load for enabled plugins only."""

    async def load_pending(self) -> None:
        """Call on_load for plugins that were enabled at runtime."""

    async def unload_all(self) -> None

    def wrap_method(
        self,
        pre_hook: str,
        post_hook: str,
        original: Callable,
        *,
        input_kwarg: str = "",
        extra_kwargs: dict[str, Any] | None = None,
    ) -> Callable:
        """Wrap a method with pre/post hooks from all plugins.

        Returns original unchanged if no plugins override the hooks.
        """

    async def run_pre_hooks(self, hook_name: str, value: Any, **kwargs) -> Any:
        """Run pre-hooks linearly, returning (possibly transformed) value.

        Used where wrap_method cannot apply (async generators).
        """

    async def notify(self, callback_name: str, **kwargs) -> None:
        """Fire a callback on all active plugins (fire-and-forget)."""
```

**Plugin configuration (in config.yaml):**

```yaml
plugins:
  - name: my_plugin
    type: custom
    module: path.to.module
    class: MyPlugin
```

---

## Composition Algebra (`compose/`)

Pythonic operators for combining agents into pipelines, routers, and parallel workflows.

### Runnable Protocol

```python
@runtime_checkable
class Runnable(Protocol):
    """Anything that takes input and produces output asynchronously."""
    async def run(self, input: Any) -> Any: ...
```

### BaseRunnable

```python
class BaseRunnable:
    """Concrete base providing operator overloads.

    Operators:
      >>   sequence (auto-wraps plain callables)
      &    parallel product (asyncio.gather)
      |    fallback (try first, if exception try second)
      *N   retry N times
      ()   run (await pipeline(x))
    """

    async def run(self, input: Any) -> Any: ...
    async def __call__(self, input: Any) -> Any:
        """await pipeline(x) is sugar for await pipeline.run(x)."""

    # Operators
    def __rshift__(self, other) -> "BaseRunnable":  # a >> b
        """Sequence. Also supports >> dict for routing."""
    def __and__(self, other) -> "BaseRunnable":      # a & b
        """Parallel product."""
    def __or__(self, other) -> "BaseRunnable":       # a | b
        """Fallback."""
    def __mul__(self, n: int) -> "BaseRunnable":     # a * 3
        """Retry N times."""

    def iterate(self, initial_input: Any) -> "PipelineIterator":
        """Return async iterator that feeds output back as input."""

    def map(self, fn: Callable) -> "BaseRunnable":
        """Post-process output: self >> pure(fn)."""

    def contramap(self, fn: Callable) -> "BaseRunnable":
        """Pre-process input: pure(fn) >> self."""

    def fails_when(self, predicate: Callable[[Any], bool]) -> "BaseRunnable":
        """Wrap so output matching predicate raises (triggers fallback)."""
```

### Combinators

```python
class Pure(BaseRunnable):
    """Wrap a sync or async callable as a Runnable."""
    def __init__(self, fn: Callable): ...

class Sequence(BaseRunnable):
    """Run steps in order, piping each output as the next input."""

class Product(BaseRunnable):
    """Run branches concurrently (asyncio.gather), return tuple of results."""

class Fallback(BaseRunnable):
    """Try primary; if it raises Exception, run fallback instead."""

class Retry(BaseRunnable):
    """Retry up to max_attempts times on Exception."""
    def __init__(self, inner: BaseRunnable, max_attempts: int): ...

class Router(BaseRunnable):
    """Route to a branch by key. Use '_default' for catch-all.

    Input: (key, payload) tuple or just key (used as both key and payload).
    """
    def __init__(self, routes: dict[str, BaseRunnable]): ...

class FailsWhen(BaseRunnable):
    """Raise ValueError when predicate matches output."""

class PipelineIterator:
    """Async iterator that repeatedly runs a pipeline, feeding output back.

    Usage:
        async for result in pipeline.iterate("start"):
            if done(result):
                break
    """
    def feed(self, value: Any) -> None:
        """Override the next iteration's input."""
```

### Agent Wrappers

```python
class AgentRunnable(BaseRunnable):
    """Persistent agent session, reused across calls.

    Conversation history accumulates. Supports async with.
    """
    async def run(self, input: Any) -> str
    async def close(self) -> None

class AgentFactory(BaseRunnable):
    """Ephemeral agent — creates a fresh session per call, destroys after.

    No conversation carry-over between calls.
    """
    def __init__(self, config: AgentConfig | str | Path): ...
    async def run(self, input: Any) -> str

async def agent(config: AgentConfig | str | Path) -> AgentRunnable:
    """Create a persistent AgentRunnable (starts immediately).

    Usage:
        async with await agent("@kt-defaults/creatures/swe") as a:
            result = await (a >> process)(task)
    """

def factory(config: AgentConfig | str | Path) -> AgentFactory:
    """Create an ephemeral AgentFactory (no startup cost)."""
```

### Effects (Cost Analysis)

```python
@dataclass
class Effects:
    """Optional cost/latency/reliability annotation on a Runnable."""
    cost: float | None = None
    latency: float | None = None
    reliability: float | None = None

    def sequential(self, other: "Effects") -> "Effects":
        """Compose for f >> g: costs add, latencies add, reliabilities multiply."""

    def parallel(self, other: "Effects") -> "Effects":
        """Compose for f & g: costs add, latencies max, reliabilities multiply."""
```

### Composition Examples

```python
# Routing: classify → route to specialist
classifier = factory(classifier_config)
router = Pure(classify_and_pair) >> {
    "code": factory(code_config),
    "writing": factory(writing_config),
    "_default": factory(general_config),
}

# Pipeline with transforms: agent → parse → agent → format
pipeline = extractor >> json.loads >> enricher >> format_report

# Parallel branches
results = await (analyst & writer & designer)(task)

# Retry with fallback
safe = (expert * 2) | generalist

# Loop with native control flow
async for result in (writer >> reviewer).iterate(task):
    if "APPROVED" in result:
        break
```
