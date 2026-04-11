# Plugin System

Plugins let you hook into the internal flows of a KohakuTerrarium agent without replacing any component. They observe, transform, and guard every stage of the agent lifecycle -- from LLM calls to tool execution to output routing.

This guide covers both the **prompt plugin system** (available now) and the **agent lifecycle plugin system** (available now, with some hooks still being wired). By the end, you will know how to write, configure, and distribute plugins for any KohakuTerrarium agent.

## Plugins vs Modules

KohakuTerrarium has two extension mechanisms. Picking the wrong one leads to unnecessary complexity.

**Modules** replace a component entirely. You swap out an input module (CLI for Slack), add a new tool (Semgrep scanner), or change the output target (stdout for Discord). Modules are self-contained and independently configurable.

**Plugins** intercept the flows *between* components. They observe LLM calls, transform tool arguments, block dangerous commands, inject context before the model sees the conversation, or redact PII from output. Plugins never replace a component -- they wrap around existing ones.

| Question | Use a Module | Use a Plugin |
|----------|-------------|--------------|
| "I need a new input source" | Yes | No |
| "I want to log every LLM call" | No | Yes |
| "I need a custom tool" | Yes | No |
| "I want to block `rm -rf` in bash" | No | Yes |
| "I need a different output target" | Yes | No |
| "I want to inject RAG context before each LLM call" | No | Yes |
| "I need to track cost per session" | No | Yes |

The rule of thumb: if you are adding a new capability, write a module. If you are intercepting or augmenting an existing flow, write a plugin.

---

## Part 1: Plugin Architecture

### BasePlugin Class

All plugins extend `BasePlugin` from `kohakuterrarium.modules.plugin.base`. You override only the hooks you need -- everything else is a no-op by default.

```python
from kohakuterrarium.modules.plugin.base import BasePlugin, PluginContext

class MyPlugin(BasePlugin):
    name = "my_plugin"
    priority = 50  # Lower number = runs first (default: 50)

    async def on_load(self, context: PluginContext) -> None:
        """Called once when the plugin is loaded into an agent."""
        pass

    async def on_unload(self) -> None:
        """Called when the agent shuts down."""
        pass
```

Every plugin has two required class attributes:

- **`name`**: A unique string identifier. Used in config, logs, and the `/plugin` command.
- **`priority`**: An integer that controls execution order. Lower numbers run first for pre-hooks and callbacks, last for post-hooks. Default is 50.

### PluginContext

When `on_load` is called, the plugin receives a `PluginContext` with information about the agent and controlled write methods:

```python
@dataclass
class PluginContext:
    agent_name: str          # Name of the agent
    working_dir: Path        # Current working directory
    session_id: str          # Session identifier (if any)
    model: str               # Current LLM model name

    def switch_model(self, name: str) -> str:
        """Switch the LLM model. Returns resolved model name."""

    def inject_event(self, event) -> None:
        """Push a trigger event into the agent's event queue."""

    def get_state(self, key: str) -> Any:
        """Read plugin-scoped state from session store."""

    def set_state(self, key: str, value: Any) -> None:
        """Write plugin-scoped state to session store."""
```

The `get_state` / `set_state` methods persist data across turns within a session. State is namespaced automatically -- a plugin named `"cost_tracker"` writing key `"total"` stores it as `plugin:cost_tracker:total` in the session store.

### PluginManager

The `PluginManager` is created at agent startup and lives on `agent.plugins`. It handles:

1. **Registration** -- plugins are sorted by priority on registration.
2. **Lifecycle** -- `load_all()` calls `on_load` on every plugin; `unload_all()` calls `on_unload` in reverse order.
3. **Hook dispatch** -- two patterns: `wrap_method()` for pre/post decoration of existing methods, and `notify()` for fire-and-forget callbacks.
4. **Runtime toggle** -- plugins can be enabled/disabled at runtime via the `/plugin` slash command.

### Hook Execution Modes

The plugin system uses three distinct modes for running hooks:

#### 1. Fire-and-Forget (Callbacks)

Used for observation hooks like `on_agent_start`, `on_event`, `on_interrupt`. Every active plugin's hook is called. Return values are ignored. Exceptions are logged but never propagate.

