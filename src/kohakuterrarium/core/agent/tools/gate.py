"""Agent side of renderer gating: refresh the prompt when what it shows changes.

``watch_renderers`` subscribes to the output router; a renderer change that
alters the visible tool set, or a renderer a plugin's prompt section depends
on (``BasePlugin.prompt_renderers``), rebuilds the system prompt at once when
idle, or at the next LLM request while a turn is running.
"""

from typing import Any

from kohakuterrarium.core.tools.gate import (
    visible_catalog,
    visible_subagent_names,
    visible_tool_names,
)
from kohakuterrarium.modules.plugin.base import ToolVisibility


class AgentToolGateMixin:
    """Renderer-driven prompt refresh for ``Agent``."""

    registry: Any

    def _visible_catalog(self) -> ToolVisibility | None:
        """The model-visible tool catalog for the current plugins and renderers."""
        return visible_catalog(
            self.registry,
            getattr(self, "plugins", None),
            getattr(self, "output_router", None),
        )

    def _visible_tool_key(
        self,
    ) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
        """Visible tools, sub-agents and prompt-relevant renderers: what the prompt shows."""
        visibility = self._visible_catalog()
        return (
            frozenset(visible_tool_names(self.registry, visibility)),
            frozenset(visible_subagent_names(self.registry, visibility)),
            self._prompt_renderer_key(),
        )

    def _prompt_renderer_key(self) -> frozenset[str]:
        """Attached renderers that some active plugin's prompt section depends on."""
        plugins = getattr(self, "plugins", None)
        wanted = plugins.prompt_renderers() if plugins is not None else frozenset()
        if not wanted:
            return frozenset()
        router = getattr(self, "output_router", None)
        renderers = getattr(router, "ui_renderers", None)
        present = renderers() if callable(renderers) else {}
        return frozenset(r for r in present if r in wanted)

    def watch_renderers(self) -> None:
        """Subscribe to renderer changes on the output router, when it reports them."""
        self._prompt_tool_key = self._visible_tool_key()
        router = getattr(self, "output_router", None)
        subscribe = getattr(router, "add_renderers_listener", None)
        if callable(subscribe):
            subscribe(self._on_renderers_changed)

    def _on_renderers_changed(self, old: dict, new: dict) -> None:
        """Mark the prompt stale when the visible catalog changed; apply it if idle."""
        if self._visible_tool_key() == getattr(self, "_prompt_tool_key", None):
            self._prompt_refresh_pending = False
            return
        self._prompt_refresh_pending = True
        if not getattr(self, "is_processing", False):
            self.apply_pending_prompt_refresh()

    def apply_pending_prompt_refresh(self) -> bool:
        """Rebuild the system prompt if a renderer change left it stale."""
        if not getattr(self, "_prompt_refresh_pending", False):
            return False
        self._prompt_refresh_pending = False
        self.refresh_system_prompt()
        return True
