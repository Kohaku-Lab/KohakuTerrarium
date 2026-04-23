"""Plugin protocol and base class for KohakuTerrarium.

Two extension patterns:

**Pre/post hooks** — wrap existing methods via decoration at init time.
The manager runs pre_* hooks before the real call (can transform input
or block), then the real call, then post_* hooks (can transform output).
All plugins run linearly by priority, not nested.

**Callbacks** — fire-and-forget notifications with data.

Error handling:
  - PluginBlockError in pre_tool_execute: blocks execution, becomes tool result
  - Regular Exception: logged, plugin skipped, execution continues
"""

import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.core.agent import Agent
    from kohakuterrarium.core.compact import CompactManager
    from kohakuterrarium.core.controller import Controller
    from kohakuterrarium.core.registry import Registry
    from kohakuterrarium.core.scratchpad import Scratchpad
    from kohakuterrarium.modules.subagent.manager import SubAgentManager
    from kohakuterrarium.session.memory import SessionMemory
    from kohakuterrarium.session.store import SessionStore


logger = get_logger(__name__)


class PluginBlockError(Exception):
    """Raised by a plugin to block tool/sub-agent execution.

    The error message is returned to the model as the tool result.
    Only meaningful in ``pre_tool_execute`` and ``pre_subagent_run``.
    """


class PluginContext:
    """Context provided to plugins on load.

    Public accessor surface (read-only properties):

    * ``host_agent`` — the Agent this plugin is attached to.
    * ``session_store`` — persistence layer (may be ``None``).
    * ``session_memory`` — FTS/vector memory (may be ``None`` if disabled).
    * ``registry`` — tool/sub-agent registry.
    * ``scratchpad`` — session-scoped key/value store.
    * ``compact_manager`` — auto-compact controller (may be ``None``).
    * ``controller`` — LLM conversation loop.
    * ``subagent_manager`` — sub-agent lifecycle manager.

    Helpers:

    * ``switch_model(name)`` — hot-swap the LLM profile.
    * ``inject_event(event)`` — push a ``TriggerEvent`` into the queue.
    * ``inject_message_before_llm(role, content)`` — queue a message to be
      prepended to the next LLM call.
    * ``get_state(key)`` / ``set_state(key, value)`` — plugin-scoped state.

    The private ``_agent`` attribute still works but emits a
    ``DeprecationWarning`` once per ``(plugin, context)`` pair.
    One-minor-version compatibility window (per
    ``plans/harness/extension-point-decisions.md`` cluster 7.2), after
    which the alias is removed.
    """

    def __init__(
        self,
        agent_name: str = "",
        working_dir: Path | None = None,
        session_id: str = "",
        model: str = "",
        _host_agent: Any = None,
        _plugin_name: str = "",
        *,
        _agent: Any = None,
    ) -> None:
        self.agent_name = agent_name
        self.working_dir = working_dir if working_dir is not None else Path.cwd()
        self.session_id = session_id
        self.model = model
        # Accept legacy ``_agent=`` kwarg (emits no warning at construction
        # time — the warning fires on access). Field renames happened in
        # the H.5 refactor; the new canonical storage is ``_host_agent``.
        if _host_agent is None and _agent is not None:
            _host_agent = _agent
        self._host_agent = _host_agent
        self._plugin_name = _plugin_name
        self._deprecation_warned: set[str] = set()

    def __repr__(self) -> str:
        return (
            f"PluginContext(agent_name={self.agent_name!r}, "
            f"session_id={self.session_id!r}, model={self.model!r}, "
            f"plugin={self._plugin_name!r})"
        )

    # ── Backward-compat alias for ``_agent`` ───────────────────────────

    @property
    def _agent(self) -> Any:
        """Deprecated back-ref to the host Agent.

        Kept for one minor version. Use ``host_agent`` (or the specific
        typed properties like ``session_store`` / ``registry``) instead.
        """
        self._warn_deprecated_agent_access()
        return self._host_agent

    @_agent.setter
    def _agent(self, value: Any) -> None:
        # Allow in-place assignment. Routes to the real storage slot.
        self._host_agent = value

    def _warn_deprecated_agent_access(self) -> None:
        """Emit DeprecationWarning once per plugin lifetime."""
        key = self._plugin_name or "<anonymous>"
        if key in self._deprecation_warned:
            return
        self._deprecation_warned.add(key)
        message = (
            f"PluginContext._agent is deprecated; plugin '{key}' should "
            "use context.host_agent (or context.session_store / "
            "context.registry / context.scratchpad / etc.). The alias "
            "will be removed in the next minor release."
        )
        warnings.warn(message, DeprecationWarning, stacklevel=3)
        logger.info(
            "Plugin using deprecated PluginContext._agent",
            plugin_name=key,
        )

    # ── Public accessors ───────────────────────────────────────────────

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
        """SessionMemory for FTS/vector search, or ``None`` if disabled.

        Agents that do not enable memory indexing return ``None``.
        Plugins that need a memory object may construct their own via
        ``session.memory.SessionMemory`` using ``session_store``.
        """
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

    # ── Helpers ────────────────────────────────────────────────────────

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

    def inject_message_before_llm(self, role: str, content: str | list) -> None:
        """Queue a message to be prepended to the next LLM call.

        The message is drained by the controller just before
        ``pre_llm_call`` hooks run, so all registered plugins observe
        the injected message in ``messages`` too. If the host agent is
        not yet bound, the call is a no-op.
        """
        controller = self.controller
        if controller is None:
            return
        queue = getattr(controller, "_pending_injections", None)
        if queue is None:
            queue = []
            controller._pending_injections = queue
        queue.append({"role": role, "content": content})

    def get_state(self, key: str) -> Any:
        """Read plugin-scoped state from session store."""
        store = self.session_store
        if store is None:
            return None
        return store.state.get(f"plugin:{self._plugin_name}:{key}")

    def set_state(self, key: str, value: Any) -> None:
        """Write plugin-scoped state to session store."""
        store = self.session_store
        if store is None:
            return
        store.state[f"plugin:{self._plugin_name}:{key}"] = value