```python
# Internal: how the manager dispatches a callback
await self.plugins.notify("on_agent_start")
```

#### 2. Pipeline / Chain (Pre/Post Hooks)

Used for hooks that can transform data, like `pre_llm_call` (transform messages) or `pre_tool_execute` (transform tool arguments). Each plugin receives the output of the previous plugin. Returning `None` means "no change."

```python
# Internal: pre-hook chain for LLM calls
messages = await self.plugins.run_pre_hooks(
    "pre_llm_call", messages, model="claude-sonnet-4"
)
```

#### 3. Method Wrapping

For tool execution and sub-agent runs, the manager wraps the actual `execute()` or `_run_subagent()` method with a decorator that runs all pre-hooks, calls the original, then runs all post-hooks. When no plugins override a hook, the original function is returned unchanged -- zero overhead.

```python
# Internal: how tool.execute is wrapped at agent startup
tool.execute = plugins.wrap_method(
    "pre_tool_execute",    # pre-hook name
    "post_tool_execute",   # post-hook name
    tool.execute,          # original method
    input_kwarg="args",
    extra_kwargs={"tool_name": tool_name},
)
```

### Error Handling

Two distinct error paths exist in the plugin system:

**`PluginBlockError`** -- A policy rejection. Raise this in `pre_tool_execute` or `pre_subagent_run` to prevent execution. The error message becomes the tool result that the LLM sees, so the model can adjust its approach.

```python
from kohakuterrarium.modules.plugin.base import PluginBlockError

async def pre_tool_execute(self, args, **kwargs):
    if kwargs.get("tool_name") == "bash":
        cmd = args.get("command", "")
        if "rm -rf /" in cmd:
            raise PluginBlockError("Blocked: destructive command detected.")
    return None
```

**Regular `Exception`** -- A plugin bug. Logged as a warning, the plugin is skipped, and execution continues as if the plugin did not exist. The model never sees plugin failures.

### Plugin Lifecycle

```
Agent.start()
  |
  +-- init_plugins() creates PluginManager, registers plugins from config
  |
  +-- _apply_plugin_hooks() wraps tool.execute and subagent._run_subagent
  |
  +-- _load_plugins()
  |     |-- plugins.load_all(context)  -- calls on_load for each plugin
  |     +-- plugins.notify("on_agent_start")
  |
  +-- ... agent runs, hooks fire on each LLM call, tool, event ...
  |
  +-- Agent.stop()
        |-- plugins.notify("on_agent_stop")
        +-- plugins.unload_all()  -- calls on_unload in reverse order
```

---

## Part 2: Prompt Plugins (Available Now)

The prompt plugin system (`kohakuterrarium.prompt.plugins`) controls what goes into the system prompt. Each prompt plugin contributes a section, and the aggregator combines them by priority order.

### The PromptPlugin Protocol

```python
from kohakuterrarium.prompt.plugins import PluginContext, BasePlugin

class MyPromptPlugin(BasePlugin):
    @property
    def name(self) -> str:
        return "my_prompt_section"

    @property
    def priority(self) -> int:
        return 30  # Lower = earlier in prompt

    def get_content(self, context: PluginContext) -> str | None:
        """Return content to add to the system prompt, or None to skip."""
        return "## My Section\n\nCustom instructions here."
```

Note: This is the `BasePlugin` from `prompt/plugins.py`, which is separate from the lifecycle `BasePlugin` in `modules/plugin/base.py`. The prompt plugin system predates the lifecycle plugin system and uses a simpler protocol.

### Built-in Prompt Plugins

KohakuTerrarium ships with four prompt plugins:

| Plugin | Priority | What It Does |
|--------|----------|-------------|
| `EnvInfoPlugin` | 10 | Injects working directory, platform, git status, date |
| `ProjectInstructionsPlugin` | 20 | Loads AGENTS.md / .kohaku.md / CLAUDE.md from project tree |
| `ToolListPlugin` | 50 | Generates tool name + description list from registry |
| `FrameworkHintsPlugin` | 60 | Adds tool call syntax examples and framework commands |

