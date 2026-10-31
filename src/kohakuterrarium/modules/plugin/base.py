"""Plugin protocol and base class for KohakuTerrarium.

Hooks run linearly by priority; callbacks observe lifecycle events without nesting.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from kohakuterrarium.core.conversation.pinned import pin_message, unpin_message
from kohakuterrarium.modules.ui_owner import UIOwnerMixin
from kohakuterrarium.modules.plugin.option_validation import (
    validate_plugin_options,
)
from kohakuterrarium.plugins_context import (
    spawn_child_agent as _default_spawn_child_agent_helper,
)
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.core.agent import Agent
    from kohakuterrarium.core.compact.manager import CompactManager
    from kohakuterrarium.core.controller.controller import Controller
    from kohakuterrarium.core.registry import Registry
    from kohakuterrarium.core.scratchpad import Scratchpad
    from kohakuterrarium.modules.subagent.manager import SubAgentManager
    from kohakuterrarium.modules.tool.base import BaseTool
    from kohakuterrarium.session.memory.index import SessionMemory
    from kohakuterrarium.session.store import SessionStore


logger = get_logger(__name__)


class PluginBlockError(Exception):
    """Block a tool or sub-agent call and expose the reason as its result."""


class PluginLockedError(ValueError):
    """A runtime request tried to disable or reconfigure a locked plugin."""


@dataclass(frozen=True)
class ToolVisibility:
    """Per-request tool catalog restriction contributed by a plugin.

    ``None`` means the category stays unrestricted; an empty ``frozenset``
    hides every member of that category from the current request.
    """

    allowed_tools: frozenset[str] | None = None
    allowed_subagents: frozenset[str] | None = None

    def is_unrestricted(self) -> bool:
        """Return True when neither tool nor sub-agent names are restricted."""
        return self.allowed_tools is None and self.allowed_subagents is None


class PluginContext:
    """Expose host runtime state and safe helper operations to a loaded plugin."""

    def __init__(
        self,
        agent_name: str = "",
        working_dir: Path | None = None,
        session_id: str = "",
        model: str = "",
        _host_agent: Any = None,
        _plugin_name: str = "",
        _spawn_child_agent_helper: Callable[..., Any] | None = None,
    ) -> None:
        self.agent_name = agent_name
        self.working_dir = working_dir if working_dir is not None else Path.cwd()
        self.session_id = session_id
        self.model = model
        self._host_agent = _host_agent
        self._plugin_name = _plugin_name
        self._spawn_child_agent_helper = (
            _spawn_child_agent_helper or _default_spawn_child_agent_helper
        )

    def __repr__(self) -> str:
        return (
            f"PluginContext(agent_name={self.agent_name!r}, "
            f"session_id={self.session_id!r}, model={self.model!r}, "
            f"plugin={self._plugin_name!r})"
        )

    @property
    def host_agent(self) -> "Agent | None":
        """The Agent this plugin is attached to (``None`` pre-load)."""
        return self._host_agent

    @property
    def session_store(self) -> "SessionStore | None":
        """SessionStore for persistent state, or ``None`` if not attached."""
        agent = self._host_agent
        if agent is None:
            return None
        return getattr(agent, "session_store", None)

    @property
    def session_memory(self) -> "SessionMemory | None":
        """Return indexed session memory when the host enabled it."""
        agent = self._host_agent
        if agent is None:
            return None
        return getattr(agent, "session_memory", None)

    @property
    def registry(self) -> "Registry | None":
        """Tool/sub-agent registry."""
        agent = self._host_agent
        if agent is None:
            return None
        return getattr(agent, "registry", None)

    @property
    def scratchpad(self) -> "Scratchpad | None":
        """Session-scoped key/value scratchpad."""
        agent = self._host_agent
        if agent is None:
            return None
        return getattr(agent, "scratchpad", None)

    @property
    def compact_manager(self) -> "CompactManager | None":
        """Auto-compact controller (may be ``None``)."""
        agent = self._host_agent
        if agent is None:
            return None
        return getattr(agent, "compact_manager", None)

    @property
    def controller(self) -> "Controller | None":
        """LLM conversation loop."""
        agent = self._host_agent
        if agent is None:
            return None
        return getattr(agent, "controller", None)

    @property
    def subagent_manager(self) -> "SubAgentManager | None":
        """Sub-agent lifecycle manager."""
        agent = self._host_agent
        if agent is None:
            return None
        return getattr(agent, "subagent_manager", None)

    def switch_model(self, name: str) -> str:
        """Switch the LLM model. Returns resolved model name."""
        agent = self._host_agent
        if agent is not None and hasattr(agent, "switch_model"):
            return agent.switch_model(name)
        return ""

    def inject_event(self, event: Any) -> None:
        """Push a trigger event into the agent's event queue."""
        agent = self._host_agent
        if agent is not None and hasattr(agent, "controller"):
            agent.controller.push_event_sync(event)

    async def emit(self, event: Any) -> None:
        """Emit a display-only output event through the host router."""
        agent = self._host_agent
        if agent is None:
            return
        router = getattr(agent, "output_router", None)
        if router is None:
            return
        await router.emit(event)

    async def emit_and_wait(self, event: Any, timeout_s: float | None = None) -> Any:
        """Emit an interactive output event and await its reply or timeout marker."""
        agent = self._host_agent
        if agent is None:
            raise RuntimeError("PluginContext is not attached to an agent")
        router = getattr(agent, "output_router", None)
        if router is None:
            raise RuntimeError("Agent has no output_router")
        return await router.emit_and_wait(event, timeout_s=timeout_s)

    @property
    def widgets(self) -> Any:
        """The host agent's :class:`WidgetService`, or ``None`` when unattached."""
        router = getattr(self._host_agent, "output_router", None)
        return getattr(router, "widgets", None)

    def inject_message_before_llm(self, role: str, content: str | list) -> None:
        """Queue a message before pre-call hooks so every plugin observes it."""
        controller = self.controller
        if controller is None:
            return
        queue = getattr(controller, "_pending_injections", None)
        if queue is None:
            queue = []
            controller._pending_injections = queue
        queue.append({"role": role, "content": content})

    def pin_message(self, key: str, role: str, content: str | list) -> bool:
        """Pin a message right after the system prompt; compaction keeps it.

        Replaces an existing pin with the same key. Returns False when the
        context is not bound to an agent.
        """
        agent = self._host_agent
        if agent is None:
            return False
        pin_message(agent, key, role, content)
        return True

    def unpin_message(self, key: str) -> bool:
        """Remove the pin ``key``; return whether it existed."""
        agent = self._host_agent
        return unpin_message(agent, key) if agent is not None else False

    def _state_key(self, key: str) -> str:
        if not self.agent_name:
            return f"plugin:{self._plugin_name}:{key}"
        identity = json.dumps(
            [self.agent_name, self._plugin_name, key], separators=(",", ":")
        )
        return f"plugin-state:{identity}"

    def get_state(self, key: str) -> Any:
        """Read creature/plugin state, falling back to an unmigrated legacy key."""
        store = self.session_store
        if store is None:
            return None
        try:
            return store.state[self._state_key(key)]
        except KeyError:
            return store.state.get(f"plugin:{self._plugin_name}:{key}")

    def set_state(self, key: str, value: Any) -> None:
        """Write private creature/plugin state without changing legacy records."""
        store = self.session_store
        if store is None:
            return
        store.state[self._state_key(key)] = value

    def spawn_child_agent(
        self,
        config_path_or_dict: "str | dict[str, Any]",
        role: str = "child",
    ) -> "Agent":
        """Build a child agent attached to the host session under the plugin role."""
        helper = self._spawn_child_agent_helper
        return helper(self, config_path_or_dict, role)


