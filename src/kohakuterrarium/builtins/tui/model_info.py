"""Per-tab (per-creature) model info for the TUI.

The session panel has ONE ``Model:`` line but a multi-creature graph
has one model per creature. This module owns the registry that keys
model info by tab/target and the routing that keeps the visible line
bound to the ACTIVE tab:

- :class:`TabModelRegistryMixin` — mixed into
  :class:`~kohakuterrarium.builtins.tui.session.TUISession`. Stores
  per-target model + context limits, resolves tabs to live agents
  (``agent_for_tab``), and re-renders the line on tab switches. Like
  ``core/agent_model.py``'s mixin it has no state of its own — the
  backing dicts and hooks are initialised by ``TUISession.__init__``.
- :func:`handle_session_info` — the body of TUIOutput's
  ``session_info`` handler (same extraction pattern as
  ``_injection.py``). Routes per-creature events through the registry
  in tabbed (engine) mode; falls back to the legacy whole-panel
  update in standalone mode.
"""

from typing import Any

from kohakuterrarium.builtins.tui.widgets import SessionInfoPanel
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class TabModelRegistryMixin:
    """Per-tab model registry + active-tab model-line rendering.

    Declared for static checkers; populated by ``TUISession.__init__``.
    """

    host_agent: Any
    resolve_tab_agent: Any
    _app: Any
    _terrarium_tabs: list[str] | None
    _model_by_target: dict[str, str]
    _context_by_target: dict[str, tuple[int, int]]

    def agent_for_tab(self, tab: str | None = None) -> Any:
        """Resolve ``tab`` (default: the active tab) to its live agent.

        Falls back to ``host_agent`` when no resolver is installed,
        the tab is a channel tab, or resolution fails — so standalone
        TUI behaviour is unchanged.
        """
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
        """Record ``target``'s model, refreshing the panel only when
        the target IS the visible tab (or in single-area mode). This
        is the seam that stops creature B's model switch from stomping
        the ``Model:`` line while the user is looking at creature A.
        """
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
        """Re-render the model line for a newly activated tab.

        Reads the per-tab registry first; on a miss, resolves the
        tab's live agent and asks it directly (also caching the
        answer). Channel tabs keep whatever model line is showing.
        """
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
        """Update ONLY the panel's ``Model:`` line (partial update)."""
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
    """Route a ``session_info`` activity to the TUI session panel."""
    session_id = metadata.get("session_id", "")
    # Prefer the canonical ``provider/name[@variations]`` identifier
    # so the panel matches the web pill and ``/model`` output.
    model = metadata.get("llm_name", "") or metadata.get("model", "")
    agent_name = metadata.get("agent_name", "")
    max_context = metadata.get("max_context", 0)
    compact_threshold = metadata.get("compact_threshold", 0)
    if getattr(tui, "_terrarium_tabs", None):
        # Tabbed (engine) mode: models are per-creature. Route via
        # the per-target registry so a sibling's model switch never
        # stomps the visible tab's line; identity lines (session
        # id / agent name) stay engine-owned in this mode.
        target = getattr(output, "_default_target", "") or ""
        tui.update_target_model(target, model, max_context or 0, compact_threshold or 0)
        return
    tui.update_session_info(session_id, model, agent_name)
    if max_context:
        tui.set_context_limits(max_context, compact_threshold)