For a basic agent, only `ToolListPlugin` and `FrameworkHintsPlugin` are active. SWE-style agents get all four.

### Writing a Custom Prompt Plugin

Here is a complete prompt plugin that injects a custom "rules" section into the system prompt:

```python
# File: my_agent/plugins/rules_plugin.py

from kohakuterrarium.prompt.plugins import BasePlugin, PluginContext


class RulesPlugin(BasePlugin):
    """Injects team-specific coding rules into the system prompt."""

    @property
    def name(self) -> str:
        return "team_rules"

    @property
    def priority(self) -> int:
        return 25  # After env info, before tool list

    def get_content(self, context: PluginContext) -> str | None:
        rules_file = context.working_dir / "RULES.md"
        if not rules_file.exists():
            return None

        content = rules_file.read_text(encoding="utf-8")
        return f"## Team Rules\n\n{content}"
```

### Using Prompt Plugins with the Aggregator

The `aggregate_with_plugins` function assembles the full system prompt:

```python
from kohakuterrarium.prompt.aggregator import aggregate_with_plugins
from kohakuterrarium.prompt.plugins import get_swe_plugins

# Start with the SWE defaults
plugins = get_swe_plugins()

# Add your custom plugin
from my_agent.plugins.rules_plugin import RulesPlugin
plugins.append(RulesPlugin())

system_prompt = aggregate_with_plugins(
    base_prompt="You are a helpful SWE agent.",
    plugins=plugins,
    registry=my_registry,
    working_dir=Path.cwd(),
)
```

The aggregator sorts plugins by priority, calls `get_content()` on each, and joins the non-None results after the base prompt.

---

## Part 3: Agent Lifecycle Plugins (Available Now)

The lifecycle plugin system hooks into the agent's runtime flow. It is implemented in `kohakuterrarium.modules.plugin` and is fully functional. All the hooks documented below are wired into the core agent code.

### Available Hooks

| Hook | Mode | When It Fires | Can Modify? |
|------|------|---------------|-------------|
| `on_load(context)` | lifecycle | Plugin loaded into agent | -- |
| `on_unload()` | lifecycle | Agent shutting down | -- |
| `on_agent_start()` | callback | After `agent.start()` completes | No |
| `on_agent_stop()` | callback | Before `agent.stop()` begins | No |
| `pre_llm_call(messages, **kw)` | pipeline | Before LLM API call | Yes (messages) |
| `post_llm_call(messages, response, usage, **kw)` | callback | After LLM response received | No |
| `pre_tool_execute(args, **kw)` | pipeline | Before tool runs | Yes (args) |
| `post_tool_execute(result, **kw)` | pipeline | After tool completes | Yes (result) |
| `pre_subagent_run(task, **kw)` | pipeline | Before sub-agent spawns | Yes (task string) |
| `post_subagent_run(result, **kw)` | pipeline | After sub-agent completes | Yes (result) |
| `on_event(event)` | callback | Incoming trigger event | No (observe only) |
| `on_interrupt()` | callback | User presses Escape | No |
| `on_task_promoted(job_id, tool_name)` | callback | Direct task promoted to background | No |
| `on_compact_start(context_length)` | callback | Before context compaction | No |
| `on_compact_end(summary, messages_removed)` | callback | After context compaction | No |

**Pipeline hooks** return `None` to leave the value unchanged, or return a new value to replace it for the next plugin in the chain. **Callback hooks** are fire-and-forget -- return values are ignored.

### Example 1: Audit Logger

A plugin that writes a structured JSONL log of every LLM call and tool execution.