class BasePlugin:
    """Base class for plugins. Override only what you need.

    Pre/post hooks run linearly by priority around real methods:
        pre_xxx  → real method → post_xxx

    Return None from pre/post to keep the value unchanged.
    Return a value to replace it for the next plugin in the chain.
    """

    name: str = "unnamed"
    priority: int = 50  # Lower = runs first in pre, last in post

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
        return None

    async def post_llm_call(
        self, messages: list[dict], response: str, usage: dict, **kwargs
    ) -> None:
        """After LLM call. Observation — cannot modify response.

        kwargs: model (str)
        """

    # ── Tool hooks ──

    async def pre_tool_execute(self, args: dict, **kwargs) -> dict | None:
        """Before tool execution. Return modified args or None.

        kwargs: tool_name (str), job_id (str)
        Raise PluginBlockError to prevent execution.
        """
        return None

    async def post_tool_execute(self, result: Any, **kwargs) -> Any | None:
        """After tool execution. Return modified result or None.

        kwargs: tool_name (str), job_id (str), args (dict)
        """
        return None

    # ── Sub-agent hooks ──

    async def pre_subagent_run(self, task: str, **kwargs) -> str | None:
        """Before sub-agent run. Return modified task or None.

        kwargs: name (str), job_id (str), is_background (bool)
        Raise PluginBlockError to prevent execution.
        """
        return None

    async def post_subagent_run(self, result: Any, **kwargs) -> Any | None:
        """After sub-agent run. Return modified result or None.

        kwargs: name (str), job_id (str)
        """
        return None

    # ── Callbacks (fire-and-forget) ──

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
        """Called before context compaction.

        Return ``False`` to veto this compaction cycle — the manager
        will skip compaction entirely and ``on_compact_end`` will not
        fire. Any other return value (``None``, ``True``) proceeds.

        If multiple plugins implement this hook, compaction proceeds
        only when no plugin returns ``False``.
        """
        return None

    async def on_compact_end(self, summary: str, messages_removed: int) -> None:
        """Called after context compaction (only when not vetoed)."""
