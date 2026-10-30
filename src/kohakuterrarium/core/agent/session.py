"""Agent session surface: store attachment, session info, prompt and state views."""

from typing import Any

from kohakuterrarium.core.agent.history.compact import (
    restore_compact_state_from_session,
)
from kohakuterrarium.core.agent.observability import (
    build_session_info as _build_session_info,
    wire_plugin_hook_timing,
    wire_scratchpad_observer,
)
from kohakuterrarium.core.conversation.context_snapshot import (
    ContextSnapshot,
    capture_agent_context,
)
from kohakuterrarium.core.conversation.pinned import (
    sync_with_store as sync_pinned_with_store,
)
from kohakuterrarium.core.token_ledger import context_window
from kohakuterrarium.session.attach.agent import attach_to_session as _attach_to_session
from kohakuterrarium.session.attach.agent import (
    detach_from_session as _detach_from_session,
)
from kohakuterrarium.session.output.output import SessionOutput
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class AgentSessionMixin:
    """Persist the agent into a ``SessionStore`` and report its state."""

    def _init_session_state(self) -> None:
        # Session persistence (set externally via attach_session_store)
        self.session_store: Any = None
        self._session_output: Any = None
        self._pending_resume_events: list[dict] | None = None

    def _publish_session_info(self) -> None:
        """Publish session info to output (for TUI session panel).

        Handles session ID retrieval, LLM profile persistence,
        prompt cache key, embedding config, and session_info notification.
        """
        session_id = ""
        if self.session_store:
            try:
                meta = self.session_store.load_meta()
                session_id = meta.get("session_id", "")
            except Exception as e:
                logger.warning(
                    "Failed to load session meta for session_id",
                    error=str(e),
                    exc_info=True,
                )

        # Save selected preset/profile name to session state for resume
        selected_llm_name = self._llm_selector or self.config.llm_profile or ""
        if self.session_store and selected_llm_name:
            self.session_store.state[f"{self.config.name}:llm_profile"] = (
                selected_llm_name
            )

        # Set prompt_cache_key on LLM provider for cache routing
        if session_id and hasattr(self.llm, "prompt_cache_key"):
            self.llm.prompt_cache_key = session_id
            logger.info("Prompt cache key set", cache_key=session_id[:16])

        # Save embedding config to session state for search_memory tool and resume
        if self.session_store:
            memory_cfg = getattr(self.config, "memory", None)
            embed_cfg = (
                memory_cfg.get("embedding") if isinstance(memory_cfg, dict) else None
            )
            if embed_cfg:
                self.session_store.state["embedding_config"] = embed_cfg

        # Get the actual model name from the LLM provider
        model = (
            getattr(self.llm, "model", "")
            or getattr(getattr(self.llm, "config", None), "model", "")
            or "(no model - backend)"
        )
        max_context, compact_at = context_window(self)
        # ``llm_name`` carries the canonical ``provider/name[@variations]``
        # identifier so CLI banners, the web ModelSwitcher pill, and
        # future ``/model`` invocations all show the same selector form.
        # Falls back to whatever the user originally specified if
        # resolution failed (shouldn't happen at this point but we're
        # conservative — the event still fires with best-effort data).
        llm_identifier = self.llm_identifier() or selected_llm_name
        self.output_router.notify_activity(
            "session_info",
            "",
            metadata={
                "session_id": session_id,
                "model": model,
                "llm_name": llm_identifier,
                "agent_name": self.config.name,
                "max_context": max_context,
                "compact_threshold": compact_at,
            },
        )

    def capture_context(self) -> ContextSnapshot:
        """Return an immutable snapshot of the current effective conversation."""
        return capture_agent_context(self)

    def attach_session_store(
        self, store: Any, *, capture_activity: bool = True
    ) -> None:
        """Attach a SessionStore; registers a SessionOutput sink.

        Wires scratchpad + plugin-hook observers so Wave B
        ``scratchpad_write`` / ``plugin_hook_timing`` events flow.
        """
        self.session_store = store
        # Controller needs direct access for artifact writes.
        if hasattr(self, "controller") and self.controller is not None:
            self.controller.session_store = store
        if store is None:
            if self._session_output is not None:
                self.output_router.remove_secondary(self._session_output)
                self._session_output = None
        elif (
            self._session_output is None
            or getattr(self._session_output, "_store", None) is not store
        ):
            if self._session_output is not None:
                self.output_router.remove_secondary(self._session_output)
            self._session_output = SessionOutput(
                self.config.name, store, self, capture_activity=capture_activity
            )
            self.output_router.add_secondary(self._session_output)
        else:
            self._session_output._capture_activity = capture_activity
        if hasattr(self, "subagent_manager"):
            self.subagent_manager._session_store = store
            self.subagent_manager._parent_name = self.config.name
        self.trigger_manager._session_store = store
        self.trigger_manager._agent_name = self.config.name
        if self.compact_manager:
            self.compact_manager._session_store = store
        if store is None:
            return
        sync_pinned_with_store(self, store)
        if self.compact_manager:
            restore_compact_state_from_session(
                self.compact_manager, store, self.config.name
            )
        native_tool_options = getattr(self, "native_tool_options", None)
        if native_tool_options is not None:
            try:
                native_tool_options.apply()
            except Exception as e:
                logger.warning(
                    "native tool option apply skipped",
                    agent=self.config.name,
                    error=str(e),
                    exc_info=True,
                )
        tool_options = getattr(self, "tool_options", None)
        if tool_options is not None:
            try:
                tool_options.apply()
            except Exception as e:
                logger.warning(
                    "tool option apply skipped",
                    agent=self.config.name,
                    error=str(e),
                    exc_info=True,
                )
        plugin_options = getattr(self, "plugin_options", None)
        if plugin_options is not None:
            try:
                plugin_options.apply()
            except Exception as e:
                logger.warning(
                    "plugin options apply skipped",
                    agent=self.config.name,
                    error=str(e),
                    exc_info=True,
                )
        # Wave B: route scratchpad + plugin-hook events to the router.
        wire_scratchpad_observer(self)
        wire_plugin_hook_timing(self)
        logger.debug("Session store attached", agent=self.config.name)

    # Aliases preserve the Agent API while session attachment remains centralized.
    attach_to_session = _attach_to_session
    detach_from_session = _detach_from_session

    def update_system_prompt(self, content: str, replace: bool = False) -> None:
        """Update the system prompt of a running agent.

        Args:
            content: New content to append (or full replacement if replace=True)
            replace: If True, replace entire system prompt. If False, append.
        """
        sys_msg = self.controller.conversation.get_system_message()
        if sys_msg is None:
            return

        if replace:
            sys_msg.content = content
        else:
            if isinstance(sys_msg.content, str):
                sys_msg.content = sys_msg.content + "\n\n" + content

        logger.info("System prompt updated", replace=replace, added_length=len(content))

    def get_system_prompt(self) -> str:
        """Get the current system prompt text."""
        sys_msg = self.controller.conversation.get_system_message()
        if sys_msg and isinstance(sys_msg.content, str):
            return sys_msg.content
        return ""

    def get_state(self) -> dict[str, Any]:
        """Get agent state for monitoring."""
        return {
            "name": self.config.name,
            "running": self._running,
            "tools": self.tools,
            "subagents": self.subagents,
            "message_count": len(self.conversation_history),
            "pending_jobs": self.executor.get_pending_count() if self.executor else 0,
        }

    def session_info(self, tokens_view: str = "own") -> dict[str, Any]:
        """Build a session snapshot with the requested token accounting view."""
        return _build_session_info(self, tokens_view)