```python
# File: plugins/audit_logger.py

import json
import time
from pathlib import Path
from typing import Any

from kohakuterrarium.modules.plugin.base import BasePlugin, PluginContext


class AuditLoggerPlugin(BasePlugin):
    """Writes structured audit logs for every agent action."""

    name = "audit_logger"
    priority = 1  # Run first to observe everything

    def __init__(self, options: dict | None = None):
        opts = options or {}
        self._log_path = Path(opts.get("path", "agent_audit.jsonl"))
        self._log_file = None
        self._agent_name = ""

    async def on_load(self, context: PluginContext) -> None:
        self._log_file = open(self._log_path, "a", encoding="utf-8")
        self._agent_name = context.agent_name
        self._emit("plugin_loaded")

    async def on_unload(self) -> None:
        self._emit("plugin_unloaded")
        if self._log_file:
            self._log_file.close()

    async def post_llm_call(
        self, messages: list[dict], response: str, usage: dict, **kwargs
    ) -> None:
        self._emit(
            "llm_call",
            model=kwargs.get("model", ""),
            message_count=len(messages),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        )

    async def pre_tool_execute(self, args: dict, **kwargs) -> dict | None:
        self._emit(
            "tool_start",
            tool=kwargs.get("tool_name", ""),
            args_keys=list(args.keys()),
        )
        return None  # Do not modify args

    async def post_tool_execute(self, result: Any, **kwargs) -> Any | None:
        error = getattr(result, "error", None)
        self._emit(
            "tool_end",
            tool=kwargs.get("tool_name", ""),
            success=error is None,
            error=str(error) if error else "",
        )
        return None  # Do not modify result

    async def on_event(self, event: Any) -> None:
        self._emit("event", event_type=str(getattr(event, "type", "")))

    def _emit(self, event_type: str, **data) -> None:
        if not self._log_file:
            return
        record = {
            "ts": time.time(),
            "agent": self._agent_name,
            "event": event_type,
            **data,
        }
        self._log_file.write(json.dumps(record) + "\n")
        self._log_file.flush()
```

**Configuration:**

```yaml
# creatures/my-agent/config.yaml
plugins:
  - name: audit_logger
    module: plugins.audit_logger
    class: AuditLoggerPlugin
    options:
      path: ./logs/audit.jsonl
```

### Example 2: Permission Guard

A plugin that blocks dangerous tool calls before they execute.

```python
# File: plugins/permission_guard.py

from kohakuterrarium.modules.plugin.base import (
    BasePlugin,
    PluginBlockError,
    PluginContext,
)


class PermissionGuardPlugin(BasePlugin):
    """Blocks dangerous tool calls based on configurable rules."""

    name = "permission_guard"
    priority = 5  # Run before most plugins

    def __init__(self, options: dict | None = None):
        opts = options or {}
        self._blocked_tools: set[str] = set(opts.get("blocked_tools", []))
        self._blocked_patterns: list[str] = opts.get("blocked_patterns", [
            "rm -rf",
            "git push --force",
            "DROP TABLE",
        ])
        self._readonly_tools: set[str] = set(opts.get("readonly_tools", [
            "read", "glob", "grep", "tree", "info", "think",
        ]))

    async def pre_tool_execute(self, args: dict, **kwargs) -> dict | None:
        tool_name = kwargs.get("tool_name", "")

        # Always allow read-only tools
        if tool_name in self._readonly_tools:
            return None

        # Block entirely forbidden tools
        if tool_name in self._blocked_tools:
            raise PluginBlockError(
                f"Tool '{tool_name}' is blocked by permission_guard. "
                f"Use a different approach."
            )

        # Check bash commands for dangerous patterns
        if tool_name == "bash":
            cmd = args.get("command", "")
            for pattern in self._blocked_patterns:
                if pattern in cmd:
                    raise PluginBlockError(
                        f"Blocked: '{pattern}' found in bash command. "
                        f"This pattern is not allowed by permission_guard."
                    )

        return None  # Allow execution with original args
```

**Configuration:**

```yaml
plugins:
  - name: permission_guard
    module: plugins.permission_guard
    class: PermissionGuardPlugin
    options:
      blocked_tools:
        - bash
      blocked_patterns:
        - "rm -rf /"
        - "git push --force"
      readonly_tools:
        - read
        - glob
        - grep
        - tree
        - info
        - think
```

When a blocked tool is called, the model receives an error message like:

```
Error: [permission_guard] Tool 'bash' is blocked by permission_guard. Use a different approach.
```

The model sees this as a normal tool error and can adjust its strategy.

### Example 3: Cost Tracker

A plugin that tracks token usage and estimated cost per session.

