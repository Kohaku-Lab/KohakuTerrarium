"""Plugin protocol and base class for KohakuTerrarium.

Two extension patterns:

**Pre/post hooks** — wrap existing methods via decoration at init time.
The manager runs pre_* hooks before the real call (can transform input
or block), then the real call, then post_* hooks (can transform output).
All plugins run linearly by priority, not nested.

**Callbacks** — fire-and-forget notifications with data.

Error handling:
  - PluginBlockError in pre_tool_execute / pre_tool_dispatch:
    blocks execution, becomes tool result
  - Regular Exception: logged, plugin skipped, execution continues
"""

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from kohakuterrarium.modules.plugin.option_validation import (
    validate_plugin_options,
)
from kohakuterrarium.plugins_context import (
    spawn_child_agent as _default_spawn_child_agent_helper,
)
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
    Only meaningful in ``pre_tool_execute``, ``pre_tool_dispatch``, and
    ``pre_subagent_run``.
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

    The deprecated ``_agent`` alias was removed in Cluster 2 (β) of the
    extension-point work. Use ``host_agent`` (or the specific typed
    properties above) instead.
    """

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

    async def emit(self, event: Any) -> None:
        """Emit a Phase B :class:`OutputEvent` through the agent's
        output bus. Display-only — for interactive events that need a
        reply, use :meth:`emit_and_wait` instead.
        """
        agent = self._host_agent
        if agent is None:
            return
        router = getattr(agent, "output_router", None)
        if router is None:
            return
        await router.emit(event)

    async def emit_and_wait(self, event: Any, timeout_s: float | None = None) -> Any:
        """Emit an interactive :class:`OutputEvent` and await a
        :class:`UIReply`. Returns the reply (with ``action_id``,
        ``values``) or a ``UIReply`` whose ``action_id`` is
        ``"__timeout__"`` on timeout.

        Plugins commonly use this in ``pre_tool_execute`` to gate a
        tool call on user consent. See ``builtins/plugins/permgate.py``
        for the canonical exemplar.
        """
        agent = self._host_agent
        if agent is None:
            raise RuntimeError("PluginContext is not attached to an agent")
        router = getattr(agent, "output_router", None)
        if router is None:
            raise RuntimeError("Agent has no output_router")
        return await router.emit_and_wait(event, timeout_s=timeout_s)

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

    # ── Wave F: spawn child agent ─────────────────────────────────────

    def spawn_child_agent(
        self,
        config_path_or_dict: "str | dict[str, Any]",
        role: str = "child",
    ) -> "Agent":
        """Build a child :class:`Agent` and attach it to the host session.

        Wave F sugar: convenience wrapper over
        ``Agent.from_path(...)`` / ``Agent(config)`` plus
        :func:`kohakuterrarium.session.attachment_service.attach_agent_to_session`.
        The resulting agent writes its events under
        ``<host>:attached:plugin:<plugin_name>/<role>:<attach_seq>:e<seq>``
        in the host session's backing store.

        Returns the constructed :class:`Agent` — the caller owns its
        lifecycle (``await agent.start()`` / ``agent.inject_input(...)``
        / ``await agent.stop()``).
        """
        helper = self._spawn_child_agent_helper
        return helper(self, config_path_or_dict, role)


class BasePlugin:
    """Base class for plugins. Override only what you need.

    Pre/post hooks run linearly by priority around real methods:
        pre_xxx  → real method → post_xxx

    Return None from pre/post to keep the value unchanged.
    Return a value to replace it for the next plugin in the chain.

    Declarative gating via ``applies_to``:
        class MyPlugin(BasePlugin):
            applies_to = {
                "agent_names": ["swe"],        # list of exact matches
                "model_patterns": ["^codex/"], # list of regex strings
            }

    Override ``should_apply(context)`` for dynamic gating; subclasses
    typically call ``super().should_apply(context)`` first.
    """

    name: str = "unnamed"
    priority: int = 50  # Lower = runs first in pre, last in post

    # Declarative filter. Empty dict / missing = apply to all contexts.
    applies_to: dict[str, list[str]] = {}

    def __init__(self) -> None:
        # Pre-compile model_patterns once. Evaluated before every hook
        # call (see cluster 2.5 of the extension-point spec).
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
        # Canonical store for runtime-mutable options. Plugins that
        # support runtime configuration should override
        # :meth:`option_schema` and populate this dict in their own
        # ``__init__``, then call :meth:`refresh_options` to derive
        # any internal state. Mutation goes through :meth:`set_options`
        # which validates against the schema.
        self.options: dict[str, Any] = {}

    # ── Options (runtime-mutable configuration) ──

    @classmethod
    def option_schema(cls) -> dict[str, dict[str, Any]]:
        """Return this plugin's option schema for runtime introspection.

        Default returns ``{}`` — plugins with no schema-described
        options. Plugins that want to be runtime-configurable from a
        UI override this. See
        :mod:`kohakuterrarium.modules.plugin.option_validation` for
        the schema shape.
        """
        return {}

    def get_options(self) -> dict[str, Any]:
        """Return a copy of the current option values."""
        return dict(self.options)

    def set_options(self, values: dict[str, Any]) -> dict[str, Any]:
        """Validate, store, and re-apply option overrides.

        Validates ``values`` against :meth:`option_schema`, merges into
        :attr:`options`, then calls :meth:`refresh_options` so the
        plugin can re-derive any internal state. Returns the full
        post-merge options dict.

        Raises :class:`PluginOptionError` (a ``ValueError``) on
        invalid input — unknown keys, wrong types, out-of-range values.
        """
        schema = type(self).option_schema()
        cleaned = validate_plugin_options(
            getattr(self, "name", "?"), values or {}, schema or {}
        )
        for key, value in cleaned.items():
            self.options[key] = value
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

    def refresh_options(self) -> None:
        """Re-derive internal state from :attr:`options`.

        Called after a successful :meth:`set_options`. Default no-op;
        plugins with derived state (caches, compiled regexes, etc.)
        override to re-apply :attr:`options` to their internal fields.
        """
        return None

    # ── Gating ──

    def should_apply(self, context: PluginContext) -> bool:
        """Return True if this plugin should run for the given context.

        Default implementation consults the declarative ``applies_to``
        filter. Override to add dynamic checks — call
        ``super().should_apply(context)`` first to keep the declarative
        gate in effect.
        """
        applies_to = self.applies_to or {}
        names = applies_to.get("agent_names") or []
        if names and context.agent_name not in names:
            return False
        if self._model_pattern_res:
            model = context.model or ""
            if not any(p.search(model) for p in self._model_pattern_res):
                return False
        return True

    # ── Prompt contributions ──

    def get_prompt_content(self, context: PluginContext) -> str | None:
        """Contribute prose to the runtime system prompt.

        Return ``None`` or an empty string to contribute nothing. Runtime
        prompt contributions are collected in plugin priority order and
        inserted between tool guidance and framework hints.
        """
        return None

    # ── Controller / package commands ──

    def contribute_commands(self) -> dict[str, Any]:
        """Return a mapping of ``##name##`` → ``BaseCommand`` instance.

        Called once per plugin after ``on_load``. Built-in command names
        (``info``, ``read_job``, ``jobs``, ``wait``) are protected —
        attempting to register one without ``override=True`` raises.
        """
        return {}

    # ── Termination voting ──

    def contribute_termination_check(
        self,
    ) -> "Callable[[Any], Any] | None":
        """Return a checker function that votes on termination each turn.

        The checker is a callable ``fn(context: TerminationContext) ->
        TerminationDecision | None``. Return ``None`` (default) to not
        participate in termination voting.

        When any plugin's checker returns ``TerminationDecision(
        should_stop=True, reason=...)``, the run stops (any-can-stop
        per cluster 3.3).
        """
        return None

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
    ) -> str | None:
        """After LLM call. Return a rewritten response string or None.

        Chain-with-return semantics: each plugin sees the previous
        plugin's rewrite. ``None`` means pass through unchanged.
        Finalize-only — one fire per complete turn with the full
        assistant content.

        kwargs: model (str)
        """
        return None

    # ── Tool hooks ──

    async def pre_tool_dispatch(self, call: Any, context: PluginContext) -> Any | None:
        """Before the executor sees a tool call.

        Fires after the parser emits a ``ToolCallEvent`` and before the
        executor submits it. Return a new/modified ``ToolCallEvent`` to
        rewrite (can change tool name, args, or both). Return ``None``
        to pass through. Raise ``PluginBlockError`` to veto the call;
        the error text becomes the tool result.

        Chain linearly by priority; each plugin sees the output of the
        previous one.
        """
        return None

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
