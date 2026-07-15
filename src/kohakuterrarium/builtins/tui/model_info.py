"""Keep the single session-panel model line consistent with the active tab.

Tabbed sessions retain model and context limits per creature, resolve modal
actions against that creature, and ignore sibling updates until their tab is active.
"""

from typing import Any

from kohakuterrarium.builtins.tui.widgets import SessionInfoPanel
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class TabModelRegistryMixin:
    """Store per-tab model state and render only the active creature's details."""

    host_agent: Any
    resolve_tab_agent: Any
    _app: Any
    _terrarium_tabs: list[str] | None
    _model_by_target: dict[str, str]
    _context_by_target: dict[str, tuple[int, int]]

    def agent_for_tab(self, tab: str | None = None) -> Any:
        """Resolve creature tabs to live agents; channels and failures use the host."""
        target = tab if tab is not None else self.get_active_tab()
        if target and not target.startswith("#") and callable(self.resolve_tab_agent):
            try:
                agent = self.resolve_tab_agent(target)
            except Exception:
                agent = None
            if agent is not None:
                return agent
        return self.host_agent

    def update_target_model(
        self,
        target: str,
        model: str,
        max_context: int = 0,
        compact_threshold: int = 0,
    ) -> None:
        """Record a tab's model and refresh the panel when it is visible."""
        if not model:
            return
        if target:
            self._model_by_target[target] = model
            if max_context:
                self._context_by_target[target] = (max_context, compact_threshold)
        active = self.get_active_tab()
        if not self._terrarium_tabs or not target or target == active:
            self._set_model_line(model)
            if max_context:
                self.set_context_limits(max_context, compact_threshold)

    def refresh_model_for_tab(self, tab: str) -> None:
        """Render a tab's cached model or resolve and cache it from the live agent."""
        if not tab or tab.startswith("#"):
            return
        model = self._model_by_target.get(tab, "")
        if not model:
            agent = self.agent_for_tab(tab)
            ident = getattr(agent, "llm_identifier", None)
            if callable(ident):
                try:
                    model = ident() or ""
                except Exception:
                    model = ""
            if model:
                self._model_by_target[tab] = model
        if model:
            self._set_model_line(model)
        limits = self._context_by_target.get(tab)
        if limits:
            self.set_context_limits(*limits)

    def _set_model_line(self, model: str) -> None:
        """Update only the session panel's model line."""
        pending = getattr(self, "_pending_session_info", None)
        if pending:
            self._pending_session_info = (pending[0], model, pending[2])
        if not self._app or not self._app.is_running:
            return

        def _do():
            try:
                self._app.query_one("#session-panel", SessionInfoPanel).set_model(model)
            except Exception as e:
                logger.warning("TUI model-line update failed", error=str(e))

        self._safe_call(_do)


def handle_session_info(tui: Any, output: Any, metadata: dict) -> None:
    """Route session information to standalone or per-tab panel state."""
    session_id = metadata.get("session_id", "")
    # Use the canonical identifier shared with other model displays.
    model = metadata.get("llm_name", "") or metadata.get("model", "")
    agent_name = metadata.get("agent_name", "")
    max_context = metadata.get("max_context", 0)
    compact_threshold = metadata.get("compact_threshold", 0)
    if getattr(tui, "_terrarium_tabs", None):
        # In engine mode, model details are isolated by creature tab.
        target = getattr(output, "_default_target", "") or ""
        tui.update_target_model(target, model, max_context or 0, compact_threshold or 0)
        return
    tui.update_session_info(session_id, model, agent_name)
    if max_context:
        tui.set_context_limits(max_context, compact_threshold)