```python
# File: plugins/cost_tracker.py

from kohakuterrarium.modules.plugin.base import BasePlugin, PluginContext
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class CostTrackerPlugin(BasePlugin):
    """Tracks token usage and estimates cost per session."""

    name = "cost_tracker"
    priority = 10

    # Approximate pricing per 1M tokens
    DEFAULT_PRICING = {
        "claude-opus-4": {"input": 15.00, "output": 75.00},
        "claude-sonnet-4": {"input": 3.00, "output": 15.00},
        "claude-haiku": {"input": 0.25, "output": 1.25},
        "default": {"input": 1.00, "output": 5.00},
    }

    def __init__(self, options: dict | None = None):
        opts = options or {}
        self._budget = opts.get("budget_usd", 0.0)  # 0 = no limit
        self._pricing = {**self.DEFAULT_PRICING, **(opts.get("pricing", {}))}
        self._total_cost = 0.0
        self._call_count = 0
        self._context: PluginContext | None = None

    async def on_load(self, context: PluginContext) -> None:
        self._context = context
        # Restore state from previous session (if resuming)
        saved_cost = context.get_state("total_cost")
        if saved_cost is not None:
            self._total_cost = float(saved_cost)
        saved_calls = context.get_state("call_count")
        if saved_calls is not None:
            self._call_count = int(saved_calls)

    async def post_llm_call(
        self, messages: list[dict], response: str, usage: dict, **kwargs
    ) -> None:
        model = kwargs.get("model", "default")
        pricing = self._pricing.get(model, self._pricing["default"])

        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        cached_tokens = usage.get("cached_tokens", 0)

        effective_input = prompt_tokens - cached_tokens
        cost = (
            effective_input * pricing["input"]
            + completion_tokens * pricing["output"]
        ) / 1_000_000

        self._total_cost += cost
        self._call_count += 1

        logger.info(
            "LLM cost",
            call_number=self._call_count,
            call_cost=f"${cost:.4f}",
            total_cost=f"${self._total_cost:.4f}",
        )

        # Persist to session state
        if self._context:
            self._context.set_state("total_cost", self._total_cost)
            self._context.set_state("call_count", self._call_count)

        # Budget warning
        if self._budget > 0 and self._total_cost >= self._budget:
            logger.warning(
                "Budget exhausted",
                total=f"${self._total_cost:.4f}",
                budget=f"${self._budget:.2f}",
            )

    async def on_agent_stop(self) -> None:
        logger.info(
            "Session cost summary",
            total_calls=self._call_count,
            total_cost=f"${self._total_cost:.4f}",
        )
```

**Configuration:**

```yaml
plugins:
  - name: cost_tracker
    module: plugins.cost_tracker
    class: CostTrackerPlugin
    options:
      budget_usd: 5.0
      pricing:
        my-local-model:
          input: 0.0
          output: 0.0
```

### Example 4: RAG Context Injection

A plugin that searches a knowledge base before each LLM call and injects relevant context.

```python
# File: plugins/rag_plugin.py

from kohakuterrarium.modules.plugin.base import BasePlugin, PluginContext


class RAGPlugin(BasePlugin):
    """Injects retrieved context before each LLM call."""

    name = "rag"
    priority = 30

    def __init__(self, options: dict | None = None):
        opts = options or {}
        self._index_path = opts.get("index_path", "")
        self._top_k = opts.get("top_k", 5)
        self._max_chars = opts.get("max_context_chars", 8000)
        self._last_query = ""
        self._searcher = None

    async def on_load(self, context: PluginContext) -> None:
        if not self._index_path:
            return
        # Lazy import to avoid startup cost if not needed
        from kohakuterrarium.session.embedding import create_embedder
        from kohakuterrarium.session.memory import SessionMemory

        embedder = create_embedder({"provider": "model2vec"})
        self._searcher = SessionMemory(self._index_path, embedder)

    async def on_event(self, event) -> None:
        """Capture user queries for RAG retrieval."""
        if getattr(event, "type", "") == "user_input":
            content = event.content if isinstance(event.content, str) else ""
            if content:
                self._last_query = content

    async def pre_llm_call(self, messages: list[dict], **kwargs) -> list[dict] | None:
        if not self._searcher or not self._last_query:
            return None

        results = await self._searcher.search(
            self._last_query, top_k=self._top_k
        )
        if not results:
            return None

        # Build context block from search results
        chunks = []
        total_len = 0
        for r in results:
            text = r.get("text", r.get("content", ""))
            if total_len + len(text) > self._max_chars:
                break
            source = r.get("source", "unknown")
            score = r.get("score", 0)
            chunks.append(f"[source: {source}, score: {score:.2f}]\n{text}")
            total_len += len(text)

        if not chunks:
            return None

        rag_block = (
            "<retrieved_context>\n"
            + "\n---\n".join(chunks)
            + "\n</retrieved_context>\n\n"
            "The above context was retrieved from the knowledge base. "
            "Use it if relevant. Do not mention the retrieval mechanism."
        )

        # Inject before the last user message
        modified = list(messages)
        for i in range(len(modified) - 1, -1, -1):
            if modified[i].get("role") == "user":
                content = modified[i]["content"]
                modified[i] = {**modified[i], "content": rag_block + content}
                break

        self._last_query = ""  # Consume query
        return modified
```