class BasePlugin(UIOwnerMixin):
    """Provide no-op hooks, declarative gating, and runtime option handling."""

    name: str = "unnamed"
    priority: int = 50
    inherit_fork_policy: bool = False

    # An empty applicability filter enables the plugin for every context.
    applies_to: dict[str, list[str]] = {}
    # Renderer ids whose attach or detach changes get_prompt_content.
    prompt_renderers: frozenset[str] = frozenset()
    # Names ``<doc_prefix>:<topic>`` that ``info`` resolves through doc_topic.
    doc_prefix: str = ""

    def __init__(self) -> None:
        # Compile once because applicability is evaluated for every hook.
        self._model_pattern_res: list[re.Pattern[str]] = []
        for pattern in self.applies_to.get("model_patterns", []) or []:
            try:
                self._model_pattern_res.append(re.compile(pattern))
            except re.error as exc:
                logger.warning(
                    "Plugin model_patterns regex failed to compile; skipping",
                    plugin_name=getattr(self, "name", "?"),
                    pattern=str(pattern),
                    error=str(exc),
                )
        # Runtime option mutation is validated before derived state is refreshed.
        self.options: dict[str, Any] = {}

    def fork_tool_policy(self) -> Callable[..., Any]:
        """Return a validation-only pre-tool hook for one context-fork child.

        Opted-in hooks must not mutate parent state or transform arguments.
        Stateful policies override this factory to allocate child-private state.
        Parent lifecycle and other hooks are not inherited.
        """
        return self.pre_tool_execute

    @classmethod
    def option_schema(cls) -> dict[str, dict[str, Any]]:
        """Return the schema used to validate and render runtime options."""
        return {}

    def _options_dict(self) -> dict[str, Any]:
        """Return an option store even for subclasses that skip base initialization."""
        opts = getattr(self, "options", None)
        if not isinstance(opts, dict):
            opts = {}
            self.options = opts
        return opts

    def get_options(self) -> dict[str, Any]:
        """Return a copy of the current option values."""
        return dict(self._options_dict())

    def set_options(self, values: dict[str, Any]) -> dict[str, Any]:
        """Validate and merge option overrides before refreshing derived state."""
        schema = type(self).option_schema()
        cleaned = validate_plugin_options(
            getattr(self, "name", "?"), values or {}, schema or {}
        )
        opts = self._options_dict()
        for key, value in cleaned.items():
            opts[key] = value
        try:
            self.refresh_options()
        except Exception as e:  # pragma: no cover — defensive
            logger.warning(
                "Plugin refresh_options raised after set_options",
                plugin_name=getattr(self, "name", "?"),
                error=str(e),
                exc_info=True,
            )
        return self.get_options()

    def refuses_disable(self) -> bool:
        """Whether a runtime request to disable this plugin must be refused."""
        return False

    def refresh_options(self) -> None:
        """Re-derive internal state after validated option changes."""
        return None

    def should_apply(self, context: PluginContext) -> bool:
        """Evaluate declarative agent-name and model-pattern applicability."""
        applies_to = self.applies_to or {}
        names = applies_to.get("agent_names") or []
        if names and context.agent_name not in names:
            return False
        if self._model_pattern_res:
            model = context.model or ""
            if not any(p.search(model) for p in self._model_pattern_res):
                return False
        return True

    def get_prompt_content(self, context: PluginContext) -> str | None:
        """Return optional runtime prompt prose collected in plugin priority order."""
        return None

    def presentation_contribution(self, context: PluginContext) -> str | None:
        """Return this plugin's part of ``## Presenting your answer``, or ``None``.

        The section sits right after the base prompt and teaches the forms an
        answer can take in the attached clients (inline components, apps).
        Declare the renderers the part depends on in ``prompt_renderers``.
        """
        return None

    def doc_topics(self, context: PluginContext | None) -> list[str]:
        """Topic names ``info`` can load as ``<doc_prefix>:<topic>``."""
        return []

    def doc_topic(self, topic: str, context: PluginContext | None) -> str | None:
        """Documentation for one topic under ``doc_prefix``, or ``None`` if unknown.

        ``info("<doc_prefix>:<topic>")`` returns it, through the tool and the
        text-format ``info`` command alike. Advertise the prefix in the
        plugin's own prompt content.
        """
        return None

    def get_tool_visibility(self, context: PluginContext) -> ToolVisibility | None:
        """Return per-request tool catalog restrictions, or None for no opinion."""
        return None

    def runtime_services(self, context: Any) -> dict[str, Any]:
        """Expose optional plugin capabilities through each tool context."""
        return {}

    def contribute_commands(self) -> dict[str, Any]:
        """Return model-facing controller commands contributed by this plugin."""
        return {}

    def contribute_user_commands(self) -> dict[str, Any]:
        """Return human-facing slash commands subject to the collision policy."""
        return {}

    def contribute_tools(self) -> "list[BaseTool]":
        """Return tool instances registered while this plugin is enabled.

        Called once per plugin instance; the tools join the registry, executor
        and prompt on load or enable and leave on disable or removal. A name
        that clashes with another tool fails the load.
        """
        return []

    def contribute_termination_check(
        self,
    ) -> "Callable[[Any], Any] | None":
        """Return an optional per-turn checker whose stop vote ends the run."""
        return None

    async def on_load(self, context: PluginContext) -> None:
        """Called when plugin is loaded."""

    async def on_unload(self) -> None:
        """Called when agent shuts down."""

    async def pre_llm_call(self, messages: list[dict], **kwargs) -> list[dict] | None:
        """Optionally transform messages before a model call."""
        return None

    async def post_llm_call(
        self, messages: list[dict], response: str, usage: dict, **kwargs
    ) -> str | None:
        """Optionally rewrite the complete response after a model call."""
        return None

    async def pre_tool_dispatch(self, call: Any, context: PluginContext) -> Any | None:
        """Rewrite or block a parsed tool call before executor submission."""
        return None

    async def pre_tool_execute(self, args: dict, **kwargs) -> dict | None:
        """Optionally transform tool arguments or block execution."""
        return None

    async def post_tool_execute(self, result: Any, **kwargs) -> Any | None:
        """Optionally transform a completed tool result."""
        return None

    async def pre_subagent_run(self, task: str, **kwargs) -> str | None:
        """Optionally transform a sub-agent task or block its run."""
        return None

    async def post_subagent_run(self, result: Any, **kwargs) -> Any | None:
        """Optionally transform a completed sub-agent result."""
        return None

    async def on_agent_start(self) -> None:
        """Called after agent.start() completes."""

    async def on_agent_stop(self) -> None:
        """Called before agent.stop() begins."""

    async def on_event(self, event: Any) -> None:
        """Called on incoming trigger event. Observation only."""

    async def on_interrupt(self) -> None:
        """Called when user interrupts the agent."""

    async def on_task_promoted(self, job_id: str, tool_name: str) -> None:
        """Called when a direct task is promoted to background."""

    async def on_compact_start(self, context_length: int) -> bool | None:
        """Return false to veto the current compaction cycle."""
        return None

    async def on_compact_end(self, summary: str, messages_removed: int) -> None:
        """Called after context compaction (only when not vetoed)."""
