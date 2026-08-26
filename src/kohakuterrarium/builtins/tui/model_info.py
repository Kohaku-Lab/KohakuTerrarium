"""Keep the single session-panel model line consistent with the active tab.

Tabbed sessions retain model and context limits per creature, resolve modal
actions against that creature, and ignore sibling updates until their tab is active.
"""

import weakref
from typing import Any

from kohakuterrarium.builtins.tui.widgets import ChatInput, SessionInfoPanel
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class TabModelRegistryMixin:
    """Store per-tab model state and render only the active creature's details."""

    host_agent: Any
    resolve_tab_agent: Any
    _app: Any
    _terrarium_tabs: list[str] | None
    _model_by_target: dict[str, str]
    _identity_by_target: dict[str, tuple[str, str, str, str]]
    _context_by_target: dict[str, tuple[int, int]]
    _command_hint_watches: set[tuple[str, int]]
    command_hint_fallback: dict[str, Any]

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

    def set_command_hints(self, commands: dict, *, target: str = "") -> None:
        """Render one tab's live command registry when that tab is active."""
        if target and target != self.get_active_tab():
            return
        registry = commands or self.command_hint_fallback
        names = list(registry)
        for command in registry.values():
            names.extend(getattr(command, "aliases", None) or [])
        names = sorted(set(names))

        def _apply() -> None:
            inp = self._app.query_one("#input-box", ChatInput)
            inp.command_names = names
            inp.on_text_area_changed()

        self._safe_call(_apply)

    def refresh_command_hints_for_tab(self, tab: str = "") -> None:
        """Read command hints from the currently selected tab's live agent."""
        target = tab or self.get_active_tab()
        agent = self.agent_for_tab(target)
        lister = getattr(agent, "list_user_commands", None)
        commands = lister() if callable(lister) else {}
        self.set_command_hints(commands, target=target)

    def watch_command_agent(self, target: str, agent: Any) -> None:
        """Subscribe a creature tab to runtime command-registry changes."""
        key = (target, id(agent))
        if key in self._command_hint_watches:
            return
        self._command_hint_watches.add(key)
        add_listener = getattr(agent, "add_user_command_listener", None)
        if callable(add_listener):
            session_ref = weakref.ref(self)

            def _on_commands_changed(commands: dict) -> None:
                session = session_ref()
                if session is not None:
                    session.set_command_hints(commands, target=target)

            add_listener(_on_commands_changed)

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

    def update_target_identity(
        self,
        target: str,
        session_id: str,
        agent_name: str,
        config_name: str,
        config_ref: str,
    ) -> None:
        """Record the runtime and config identities for one creature tab."""
        if target:
            previous = self._identity_by_target.get(target, ("", "", "", ""))
            self._identity_by_target[target] = (
                session_id or previous[0],
                agent_name or previous[1],
                config_name or previous[2],
                config_ref or previous[3],
            )

    def refresh_model_for_tab(self, tab: str) -> None:
        """Render a tab's cached model, limits, and configuration identity."""
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
        identity = self._identity_by_target.get(tab)
        if identity:
            session_id, agent_name, config_name, config_ref = identity
            self.update_session_info(
                session_id, model, agent_name, config_name, config_ref
            )
        elif model:
            self._set_model_line(model)
        limits = self._context_by_target.get(tab)
        if limits:
            self.set_context_limits(*limits)

    def _set_model_line(self, model: str) -> None:
        """Update only the session panel's model line."""
        pending = getattr(self, "_pending_session_info", None)
        if pending:
            self._pending_session_info = (pending[0], model, *pending[2:])
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
    config_name = metadata.get("config_name", "")
    config_ref = metadata.get("config_ref", "")
    max_context = metadata.get("max_context", 0)
    compact_threshold = metadata.get("compact_threshold", 0)
    if getattr(tui, "_terrarium_tabs", None):
        # In engine mode, model details are isolated by creature tab.
        target = getattr(output, "_default_target", "") or ""
        tui.update_target_model(target, model, max_context or 0, compact_threshold or 0)
        tui.update_target_identity(
            target, session_id, agent_name, config_name, config_ref
        )
        if target == tui.get_active_tab():
            identity = tui._identity_by_target.get(target, ("", "", "", ""))
            tui.update_session_info(
                identity[0],
                model or tui._model_by_target.get(target, ""),
                *identity[1:],
            )
        return
    tui.update_session_info(session_id, model, agent_name, config_name, config_ref)
    if max_context:
        tui.set_context_limits(max_context, compact_threshold)