**Configuration:**

```yaml
plugins:
  - name: rag
    module: plugins.rag_plugin
    class: RAGPlugin
    options:
      index_path: ./knowledge/project_docs.kohakutr
      top_k: 5
      max_context_chars: 8000
```

### Example 5: Webhook Notifier

A plugin that sends HTTP notifications when key events occur.

```python
# File: plugins/webhook_notifier.py

from kohakuterrarium.modules.plugin.base import BasePlugin, PluginContext
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class WebhookNotifierPlugin(BasePlugin):
    """Sends webhook notifications on agent events."""

    name = "webhook_notifier"
    priority = 95  # Run after everything else

    def __init__(self, options: dict | None = None):
        opts = options or {}
        self._url = opts.get("url", "")
        self._events: set[str] = set(
            opts.get("events", ["agent_stop", "tool_error"])
        )
        self._headers = opts.get("headers", {})
        self._agent_name = ""

    async def on_load(self, context: PluginContext) -> None:
        self._agent_name = context.agent_name

    async def _send(self, event_type: str, data: dict) -> None:
        if not self._url:
            return
        import httpx

        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    self._url,
                    json={"event": event_type, "agent": self._agent_name, **data},
                    headers=self._headers,
                    timeout=10,
                )
        except Exception:
            logger.debug("Webhook send failed", exc_info=True)

    async def on_agent_stop(self) -> None:
        if "agent_stop" in self._events:
            await self._send("agent_stop", {})

    async def post_tool_execute(self, result, **kwargs):
        tool_name = kwargs.get("tool_name", "")
        error = getattr(result, "error", None)
        if "tool_error" in self._events and error:
            await self._send("tool_error", {
                "tool": tool_name,
                "error": str(error)[:500],
            })
        return None

    async def on_event(self, event) -> None:
        if "user_input" in self._events:
            if getattr(event, "type", "") == "user_input":
                content = event.content if isinstance(event.content, str) else ""
                await self._send("user_input", {"content": content[:200]})
```

**Configuration:**

```yaml
plugins:
  - name: webhook_notifier
    module: plugins.webhook_notifier
    class: WebhookNotifierPlugin
    options:
      url: https://hooks.slack.com/services/T.../B.../xxx
      events:
        - agent_stop
        - tool_error
      headers:
        Content-Type: application/json
```

---

## Part 4: Plugin Configuration

### Declaring Plugins in Creature Config

Plugins are declared in the `plugins` list of a creature's `config.yaml`:

```yaml
# creatures/my-agent/config.yaml
name: my-agent
model: claude-sonnet-4-20250514

tools:
  - bash
  - read
  - write
  - glob
  - grep

plugins:
  # Package plugin (installed via kt install)
  - name: cost_tracker
    module: my_package.plugins.cost_tracker
    class: CostTrackerPlugin
    options:
      budget_usd: 10.0

  # Local plugin (relative to agent folder or Python path)
  - name: permission_guard
    module: plugins.permission_guard
    class: PermissionGuardPlugin
    options:
      blocked_patterns:
        - "rm -rf"
        - "DROP TABLE"

  # Simple name reference (resolved from installed packages)
  - cost_tracker
```

Each entry can be either:

- **A string** -- the plugin name, resolved from installed packages.
- **A dict** with `name`, `module`, `class`, and optional `options`.

### Plugin Resolution Order

When loading a plugin by name, KohakuTerrarium searches:

1. **Installed packages** (`~/.kohakuterrarium/packages/`) -- plugins listed in `kohaku.yaml` manifests
2. **Python entry points** -- pip packages with `kohakuterrarium.plugins` entry point
3. **Explicit module/class** -- when `module` and `class` are specified in config

### Plugin Options

The `options` dict is passed to the plugin constructor. Your plugin receives it as a keyword argument:

```python
class MyPlugin(BasePlugin):
    name = "my_plugin"

    def __init__(self, options: dict | None = None):
        opts = options or {}
        self._threshold = opts.get("threshold", 100)
        self._enabled_features = opts.get("features", [])
```

### Runtime Control with /plugin

During a session, you can list, enable, and disable plugins:

```
/plugin                    -- List all plugins and their status
/plugin toggle my_plugin   -- Toggle a plugin on/off
/plugin enable my_plugin   -- Enable a specific plugin
/plugin disable my_plugin  -- Disable a specific plugin
```

Plugins discovered from installed packages (but not listed in your config) are registered as disabled. You can enable them at runtime with `/plugin enable`.

---

## Part 5: Terrarium-Level Plugins (Design Preview)

> **Status**: The terrarium-level plugin model is designed but not yet implemented. This section describes the planned architecture so you can see what will be possible. The creature-level plugin system described above is fully functional today.

### Two-Tier Model

Terrariums support plugins at two levels:

- **Terrarium plugins** observe all creatures. They see every channel message, every creature's LLM calls, and every tool execution across the entire terrarium.
- **Creature plugins** are scoped to a single creature, exactly as described above.

```yaml
# terrariums/my-team/config.yaml
terrarium:
  name: research-team

  # Terrarium-level plugins (observe everything)
  plugins:
    - name: coordination_observer
      module: plugins.coordination
      class: CoordinationObserverPlugin
    - cost_tracker

  creatures:
    - name: planner
      config: creatures/planner
      # Creature-level plugins (only this creature)
      plugins:
        - name: permission_guard
          options:
            blocked_tools: [bash]

    - name: researcher
      config: creatures/researcher
```

### Channel Message Hook

Terrarium-level plugins receive a `on_channel_message` callback whenever a message is sent on any terrarium channel:

```python
class CoordinationObserverPlugin(BasePlugin):
    """Tracks inter-creature communication patterns."""

    name = "coordination_observer"
    priority = 10

    def __init__(self, options: dict | None = None):
        self._message_log: list[dict] = []
        self._creature_stats: dict[str, dict] = {}

    async def on_channel_message(
        self, channel: str, sender: str, content: str
    ) -> None:
        import time

        self._message_log.append({
            "ts": time.time(),
            "channel": channel,
            "sender": sender,
            "length": len(content),
        })

        stats = self._creature_stats.setdefault(
            sender, {"sent": 0, "chars": 0}
        )
        stats["sent"] += 1
        stats["chars"] += len(content)

    async def on_agent_stop(self) -> None:
        from kohakuterrarium.utils.logging import get_logger

        logger = get_logger(__name__)
        logger.info("=== Coordination Summary ===")
        for creature, stats in self._creature_stats.items():
            logger.info(
                "Creature stats",
                creature=creature,
                messages=stats["sent"],
                chars=stats["chars"],
            )
        logger.info("Total messages: %d", len(self._message_log))
```

---

## Part 6: Distributing Plugins

### Package Manifest

To distribute plugins via `kt install`, include them in your `kohaku.yaml`:

```yaml
# kohaku.yaml
name: my-security-pack
version: "1.0.0"
description: "Security plugins for KohakuTerrarium agents"

plugins:
  - name: permission_guard
    module: my_security_pack.plugins.guard
    class: PermissionGuardPlugin

  - name: audit_logger
    module: my_security_pack.plugins.audit
    class: AuditLoggerPlugin
```

Users install with:

```bash
kt install ./my-security-pack
# or from a git URL:
kt install https://github.com/user/my-security-pack.git
```

After installation, the plugins appear in `/plugin` listing (disabled by default). Users enable them in their creature config or at runtime.

### Python Entry Points

For pip-installable packages, declare plugins as entry points in `pyproject.toml`:

```toml
[project.entry-points."kohakuterrarium.plugins"]
permission_guard = "my_security_pack.plugins.guard:PermissionGuardPlugin"
audit_logger = "my_security_pack.plugins.audit:AuditLoggerPlugin"
```

These are discovered automatically at agent startup.

### Best Practices

1. **Keep plugins focused.** One plugin, one concern. A cost tracker should not also do permission checking.

2. **Use priority thoughtfully.** Security plugins (permission guard, sandbox) should use low numbers (1-10) so they run first. Observation plugins (audit log, cost tracker) should also use low numbers but for a different reason -- they want to see everything before it is transformed. Transformation plugins (RAG injection, context enrichment) should use mid-range numbers (20-40). Post-processing plugins (webhook, safety filter) should use high numbers (80-95).

3. **Handle errors gracefully.** Never let your plugin crash the agent. The manager catches exceptions, but a plugin that frequently errors degrades the experience.

4. **Use `PluginContext.get_state` / `set_state`** for persistence instead of writing your own files. State is automatically namespaced and survives session resume.

5. **Return `None` from hooks you do not modify.** Returning the original value unchanged is different from returning `None` -- the former forces all downstream plugins to see your "modified" value, while `None` signals "no change" and is more efficient.

6. **Test with `PluginBlockError` carefully.** Only raise it in `pre_tool_execute` and `pre_subagent_run`. Raising it elsewhere has no special meaning and is treated as a regular exception (logged and skipped).

7. **Do not access agent internals directly.** Use the `PluginContext` methods. The `_agent` attribute on context exists for internal use only and may change without notice.

---

## Quick Reference

### Hook Cheat Sheet

```python
from kohakuterrarium.modules.plugin.base import BasePlugin, PluginBlockError, PluginContext

class MyPlugin(BasePlugin):
    name = "my_plugin"
    priority = 50

    # -- Lifecycle --
    async def on_load(self, context: PluginContext) -> None: ...
    async def on_unload(self) -> None: ...

    # -- LLM hooks --
    async def pre_llm_call(self, messages: list[dict], **kwargs) -> list[dict] | None: ...
    async def post_llm_call(self, messages, response, usage, **kwargs) -> None: ...

    # -- Tool hooks --
    async def pre_tool_execute(self, args: dict, **kwargs) -> dict | None: ...
    async def post_tool_execute(self, result, **kwargs) -> Any | None: ...

    # -- Sub-agent hooks --
    async def pre_subagent_run(self, task: str, **kwargs) -> str | None: ...
    async def post_subagent_run(self, result, **kwargs) -> Any | None: ...

    # -- Callbacks (fire-and-forget) --
    async def on_agent_start(self) -> None: ...
    async def on_agent_stop(self) -> None: ...
    async def on_event(self, event) -> None: ...
    async def on_interrupt(self) -> None: ...
    async def on_task_promoted(self, job_id: str, tool_name: str) -> None: ...
    async def on_compact_start(self, context_length: int) -> None: ...
    async def on_compact_end(self, summary: str, messages_removed: int) -> None: ...
```

### Minimal Plugin Template

```python
from kohakuterrarium.modules.plugin.base import BasePlugin, PluginContext


class MyPlugin(BasePlugin):
    name = "my_plugin"
    priority = 50

    def __init__(self, options: dict | None = None):
        opts = options or {}
        # Store configuration from options

    async def on_load(self, context: PluginContext) -> None:
        # Initialize resources, restore state
        pass

    async def on_unload(self) -> None:
        # Clean up resources
        pass
```

### Minimal Config

```yaml
plugins:
  - name: my_plugin
    module: plugins.my_plugin
    class: MyPlugin
    options:
      key: value
```
